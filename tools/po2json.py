#!/usr/bin/env python3
"""Compile locales/*/LC_MESSAGES/messages.po to messages.json.

JSON schema: flat ``{msgid: msgstr}`` for plain strings; plural entries are
stored as form arrays under the *singular* msgid::

    {"%(count)s reply": ["%(count)s відповідь", "%(count)s відповіді", ...]}

plus one metadata entry per catalog::

    {"__plural": {"nplurals": 3, "order": ["one", "few", "many"]}}

``order[i]`` is the CLDR category for ``msgstr[i]`` (derived at build time by
evaluating the PO ``Plural-Forms`` header against Babel tags), so both the
Python server (``i18n.ngettext_msg``) and the JS helper (``I18n.n`` via
``Intl.PluralRules``) resolve the right form for any n. Empty msgstr falls
back to msgid (English source) at lookup time.

Usage: python tools/po2json.py [--check]
  --check: exit non-zero if any .json is stale relative to its .po.
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"

# A msgid can never be this (guarded at build); keeps metadata in-band with
# the messages so window.I18N needs no second artifact.
PLURAL_META_KEY = "__plural"


def parse_po_entries(path: Path):
    """Split a PO file into (header_lines, [entry_blocks]) preserving order.

    Obsolete (``#~``) blocks are returned separately so callers can ignore or
    preserve them; only live entries reach the compiled catalog.
    """
    text = path.read_text(encoding="utf-8")
    # Strip each block: a file ending in a single "\n" would otherwise leave a
    # trailing newline glued to the last block, which verbatim passthrough
    # (obsolete entries) turns into an oscillating blank line.
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    header = blocks[0] if blocks else ""
    live, obsolete = [], []
    for b in blocks[1:]:
        (obsolete if b.lstrip().startswith("#~") else live).append(b)
    return header, live, obsolete


def parse_entry(block: str, path: str | Path = "<string>"):  # noqa: C901
    """Parse one PO entry block -> dict with msgid/plural/forms/comments.

    Also captures the raw ``msgid``/``msgid_plural`` source lines verbatim so
    tools/pomerge.py can re-emit them byte-for-byte (rewrapping long msgids
    risks eating inter-chunk spaces and corrupting keys).
    """
    msgid: str | None = None
    msgid_plural: str | None = None
    msgid_lines: list[str] = []
    msgid_plural_lines: list[str] = []
    msgstr: str | None = None
    msgstr_plural: dict[int, str] = {}
    comments: list[str] = []
    in_msgid = in_msgstr = in_plural = in_plural_id = False
    plural_idx = -1

    def unquote(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
            try:
                return ast.literal_eval(s)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"malformed quoted string in {path}: {s!r}") from exc
        return s

    for raw in block.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            comments.append(line)
            in_msgid = in_msgstr = False
            in_plural = in_plural_id = False
            continue
        if line.startswith("msgid_plural"):
            msgid_plural_lines.append(line)
            msgid_plural = unquote(line[len("msgid_plural") :].strip())
            in_msgid = in_msgstr = False
            in_plural = False
            in_plural_id = True
            continue
        if line.startswith("msgid"):
            msgid_lines.append(line)
            msgid = unquote(line[len("msgid") :].strip())
            in_msgid = True
            in_msgstr = False
            in_plural = in_plural_id = False
            continue
        if line.startswith("msgstr["):
            idx = int(line[len("msgstr[") :].split("]")[0])
            msgstr_plural[idx] = unquote(line.split("]", 1)[1].strip())
            in_msgid = in_msgstr = False
            in_plural_id = False
            in_plural = True
            plural_idx = idx
            continue
        if line.startswith("msgstr"):
            msgstr = unquote(line[len("msgstr") :].strip())
            in_msgid = False
            in_msgstr = True
            in_plural = in_plural_id = False
            continue
        if line.startswith('"'):
            text = unquote(line)
            if in_msgid and isinstance(msgid, str):
                msgid_lines.append(line)
                msgid += text
            elif in_plural_id and isinstance(msgid_plural, str):
                msgid_plural_lines.append(line)
                msgid_plural += text
            elif in_msgstr and isinstance(msgstr, str):
                msgstr += text
            elif in_plural and plural_idx >= 0:
                msgstr_plural[plural_idx] = msgstr_plural.get(plural_idx, "") + text
    return {
        "msgid": msgid,
        "msgid_lines": msgid_lines,
        "msgid_plural": msgid_plural,
        "msgid_plural_lines": msgid_plural_lines,
        "msgstr": msgstr,
        "msgstr_plural": msgstr_plural,
        "comments": comments,
    }


def parse_po(path: Path) -> dict[str, str]:
    """Legacy flat view (singular->form0) used by --check comparisons only."""
    catalog = build_catalog(path)[0]
    flat: dict[str, str] = {}
    for key, val in catalog.items():
        if key == PLURAL_META_KEY:
            continue
        flat[key] = val[0] if isinstance(val, list) else val
    return flat


def parse_header_forms(header: str) -> tuple[int, str]:
    """Extract (nplurals, plural_expr) from a PO header block."""
    m = re.search(r"Plural-Forms:\s*nplurals\s*=\s*(\d+)\s*;\s*plural\s*=\s*(.*?)\s*;", header, re.S)
    if not m:
        return 2, "(n != 1)"
    return int(m.group(1)), m.group(2).replace("\\n", "").strip()


# --- Gettext Plural-Forms C-expression evaluator (build time only) ---
#
# The header expression (e.g. ``(n%10==1 && n%100!=11 ? 0 : ...)``) maps n to
# a msgstr index. Grammar: n, integers, %, ==, !=, <, <=, >, >=, &&, ||, !,
# ?:, parentheses. Runtime code never evaluates this; po2json uses it once to
# derive the CLDR category order, which Intl.PluralRules/Babel consume.


def _tokenize(expr: str):  # noqa: C901
    toks: list[tuple[str, object]] = []
    i = 0
    while i < len(expr):
        c = expr[i]
        if c.isspace():
            i += 1
        elif c.isdigit():
            j = i
            while j < len(expr) and expr[j].isdigit():
                j += 1
            toks.append(("num", int(expr[i:j])))
            i = j
        elif c == "n" and (i + 1 >= len(expr) or not expr[i + 1].isalnum() and expr[i + 1] != "_"):
            toks.append(("n", None))
            i += 1
        elif expr.startswith("&&", i):
            toks.append(("&&", None))
            i += 2
        elif expr.startswith("||", i):
            toks.append(("||", None))
            i += 2
        elif expr.startswith("==", i):
            toks.append(("==", None))
            i += 2
        elif expr.startswith("!=", i):
            toks.append(("!=", None))
            i += 2
        elif expr.startswith("<=", i):
            toks.append(("<=", None))
            i += 2
        elif expr.startswith(">=", i):
            toks.append((">=", None))
            i += 2
        elif c in "<>+-*/%!?:()":
            toks.append((c, None))
            i += 1
        else:
            raise ValueError(f"bad char {c!r} in plural expr {expr!r}")
    return toks


def eval_plural_expr(expr: str, n: int) -> int:  # noqa: C901
    """Evaluate a Gettext Plural-Forms expression for n (n >= 0)."""
    toks = _tokenize(expr)
    pos = 0

    def peek():
        return toks[pos][0] if pos < len(toks) else None

    def next_tok():
        nonlocal pos
        t = toks[pos]
        pos += 1
        return t

    def truth(v):
        return 1 if v else 0

    def parse_ternary():
        cond = parse_or()
        if peek() == "?":
            next_tok()
            yes = parse_ternary()
            if peek() != ":":
                raise ValueError(f"missing ':' in plural expr {expr!r}")
            next_tok()
            no = parse_ternary()
            return yes if cond else no
        return cond

    def parse_or():
        v = parse_and()
        while peek() == "||":
            next_tok()
            rhs = parse_and()
            v = truth(v or rhs)
        return v

    def parse_and():
        v = parse_eq()
        while peek() == "&&":
            next_tok()
            rhs = parse_eq()
            v = truth(v and rhs)
        return v

    def parse_eq():
        v = parse_rel()
        while peek() in ("==", "!="):
            op = next_tok()[0]
            rhs = parse_rel()
            v = truth((v == rhs) if op == "==" else (v != rhs))
        return v

    def parse_rel():
        v = parse_add()
        while peek() in ("<", ">", "<=", ">="):
            op = next_tok()[0]
            rhs = parse_add()
            v = truth({"<": v < rhs, ">": v > rhs, "<=": v <= rhs, ">=": v >= rhs}[op])
        return v

    def parse_add():
        v = parse_mul()
        while peek() in ("+", "-"):
            op = next_tok()[0]
            rhs = parse_mul()
            v = v + rhs if op == "+" else v - rhs
        return v

    def parse_mul():
        v = parse_unary()
        while peek() in ("*", "/", "%"):
            op = next_tok()[0]
            rhs = parse_unary()
            if op == "*":
                v = v * rhs
            elif op == "/":
                v = abs(v) // abs(rhs) if rhs else 0
            else:
                v = v % rhs if rhs else 0
        return v

    def parse_unary():
        if peek() == "!":
            next_tok()
            return truth(not parse_unary())
        return parse_primary()

    def parse_primary():
        t = peek()
        if t == "(":
            next_tok()
            v = parse_ternary()
            if peek() != ")":
                raise ValueError(f"missing ')' in plural expr {expr!r}")
            next_tok()
            return v
        kind, val = next_tok()
        if kind == "num":
            return val
        if kind == "n":
            return n
        raise ValueError(f"unexpected {kind!r} in plural expr {expr!r}")

    result = parse_ternary()
    if peek() is not None:
        raise ValueError(f"trailing tokens in plural expr {expr!r}")
    return int(result)


def plural_category_order(locale_tag: str, nplurals: int, expr: str) -> list[str]:  # noqa: C901
    """CLDR category per msgstr index, e.g. uk -> ["one", "few", "many"].

    Probes the header expression for a representative n per index and asks
    Babel for that n's CLDR tag, so the order is exact by construction even
    for headers that disagree with stock CLDR data.
    """
    try:
        from babel import Locale

        loc = Locale.parse(locale_tag.replace("-", "_"))
    except Exception:
        loc = None
    order: list[str] = []
    for i in range(nplurals):
        tag = "other"
        for n in range(0, 10000):
            try:
                hit = eval_plural_expr(expr, n) == i
            except ValueError:
                hit = n == (1 if i == 0 else 0)
                if i > 1:
                    hit = False
            if hit:
                if loc is not None:
                    try:
                        tag = loc.plural_form(n)
                    except Exception:
                        pass
                break
        order.append(tag)
    if nplurals == 1:
        return ["other"]
    if not order or order[0] != "one":
        # Fallback keeps index 0 for n==1 (English-style two-form guess).
        order = (["one", "other"] + ["many"] * max(0, nplurals - 2))[:nplurals]
    return order


def build_catalog(po_path: Path) -> tuple[dict, dict]:
    """Build the JSON catalog + header info for one .po file."""
    header, live, _obsolete = parse_po_entries(po_path)
    nplurals, expr = parse_header_forms(header)
    locale_tag = po_path.parent.parent.name.replace("_", "-")
    messages: dict = {}
    for block in live:
        e = parse_entry(block, po_path)
        msgid = e["msgid"]
        if not msgid:
            continue
        if msgid == PLURAL_META_KEY:
            raise ValueError(f"{po_path}: msgid collides with metadata key {PLURAL_META_KEY!r}")
        if e["msgid_plural"] is not None:
            if e["msgstr_plural"] and max(e["msgstr_plural"]) >= nplurals:
                # A contributor translated more forms than the header declares;
                # refusing to silently drop them (fix the Plural-Forms header).
                print(
                    f"WARNING {po_path}: {msgid!r} has msgstr[{max(e['msgstr_plural'])}]"
                    f" but header says nplurals={nplurals}"
                )
            forms = [e["msgstr_plural"].get(i, "") for i in range(nplurals)]
            # NOTE: no synthetic second key for the plural msgid; plural forms
            # live only under the singular key (consumed via I18n.n/ngettext).
            # Fully-untranslated entries are skipped like plain ones.
            if any(forms):
                messages[msgid] = forms
        elif e["msgstr"]:
            messages[msgid] = e["msgstr"]
    messages[PLURAL_META_KEY] = {
        "nplurals": nplurals,
        "order": plural_category_order(locale_tag, nplurals, expr),
    }
    return messages, {"nplurals": nplurals, "expr": expr}


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for po in sorted(LOCALES.glob("*/LC_MESSAGES/messages.po")):
        msgs, _info = build_catalog(po)
        json_path = po.with_suffix(".json")
        if check:
            if not json_path.exists():
                stale.append(str(po))
                continue
            with open(json_path, encoding="utf-8") as f:
                compiled = json.load(f)
            if compiled != msgs:
                stale.append(str(po))
            continue
        # JSON catalog sorted for stable diffs; header excluded by parser.
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(msgs, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print(f"compiled {po.relative_to(ROOT)} -> {len(msgs) - 1} entries")
    if check and stale:
        print("stale catalogs:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
