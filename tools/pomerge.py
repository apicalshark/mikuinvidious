#!/usr/bin/env python3
"""Sync locales/*/LC_MESSAGES/messages.po against locales/messages.pot.

msgmerge semantics, minus the gettext dependency:

- entries whose msgid vanished from the template are marked obsolete
  (``#~`` prefix, translator work preserved, ignored by tools/po2json.py);
- new template msgids are appended with empty msgstr (sized to the locale's
  own ``nplurals`` from its header);
- ``#:`` references and flag comments of surviving entries refresh from the
  template; translations (msgstr) are never touched;
- the locale header (Language, Plural-Forms) is preserved; only
  ``POT-Creation-Date`` refreshes.

Run automatically via ``npm run build:i18n`` after ``pybabel extract`` and
before ``tools/po2json.py``. Safe to run repeatedly (idempotent). ::

    python tools/pomerge.py [--check]
      --check: exit non-zero if any .po would change (CI freshness gate).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from po2json import parse_entry, parse_header_forms, parse_po_entries

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"
POT = LOCALES / "messages.pot"

# Source-language catalogs need no entries (English msgids are the fallback);
# keep them as bare headers so the merge never appends empty translations.
SOURCE_LOCALES = {"en"}


def po_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_block(
    comments: list[str],
    msgid_lines: list[str],
    plural_lines: list[str],
    has_plural: bool,
    forms: list[str],
    obsolete: bool = False,
):
    """Render one entry block from verbatim msgid lines (never rewrapped).

    Rewrapping long msgids risks eating inter-chunk spaces and corrupting
    keys, so source lines pass through byte-for-byte; only comments and
    msgstr change. Obsolete blocks get the ``#~`` prefix.
    """
    lines = list(comments) + list(msgid_lines) + list(plural_lines)
    if has_plural:
        lines.extend(f"msgstr[{i}] {po_quote(f)}" for i, f in enumerate(forms))
    else:
        lines.append(f"msgstr {po_quote(forms[0] if forms else '')}")
    if obsolete:
        lines = [("#~ " + ln) if not ln.startswith("#~") else ln for ln in lines]
    return "\n".join(lines)


def split_ref_flags(comments: list[str]):
    """Split comments into (references+flags to refresh, other to preserve)."""
    refresh = [c for c in comments if c.startswith("#:") or c.startswith("#,")]
    keep = [c for c in comments if not (c.startswith("#:") or c.startswith("#,"))]
    return refresh, keep


def merge_po(po_path: Path, pot_entries: list[dict], pot_date: str) -> bool:  # noqa: C901
    """Merge one .po against the template. Returns True if the file changed.

    Freshness is decided by the final text comparison; the intermediate
    ``changed`` flags below are documentation-only.
    """
    header, live, obsolete = parse_po_entries(po_path)
    nplurals, _expr = parse_header_forms(header)
    nplurals = max(1, nplurals)

    live_by_id = {}
    for b in live:
        e = parse_entry(b, po_path)
        if e["msgid"]:
            live_by_id[e["msgid"]] = e

    pot_by_id = {e["msgid"]: e for e in pot_entries if e["msgid"]}
    out_blocks = [header]
    newly_obsolete: list[str] = []

    # Surviving entries keep file order; stale ones go obsolete at the end
    # (with previously-obsoleted blocks) so the layout settles in one run.
    for e in live_by_id.values():
        pot_e = pot_by_id.get(e["msgid"])
        if pot_e is None:
            newly_obsolete.append(render_entry(e, _forms_of(e), obsolete=True))
            continue
        pot_refs = [c for c in pot_e["comments"] if c.startswith("#:") or c.startswith("#,")]
        _old_refs, keep = split_ref_flags(e["comments"])
        # Template msgstr is always empty: translations are never adopted.
        forms = _forms_of(e)
        # Pad missing plural slots (e.g. locale gained forms) without touching translations.
        want = nplurals if e["msgid_plural"] is not None else 1
        if len(forms) < want:
            forms = forms + [""] * (want - len(forms))
        out_blocks.append(render_entry(e, forms, comments=keep + pot_refs))

    # New template entries append in template order.
    for e in pot_entries:
        if not e["msgid"] or e["msgid"] in live_by_id:
            continue
        # New entries are untranslated: empty msgstr sized to this locale.
        want = nplurals if e["msgid_plural"] is not None else 1
        refs = [c for c in e["comments"] if c.startswith("#:") or c.startswith("#,")]
        out_blocks.append(render_entry(e, [""] * want, comments=refs))

    # Preserve previously-obsoleted blocks verbatim at the end.
    out_blocks.extend(obsolete + newly_obsolete)

    # Refresh POT-Creation-Date so translators see the template vintage.
    # NOTE: replacement must be a function — a plain string would let re
    # interpret pot_date's literal "\n" sequences as real newlines.
    if pot_date and "POT-Creation-Date:" in out_blocks[0]:
        new_head, n = re.subn(r"POT-Creation-Date: .*?\\n", lambda _m: pot_date, out_blocks[0], count=1)
        if n:
            out_blocks[0] = new_head

    new_text = "\n\n".join(out_blocks) + "\n"
    old_text = po_path.read_text(encoding="utf-8")
    return (new_text != old_text, new_text)


def render_entry(e: dict, forms: list[str], comments: list[str] | None = None, obsolete: bool = False) -> str:
    """Render a parsed entry with replacement comments/forms (msgid verbatim)."""
    return render_block(
        e["comments"] if comments is None else comments,
        e["msgid_lines"],
        e["msgid_plural_lines"],
        e["msgid_plural"] is not None,
        forms,
        obsolete,
    )


def _forms_of(e: dict) -> list[str]:
    """Existing translated forms, preserved verbatim (never trimmed)."""
    if e["msgid_plural"] is not None:
        top = max(e["msgstr_plural"].keys(), default=-1) + 1
        return [e["msgstr_plural"].get(i, "") for i in range(max(top, 1))]
    return [e["msgstr"] or ""]


def main() -> int:
    check = "--check" in sys.argv
    _pot_header, pot_blocks, _pot_obsolete = parse_po_entries(POT)
    pot_entries = []
    for b in pot_blocks:
        e = parse_entry(b, POT)
        if e["msgid"]:
            pot_entries.append(e)
    pot_text = POT.read_text(encoding="utf-8")
    m = re.search(r'"POT-Creation-Date: .*?\\n"', pot_text)
    pot_date = m.group(0).strip('"') if m else ""
    dirty = []
    for po in sorted(LOCALES.glob("*/LC_MESSAGES/messages.po")):
        if po.parent.parent.name in SOURCE_LOCALES:
            continue
        changed, new_text = merge_po(po, pot_entries, pot_date)
        if not changed:
            continue
        dirty.append(str(po.relative_to(ROOT)))
        if not check:
            po.write_text(new_text, encoding="utf-8")
            print(f"merged {po.relative_to(ROOT)}")
    if check and dirty:
        print("stale .po files (run npm run build:i18n):", ", ".join(dirty))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
