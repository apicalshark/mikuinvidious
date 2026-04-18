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


def danmaku_xml_conv(domtree):
    results = []
    for d in domtree.getElementsByTagName("d"):
        res = danmaku_elem_conv(d)
        if res:
            results.append(res)
    return results


def danmaku_elem_conv(d):
    p = d.getAttribute("p").split(",")

    try:
        # Modes: 1:RTL, 4:Bottom, 5:Top, 6:LTR.
        # Mode 7 & 8 are advanced/special danmaku, which we don't fully support yet.
        m = ({"6": "ltr", "1": "rtl", "5": "top", "4": "bottom", "7": "rtl", "8": "rtl"})[p[1]]
    except (KeyError, IndexError):
        return {}

    if not d.firstChild or not d.firstChild.data:
        return {}

    try:
        ftsize = int(p[2]) or 25
        ftcolor = hex(int(p[3]))[2:].zfill(6)  # Ensure 6 digits
    except (ValueError, IndexError):
        ftsize = 25
        ftcolor = "ffffff"

    return {
        "text": d.firstChild.data,
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
