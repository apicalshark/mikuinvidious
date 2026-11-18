# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import re

from quart import Response, jsonify

from miku import state as shared
from miku.api import video
from miku.core.dns_cache import is_safe_public_url
from miku.core.fingerprints import FIXED_CHROME_UA
from miku.core.rate_limit import RATE_LIMITS, ip_family_key, rate_limit
from miku.core.singleflight import BoundedNegativeCache, SingleflightGroup
from miku.core.url_signing import (
    verify_danmaku_sig,
    verify_subtitle_list_sig,
    verify_subtitle_vtt_sig,
)
from miku.media.danmaku import parse_danmaku
from miku.state import app, appredis

# L2: strict route-param validation so subtitle cache keys cannot be
# polluted with separators/wildcards and only real Bilibili IDs reach
# the upstream API.
_VID_RE = re.compile(r"^(BV[a-zA-Z0-9]{10}|av\d{1,20})$")
_LAN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# L2: BCC subtitle files are fetched server-side; only Bilibili CDN/API
# hosts are allowed (same allowlist family as the DASH track proxy).
_ALLOWED_SUBTITLE_SUFFIXES = (
    ".bilivideo.com",
    ".bilivideo.cn",
    ".hdslb.com",
    ".bilibili.com",
    ".biliapi.net",
    ".acgvideo.com",
    ".akamaized.net",
)


def _is_valid_vid(vid: str) -> bool:
    return bool(vid) and bool(_VID_RE.match(vid))


make_video = video.make_video


async def _is_safe_subtitle_url(url: str) -> bool:
    return await is_safe_public_url(
        url,
        _ALLOWED_SUBTITLE_SUFFIXES,
        allowed_schemes=frozenset({"https"}),
        use_dns_cache=False,
    )


DANMAKU_CACHE_TTL = 900

_danmaku_sf = SingleflightGroup()
_danmaku_neg_cache = BoundedNegativeCache(ttl_seconds=30.0)
_subtitle_list_sf = SingleflightGroup()
_subtitle_vtt_sf = SingleflightGroup()
_subtitle_neg_cache = BoundedNegativeCache(ttl_seconds=60.0)


@app.route("/res/danmaku/s/<sig>/<vid>")
@app.route("/res/danmaku/s/<sig>/<vid>:<idx>")
@app.route("/res/danmaku/<vid>")
@app.route("/res/danmaku/<vid>:<idx>")
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("danmaku"))
async def danmaku_res(vid, idx=0, sig=None):
    if not verify_danmaku_sig(sig, vid, idx):
        return Response("Forbidden", status=403)

    if vid.isdigit() or not _is_valid_vid(vid):
        return jsonify([])

    try:
        idx = int(idx)
    except TypeError, ValueError:
        return jsonify([])
    if idx < 0:
        return jsonify([])

    sf_key = f"{vid}:{idx}"
    if _danmaku_neg_cache.get(sf_key) is not None:
        return jsonify([])

    cache_key = f"miku_danmaku_xml_{vid}_{idx}"
    try:
        cached_xml = await appredis.get(cache_key)
        if cached_xml:
            if isinstance(cached_xml, bytes):
                cached_xml = cached_xml.decode("utf-8", errors="replace")
            if isinstance(cached_xml, str):
                stripped = cached_xml.lstrip().lower()
                if not (stripped.startswith("<!doctype") or stripped.startswith("<html")):
                    return jsonify(parse_danmaku(cached_xml))
    except Exception:
        pass

    async def _fetch_xml() -> str | None:
        if _danmaku_neg_cache.get(sf_key) is not None:
            return None
        try:
            v = make_video(vid, shared.appcred)
            xml = await v.get_danmaku_xml(idx)
            upstream_error = bool(getattr(v, "_danmaku_upstream_error", False))
            if not xml or not isinstance(xml, str):
                if not upstream_error:
                    _danmaku_neg_cache.set(sf_key, True)
                return None
            stripped = xml.lstrip().lower()
            if stripped.startswith("<!doctype") or stripped.startswith("<html"):
                return None
            if stripped.startswith("<"):
                try:
                    await appredis.setex(cache_key, DANMAKU_CACHE_TTL, xml)
                except Exception:
                    pass
            return xml
        except Exception as e:
            print(f"Danmaku error for {vid}:{idx}: {e}")
            return None

    xml = await _danmaku_sf.do(sf_key, _fetch_xml)
    if not xml:
        return jsonify([])
    return jsonify(parse_danmaku(xml))


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
        except TypeError, ValueError, AttributeError:
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


@app.route("/res/subtitle/s/<sig>/<vid>")
@app.route("/res/subtitle/s/<sig>/<vid>:<idx>")
@app.route("/res/subtitle/<vid>")
@app.route("/res/subtitle/<vid>:<idx>")
@rate_limit(**RATE_LIMITS["normal"])
async def subtitle_list_res(vid, idx=0, sig=None):
    """List available subtitles for a video part (login-gated like PipePipe)."""
    if not verify_subtitle_list_sig(sig, vid, idx):
        return Response("Forbidden", status=403)
    cred = shared.appcred
    if vid.isdigit() or not (cred and cred.sessdata):
        return jsonify([])
    if not _is_valid_vid(vid):
        return jsonify([])
    try:
        idx_int = int(idx)
    except TypeError, ValueError:
        return jsonify([])
    if idx_int < 0:
        return jsonify([])

    key = f"miku_sublist_{vid}_{idx_int}"
    try:
        cached = await appredis.get(key)
        if cached:
            from miku.state import safe_json_loads

            data = safe_json_loads(cached)
            if isinstance(data, list):
                return jsonify(data)
    except Exception:
        pass

    async def _fetch_entries() -> list[dict]:
        try:
            v = make_video(vid, cred)
            subs = await v.get_subtitle_meta(page_index=idx_int)
            entries = _subtitle_entries(subs)
            import orjson

            await appredis.setex(key, 1800, orjson.dumps(entries).decode())
            return entries
        except Exception as e:
            print(f"Subtitle list error for {vid}:{idx}: {e}")
            return []

    entries = await _subtitle_list_sf.do(f"{vid}:{idx_int}", _fetch_entries)
    return jsonify(entries)


@app.route("/res/subtitle/s/<sig>/<vid>:<idx>:<lan>")
@app.route("/res/subtitle/<vid>:<idx>:<lan>")
@rate_limit(**RATE_LIMITS["normal"])
async def subtitle_vtt_res(vid, idx, lan, sig=None):
    """Serve one subtitle track as WebVTT (cached; login-gated)."""
    if not verify_subtitle_vtt_sig(sig, vid, idx, lan):
        return Response("Forbidden", status=403)
    cred = shared.appcred
    if vid.isdigit() or not (cred and cred.sessdata):
        return Response("Not Found", status=404)
    if not _is_valid_vid(vid) or not _LAN_RE.match(lan or ""):
        return Response("Not Found", status=404)
    try:
        idx_int = int(idx)
    except TypeError, ValueError:
        return Response("Not Found", status=404)
    if idx_int < 0:
        return Response("Not Found", status=404)

    neg_key = f"{vid}:{idx_int}:{lan}"
    if _subtitle_neg_cache.get(neg_key) is not None:
        return Response("Not Found", status=404)

    key = f"miku_sub_{vid}_{idx_int}_{lan}"
    try:
        cached = await appredis.get(key)
        if cached:
            return Response(cached, status=200, content_type="text/vtt; charset=utf-8")
    except Exception:
        pass

    async def _fetch_vtt() -> tuple[int, str]:
        if _subtitle_neg_cache.get(neg_key) is not None:
            return 404, ""
        try:
            v = make_video(vid, cred)
            subs = await v.get_subtitle_meta(page_index=idx_int)
            bcc_url = next(
                (s.get("subtitle_url") for s in subs if isinstance(s, dict) and s.get("lan") == lan),
                None,
            )
            if not bcc_url:
                _subtitle_neg_cache.set(neg_key, 404)
                return 404, ""
            if bcc_url.startswith("//"):
                bcc_url = "https:" + bcc_url
            if not await _is_safe_subtitle_url(bcc_url):
                return 404, ""

            client = await shared.Network.get_async_client()
            resp = await client.get(
                bcc_url,
                headers={
                    "Referer": "https://www.bilibili.com",
                    "User-Agent": FIXED_CHROME_UA,
                },
                timeout=15.0,
                follow_redirects=False,
            )
            if resp.status_code in (404, 410):
                _subtitle_neg_cache.set(neg_key, 404)
                return 404, ""
            if resp.status_code != 200:
                return 502, ""
            import orjson as _orjson

            vtt = bcc_to_vtt(_orjson.loads(resp.content))
            await appredis.setex(key, 86400, vtt)
            return 200, vtt
        except Exception as e:
            print(f"Subtitle VTT error for {vid}:{idx}:{lan}: {e}")
            return 502, ""

    status, vtt = await _subtitle_vtt_sf.do(neg_key, _fetch_vtt)
    if status == 200:
        return Response(vtt, status=200, content_type="text/vtt; charset=utf-8")
    if status == 404:
        return Response("Not Found", status=404)
    return Response("Upstream error", status=502)
