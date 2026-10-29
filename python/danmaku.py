# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.

"""Server-Side danmaku translation"""

import html
import json
import re

# Fallback extractor for payloads the strict XML parser rejects (upstream
# occasionally returns malformed/truncated XML — historically failing at the
# same position across many videos). Matches the same <d p="...">text</d>
# shape the strict path handles; entities are unescaped and stray inner tags
# stripped so one bad node can't kill the whole file.
_DANMAKU_RE = re.compile(r'<d\s+p="([^"]*)">(.*?)</d>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

try:
    from miku_core_rs import parse_danmaku_xml
except ImportError:
    parse_danmaku_xml = None


def parse_danmaku(xml_text: str | None) -> list[dict]:
    """Parse danmaku XML into list of dictionaries.

    Uses high-performance Rust parser (with GIL detachment) when available;
    gracefully falls back to pure Python regex extraction on failure or when
    the native extension is absent.
    """
    if not xml_text or not isinstance(xml_text, str):
        return []

    if parse_danmaku_xml is not None:
        try:
            return parse_danmaku_xml(xml_text)
        except Exception:
            pass

    return danmaku_xml_fallback(xml_text)


def _danmaku_entry(p_str, text):
    p = (p_str or "").split(",")

    try:
        # Modes: 1:RTL, 4:Bottom, 5:Top, 6:LTR.
        # Mode 7 & 8 are advanced/special danmaku, which we don't fully support yet.
        m = ({"6": "ltr", "1": "rtl", "5": "top", "4": "bottom", "7": "rtl", "8": "rtl"})[p[1]]
    except KeyError, IndexError:
        return {}

    if not text:
        return {}

    if len(p) > 1 and p[1] == "7" and text.startswith("["):
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                if len(arr) >= 5 and isinstance(arr[4], str):
                    text = arr[4]
                elif len(arr) > 0 and isinstance(arr[0], list) and len(arr[0]) >= 5 and isinstance(arr[0][4], str):
                    text = arr[0][4]
        except Exception:
            pass

    if not text:
        return {}

    try:
        ftsize = int(p[2]) or 25
        ftcolor = hex(int(p[3]))[2:].zfill(6)  # Ensure 6 digits
    except ValueError, IndexError:
        ftsize = 25
        ftcolor = "ffffff"

    return {
        "text": text,
        "mode": m,
        "time": float(p[0]) if len(p) > 0 else 0.0,
        "style": {
            "fontSize": f"{ftsize}px",
            "color": f"#{ftcolor}",
            "textShadow": "-1px -1px #fff, -1px 1px #fff, 1px -1px #fff, 1px 1px #fff"
            if ftcolor == "000000"
            else "-1px -1px #000, -1px 1px #000, 1px -1px #000, 1px 1px #000",
            "font": f"{ftsize}px sans-serif",
            "whiteSpace": "pre",
            "fillStyle": f"#{ftcolor}",
            "strokeStyle": "#fff" if ftcolor == "000000" else "#000",
            "lineWidth": 2.0,
        },
    }


def danmaku_xml_fallback(xml_text):
    """Best-effort danmaku extraction when strict XML parsing fails."""
    results = []
    if not xml_text:
        return results
    for match in _DANMAKU_RE.finditer(xml_text):
        text = html.unescape(_TAG_RE.sub("", match.group(2))).strip()
        res = _danmaku_entry(match.group(1), text)
        if res:
            results.append(res)
    return results


def danmaku_xml_conv(domtree):
    results = []
    for d in domtree.getElementsByTagName("d"):
        res = danmaku_elem_conv(d)
        if res:
            results.append(res)
    return results


def danmaku_elem_conv(d):
    text = d.firstChild.data if d.firstChild and d.firstChild.data else None
    return _danmaku_entry(d.getAttribute("p"), text)
