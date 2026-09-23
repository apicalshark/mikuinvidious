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

from xml.dom import minidom

from api import video
from danmaku import danmaku_xml_conv
from quart import Response, jsonify
from rate_limit import RATE_LIMITS, rate_limit
from shared import app, appcred, appredis


@app.route("/res/danmaku/<vid>")
@app.route("/res/danmaku/<vid>:<idx>")
async def danmaku_res(vid, idx=0):
    # Check if this is a live room ID (all digits)
    if vid.isdigit():
        return jsonify([])

    try:
        v = video.Video(bvid=vid, credential=appcred)
        xml = await v.get_danmaku_xml(int(idx))
        return jsonify(danmaku_xml_conv(minidom.parseString(xml)))
    except Exception as e:
        print(f"Danmaku error for {vid}:{idx}: {e}")
        return jsonify([])


def bcc_to_vtt(bcc: dict) -> str:
    """Convert Bilibili BCC subtitle JSON to WebVTT (PipePipe ``bcc2srt``).

    BCC ``body[]`` holds ``{from, to, content}`` in float seconds; VTT uses
    the same cue layout with ``.`` millisecond separators plus a header.
    """
    lines = ["WEBVTT", ""]
    body = (bcc or {}).get("body") or []
    for i, cue in enumerate(body):
        try:
            start = float(cue.get("from", 0))
            end = float(cue.get("to", 0))
            text = (cue.get("content") or "").replace("\r", "")
        except (TypeError, ValueError, AttributeError):
            continue
        lines.append(str(i + 1))
        lines.append(f"{_vtt_ts(start)} --> {_vtt_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _vtt_ts(sec: float) -> str:
    total_ms = max(0, int(sec * 1000))
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _subtitle_entries(subs: list) -> list:
    """Normalize raw subtitle meta to player-ready entries.

    Keeps the original ``lan`` for URL lookup (PipePipe matches on it) while
    exposing a display ``srclang`` with the ``ai-`` prefix stripped and an
    auto-generated flag (``ai_status != 0``).
    """
    out = []
    for s in subs or []:
        if not isinstance(s, dict):
            continue
        lan = s.get("lan") or ""
        url = s.get("subtitle_url") or ""
        if not lan or not url:
            continue
        auto = bool(s.get("ai_status"))
        label = s.get("lan_doc") or lan
        out.append(
            {
                "lan": lan,
                "srclang": lan[3:] if lan.startswith("ai-") else lan,
                "label": f"{label} (AI)" if auto and "AI" not in label.upper() else label,
                "auto_generated": auto,
            }
        )
    return out


@app.route("/res/subtitle/<vid>")
@app.route("/res/subtitle/<vid>:<idx>")
@rate_limit(**RATE_LIMITS["normal"])
async def subtitle_list_res(vid, idx=0):
    """List available subtitles for a video part (login-gated like PipePipe)."""
    if vid.isdigit() or not (appcred and appcred.sessdata):
        return jsonify([])
    try:
        key = f"miku_sublist_{vid}_{int(idx)}"
        cached = await appredis.get(key)
        if cached:
            from shared import safe_json_loads

            data = safe_json_loads(cached)
            if isinstance(data, list):
                return jsonify(data)
        v = video.Video(bvid=vid, credential=appcred)
        subs = await v.get_subtitle_meta(page_index=int(idx))
        entries = _subtitle_entries(subs)
        import orjson

        await appredis.setex(key, 1800, orjson.dumps(entries).decode())
        return jsonify(entries)
    except Exception as e:
        print(f"Subtitle list error for {vid}:{idx}: {e}")
        return jsonify([])


@app.route("/res/subtitle/<vid>:<idx>:<lan>")
@rate_limit(**RATE_LIMITS["normal"])
async def subtitle_vtt_res(vid, idx, lan):
    """Serve one subtitle track as WebVTT (cached; login-gated)."""
    if vid.isdigit() or not (appcred and appcred.sessdata):
        return Response("Not Found", status=404)
    try:
        idx = int(idx)
        key = f"miku_sub_{vid}_{idx}_{lan}"
        cached = await appredis.get(key)
        if cached:
            return Response(cached, status=200, content_type="text/vtt; charset=utf-8")
        v = video.Video(bvid=vid, credential=appcred)
        subs = await v.get_subtitle_meta(page_index=idx)
        bcc_url = next(
            (s.get("subtitle_url") for s in subs if isinstance(s, dict) and s.get("lan") == lan),
            None,
        )
        if not bcc_url:
            return Response("Not Found", status=404)
        if bcc_url.startswith("//"):
            bcc_url = "https:" + bcc_url
        from shared import Network

        client = await Network.get_async_client()
        resp = await client.get(
            bcc_url,
            headers={
                "Referer": "https://www.bilibili.com",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
                ),
            },
        )
        import orjson as _orjson

        vtt = bcc_to_vtt(_orjson.loads(resp.content))
        await appredis.setex(key, 86400, vtt)
        return Response(vtt, status=200, content_type="text/vtt; charset=utf-8")
    except Exception as e:
        print(f"Subtitle VTT error for {vid}:{idx}:{lan}: {e}")
        return Response("Upstream error", status=502)
