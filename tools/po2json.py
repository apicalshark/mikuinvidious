#!/usr/bin/env python3
"""Compile locales/*/LC_MESSAGES/messages.po to messages.json.

- .json flat {msgid: msgstr} catalog, used by Python templates and as window.I18N in JS.
  Empty msgstr falls back to msgid (English source).

Usage: python tools/po2json.py [--check]
  --check: exit non-zero if any .json is stale relative to its .po.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"


def parse_po(path: Path) -> dict[str, str]:  # noqa: C901
    messages: dict[str, str] = {}
    msgid: str | None = None
    msgstr: str | None = None
    msgid_plural: str | None = None
    msgstr_plural: dict[int, str] = {}
    in_msgid = False
    in_msgstr = False
    in_plural = False
    plural_idx = -1

    def flush():
        nonlocal msgid, msgstr, msgid_plural, msgstr_plural
        if msgid:
            if msgid_plural is not None:
                # Store singular->singular translation; plural forms are resolved
                # server-side via gettext, JS uses simple singular fallback.
                base = msgstr_plural.get(0) or msgstr or msgid
                messages[msgid] = base
                plural_key = msgid_plural
                if plural_key and plural_key not in messages:
                    messages[plural_key] = msgstr_plural.get(1) or plural_key
            elif msgstr:
                messages[msgid] = msgstr
        msgid = msgstr = msgid_plural = None
        msgstr_plural = {}

    def unquote(s: str) -> str:
        s = s.strip()
        if s.startswith('"') and s.endswith('"'):
            return ast.literal_eval(s)
        return s

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("msgid_plural"):
                flush_part = line[len("msgid_plural") :].strip()
                msgid_plural = unquote(flush_part)
                in_msgid = in_msgstr = False
                in_plural = False
                continue
            if line.startswith("msgid"):
                if msgid is not None:
                    flush()
                msgid = unquote(line[len("msgid") :].strip())
                in_msgid = True
                in_msgstr = False
                in_plural = False
                continue
            if line.startswith("msgstr["):
                idx = int(line[len("msgstr[") :].split("]")[0])
                msgstr_plural[idx] = unquote(line.split("]", 1)[1].strip())
                in_msgid = False
                in_msgstr = False
                in_plural = True
                plural_idx = idx
                continue
            if line.startswith("msgstr"):
                msgstr = unquote(line[len("msgstr") :].strip())
                in_msgid = False
                in_msgstr = True
                in_plural = False
                continue
            if line.startswith('"'):
                text = unquote(line)
                if in_msgid and isinstance(msgid, str):
                    msgid += text
                elif in_msgstr and isinstance(msgstr, str):
                    msgstr += text
                elif in_plural and plural_idx >= 0:
                    msgstr_plural[plural_idx] = msgstr_plural.get(plural_idx, "") + text
        if msgid is not None:
            flush()
    messages.pop("(header)", None)
    return messages




def main() -> int:
    check = "--check" in sys.argv
    stale = []
    import json

    for po in sorted((LOCALES).glob("*/LC_MESSAGES/messages.po")):
        msgs = parse_po(po)
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
        print(f"compiled {po.relative_to(ROOT)} -> {len(msgs)} entries")
    if check and stale:
        print("stale catalogs:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
