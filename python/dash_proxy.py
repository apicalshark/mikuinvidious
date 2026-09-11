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
"""
Canonical DASH stack for MikuInvidious.

Bilibili removed the ``durl`` (progressive MP4/FLV) response from ``playurl``;
only the ``dash`` node is returned (fragmented-MP4 on-demand tracks). This
module:

  * Fetches the canonical DASH play info (``video_get_dash_for_qn``) for both
    UGC (wbi playurl) and PGC (pgc playurl) sources,
  * caches it under ``miku_dash_<vid>_<idx>`` in Redis,
  * serves an ``isoff-on-demand`` MPD manifest (``/video/dash/<vid>/<idx>/manifest.mpd``),
  * proxies track byte ranges through the WARP SOCKS5 tunnel via ``CdnConnection``
    (``/proxy/dash/...``), faithfully passing through ``206``/``Content-Range``.

The on-demand DASH profile requires the proxy to honour HTTP Range requests
and to emit ``206 Partial Content`` with ``Content-Range``/``Content-Length`` —
this is why the old httpx-based ``proxy_dash`` (which stripped those headers)
was replaced with a raw-socket ``CdnConnection`` proxy.
"""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import deque
from urllib.parse import urlparse

import aiofiles
import orjson
from csrf import csrf_protect
from quart import Blueprint, Response, jsonify, redirect, request
from rate_limit import RATE_LIMITS, rate_limit
from shared import (
    Network,
    TicketManager,
    app,
    appconf,
    appcred,
    appredis,
    safe_json_loads,
)
from stream import CdnConnectError, CdnConnection, CdnProtocolError, CdnTimeoutError

dash_proxy_bp = Blueprint("dash_proxy", __name__)

DASH_CACHE_TTL = 1800
DASH_FETCH_TIMEOUT = 8.0

# Raw CDN domains allowed through the DASH track proxy.
_ALLOWED_DASH_DOMAINS = [
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
]


def _is_safe_dash_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    if not any(hostname == d.lstrip(".") or hostname.endswith(d) for d in _ALLOWED_DASH_DOMAINS):
        return False
    return True


async def _extract_ep_id(vi) -> str | None:
    """Best-effort extraction of a PGC ``ep_id`` from the video redirect URL."""
    try:
        info = await vi.get_info()
        redirect_url = info.get("redirect_url", "") or ""
        if redirect_url:
            m = re.search(r"ep(\d+)", redirect_url)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _normalize_track_urls(tracks: list | None) -> list:
    """Normalize Bilibili's duplicate key spellings (baseUrl/base_url/backup_url)."""
    if not tracks:
        return []
    out = []
    for t in tracks:
        if not isinstance(t, dict):
            continue
        t = dict(t)
        if "baseUrl" in t and "base_url" not in t:
            t["base_url"] = t["baseUrl"]
        if "backupUrl" in t and "backup_url" not in t:
            t["backup_url"] = t["backupUrl"]
        out.append(t)
    return out


def _parse_pgc_playurl(pgc: dict) -> dict | None:
    """Normalize a PGC playurl response to the UGC play-data shape."""
    if not isinstance(pgc, dict) or pgc.get("code") != 0:
        return None
    result = pgc.get("result") or {}
    video_info = result.get("video_info") if isinstance(result, dict) else None
    dash = (video_info or {}).get("dash") if isinstance(video_info, dict) else None
    if not dash and isinstance(result, dict):
        dash = result.get("dash")
    durl = (result.get("durl") or []) if isinstance(result, dict) else []
    if not durl and isinstance(video_info, dict):
        durl = video_info.get("durl") or []
    support_formats = result.get("support_formats", []) if isinstance(result, dict) else []
    return {
        "code": 0,
        "dash": dash or {},
        "durl": durl,
        "support_formats": support_formats,
        "quality": result.get("quality", 0) if isinstance(result, dict) else 0,
        "accept_quality": result.get("accept_quality", []) if isinstance(result, dict) else [],
    }


async def video_get_dash_for_qn(vi, idx, ep_id=None) -> dict:
    """Fetch canonical DASH play info, returning a ``{"dash":..., "durl":..., "support_formats":...}`` dict.

    Uses :meth:`api.video.Video.get_dash_playurl` (wbi-signed UGC endpoint).
    Falls back to the PGC playurl endpoint (not wbi-signed) when the UGC path
    returns an error / empty dash, which happens for premium (PGC) content.
    """
    from api import video

    v = vi if isinstance(vi, video.Video) else video.Video(bvid=vi, credential=appcred)
    try:
        cid = await v.get_cid(idx)
    except Exception as exc:
        return {"code": -1, "message": f"failed to resolve cid: {exc}"}
    if ep_id is None:
        ep_id = await _extract_ep_id(v)

    # 1) UGC wbi playurl (canonical path)
    try:
        data = await v.get_dash_playurl(page_index=idx, cid=cid, qn=120)
        if data and isinstance(data, dict) and (data.get("dash") or data.get("durl") or data.get("support_formats")):
            return {
                "code": data.get("code", 0),
                "dash": data.get("dash") or {},
                "durl": data.get("durl") or [],
                "support_formats": data.get("support_formats") or [],
                "quality": data.get("quality", 0),
                "accept_quality": data.get("accept_quality", []),
            }
    except Exception as exc:
        print(f"[DashProxy] UGC playurl failed for {v.get_bvid()}: {exc}")

    # 2) PGC playurl fallback (premium / non-wbi endpoint)
    try:
        client = await Network.get_async_client()
        cookies = {}
        if appcred and appcred.sessdata:
            cookies = {
                "SESSDATA": appcred.sessdata,
                "bili_jct": appcred.bili_jct,
                "buvid3": appcred.buvid3,
                "buvid4": appcred.buvid4,
                "DedeUserID": appcred.dedeuserid,
            }
        pgc_params = {
            "avid": v.get_aid(),
            "cid": cid,
            "qn": 120,
            "fnval": 4048,
            "fourk": 1,
            "platform": "html5",
            "high_quality": 1,
        }
        if ep_id:
            pgc_params["ep_id"] = ep_id
        pgc_raw = await client.get(
            "https://api.bilibili.com/pgc/player/web/playurl",
            params=pgc_params,
            cookies=cookies,
            headers={
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            },
            follow_redirects=True,
        )
        pgc = pgc_raw.json()
        parsed = _parse_pgc_playurl(pgc)
        if parsed:
            return parsed
        print(f"[DashProxy] PGC fallback returned code {pgc.get('code') if isinstance(pgc, dict) else '?'}")
        code = pgc.get("code", -1) if isinstance(pgc, dict) else -1
        msg = pgc.get("message", "PGC playurl failed") if isinstance(pgc, dict) else "PGC playurl failed"
        return {"code": code, "message": msg}
    except Exception as exc:
        print(f"[DashProxy] PGC fallback failed for {v.get_bvid()}: {exc}")
        return {"code": -1, "message": str(exc)}


def has_valid_dash_tracks(dash_data: dict | None) -> bool:
    """Check whether a dash payload has at least one playable track.

    A track is playable when it carries a ``SegmentBase`` with an
    ``indexRange`` — dash.js needs the sidx range to compute segment
    byte-offsets. Some UGC uploads are ``durl``-only (progressive MP4, no
    ``dash`` node at all, e.g. BV1kH35z9EzG); those must fall back to the
    progressive ``/proxy/video/`` path instead of serving an empty MPD.
    """
    if not dash_data or not isinstance(dash_data, dict):
        return False
    dash = dash_data.get("dash") or {}
    if not isinstance(dash, dict):
        return False
    for key in ("video", "audio"):
        for t in dash.get(key) or []:
            if not isinstance(t, dict):
                continue
            sb = t.get("SegmentBase") or {}
            if sb.get("indexRange"):
                return True
    return False


async def _fetch_single_durl_pgc(v, base_params: dict, ep_id=None) -> dict | None:
    """Retry a 404'd UGC durl request against the PGC playurl endpoint."""
    import re as _re

    try:
        client = await Network.get_async_client()
        cookies = {}
        if v.credential and v.credential.sessdata:
            cookies = {
                "SESSDATA": v.credential.sessdata,
                "bili_jct": v.credential.bili_jct,
                "buvid3": v.credential.buvid3,
                "buvid4": v.credential.buvid4,
                "DedeUserID": v.credential.dedeuserid,
            }
        pgc_params = dict(base_params)
        if ep_id is None:
            try:
                info = await v.get_info()
                m = _re.search(r"ep(\d+)", info.get("redirect_url", "") or "")
                if m:
                    ep_id = m.group(1)
            except Exception:
                pass
        if ep_id:
            pgc_params["ep_id"] = ep_id
        raw = await client.get(
            "https://api.bilibili.com/pgc/player/web/playurl",
            params=pgc_params,
            cookies=cookies,
            headers={
                "Referer": "https://www.bilibili.com",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        pgc = raw.json()
        if isinstance(pgc, dict) and pgc.get("code") == 0:
            parsed = _parse_pgc_playurl(pgc)
            return parsed if parsed and parsed.get("durl") else None
    except Exception:
        pass
    return None


async def _fetch_single_durl(v, idx: int, qn: int, ep_id=None) -> dict | None:
    """Fetch one progressive (``durl``) playurl for a quality level.

    Uses the non-wbi ``/x/player/playurl`` endpoint (per-quality ``qn``),
    which still returns a ``durl`` node for durl-only uploads. Returns the
    ``data``-shaped dict on success, else None.
    """
    from api.client import Api

    try:
        cid = await v.get_cid(idx)
    except Exception:
        return None
    api = Api(
        "https://api.bilibili.com/x/player/playurl",
        "GET",
        verify=False,
        credential=v.credential,
    )
    api.params = {
        "avid": v.get_aid(),
        "cid": cid,
        "qn": qn,
        "platform": "html5",
        "high_quality": 1,
    }
    try:
        res = await asyncio.wait_for(api.request(), timeout=4.0)
    except Exception as exc:
        # PGC content 404s on the UGC endpoint — retry the PGC playurl.
        if not (hasattr(exc, "code") and exc.code == -404):
            return None
        return await _fetch_single_durl_pgc(v, api.params, ep_id=ep_id)
    if isinstance(res, dict) and res.get("durl"):
        return res
    return None


def _durl_quality_list(play_data: dict | None, max_qualities: int) -> list[tuple[int, str]]:
    """Ordered ``(qn, description)`` list for the durl fallback fetches."""
    support_formats = []
    if isinstance(play_data, dict):
        support_formats = play_data.get("support_formats") or []
    qualities: list[tuple[int, str]] = []
    for f in support_formats:
        if not isinstance(f, dict) or f.get("quality") is None:
            continue
        try:
            qn = int(f["quality"])
        except (TypeError, ValueError):
            continue
        if any(q == qn for q, _ in qualities):
            continue
        qualities.append((qn, f.get("new_description") or f.get("display_desc") or str(qn)))
        if len(qualities) >= max_qualities:
            break
    if not qualities:
        fallback_qn = 0
        try:
            fallback_qn = int((play_data or {}).get("quality") or 0)
        except (TypeError, ValueError):
            fallback_qn = 0
        qualities = [(fallback_qn or 64, str(fallback_qn or 64))]
    return qualities


async def _cache_durl_entry(vid: str, idx: int, qn: int, desc: str, node: dict | None) -> dict | None:
    """Cache one quality's durl URLs; return its ``supported_src`` entry."""
    if not isinstance(node, dict):
        return None
    durl = node.get("durl") or []
    if not durl or not isinstance(durl[0], dict) or not durl[0].get("url"):
        return None
    url = durl[0]["url"]
    ext = ".flv" if ".flv" in url.lower() else ".mp4"
    actual_qn = node.get("quality", qn)
    try:
        actual_qn = int(actual_qn)
    except (TypeError, ValueError):
        actual_qn = qn
    await appredis.setex(f"mikuinv_{vid}_{idx}_{actual_qn}", 1800, url)
    backups = durl[0].get("backup_url", []) or []
    if backups and backups[0] != url:
        await appredis.setex(f"mikuinv_{vid}_{idx}_{actual_qn}_bak", 1800, backups[0])
    return {"quality": actual_qn, "new_description": desc, "ext": ext}


async def fetch_durl_supported_src(v, vid: str, idx: int, play_data: dict | None = None,
                                   ep_id=None, max_qualities: int = 4, force: bool = False) -> list:
    """Fetch + cache progressive (``durl``) URLs for durl-only videos.

    Returns a ``supported_src`` list of ``{quality, new_description, ext}``
    (the shape ``macros.html`` / ``player.js`` expect for native playback
    via ``/proxy/video/<vid>_<idx>_<qn><ext>``). Each quality's primary +
    backup CDN URL is cached under ``mikuinv_<vid>_<idx>_<qn>`` (``+_bak``),
    and the assembled list under ``mikuinv_<vid>_<idx>`` for fast reuse.

    With ``force=True`` the list cache is bypassed and every quality is
    re-resolved (used to recover from the stale-list race below).
    """
    if not force:
        cached = await appredis.get(f"mikuinv_{vid}_{idx}")
        if cached:
            data = safe_json_loads(cached)
            if isinstance(data, list) and data:
                return data

    qualities = _durl_quality_list(play_data, max_qualities)

    # Reuse the durl already fetched for the first quality (avoids one request).
    initial_durl = (play_data or {}).get("durl") if isinstance(play_data, dict) else None
    initial_qn = None
    try:
        initial_qn = int((play_data or {}).get("quality") or 0)
    except (TypeError, ValueError):
        initial_qn = None

    async def resolve_one(qn: int, desc: str) -> dict | None:
        if initial_durl and initial_qn == qn:
            node = play_data
        else:
            node = await _fetch_single_durl(v, idx, qn, ep_id=ep_id)
        return await _cache_durl_entry(vid, idx, qn, desc, node)

    results = await asyncio.gather(*[resolve_one(qn, desc) for qn, desc in qualities])
    supported = sorted(
        [r for r in results if r],
        key=lambda r: r["quality"],
        reverse=True,
    )
    if supported:
        await appredis.setex(f"mikuinv_{vid}_{idx}", 1800, orjson.dumps(supported))
    return supported


class _DurlResolveError(Exception):
    """Progressive URL resolution failed; carries the legacy HTTP status."""

    def __init__(self, message: str, status: int = 404):
        super().__init__(message)
        self.status = status


async def _resolve_durl_download(v, vid: str, idx: int, qual: int, play_data: dict | None = None):
    """Resolve ``(url, qn, ext)`` for a durl-only download.

    Tolerates the stale-list race: the ``/proxy/video/`` route deletes
    per-quality CDN keys when it rotates to backups (proxy.py), while the
    quality-list cache (``mikuinv_<vid>_<idx>``) can still reference them.
    On a per-quality miss the resolution is retried once with a forced fresh
    fetch (reusing neither the list cache nor the possibly-stale initial
    ``durl``) before giving up.
    """
    for force in (False, True):
        node_data = play_data
        if force and isinstance(node_data, dict):
            # Drop the possibly-stale initial durl; keep support_formats so
            # all qualities are re-fetched fresh.
            node_data = {**node_data, "durl": None}
        try:
            supported = await asyncio.wait_for(
                fetch_durl_supported_src(v, vid, idx, play_data=node_data, force=force),
                timeout=25.0,
            )
        except Exception as exc:
            print(f"[DashProxy] durl resolve (force={force}) failed for {vid}:{idx}: {exc}")
            if force:
                raise _DurlResolveError("upstream error", status=502) from exc
            continue
        if not supported:
            if force:
                raise _DurlResolveError("no progressive sources", status=404)
            continue
        qn, ext = _choose_durl_entry(supported, qual)
        url = await appredis.get(f"mikuinv_{vid}_{idx}_{qn}")
        if url:
            if isinstance(url, bytes):
                url = url.decode()
            return url, qn, ext
        print(f"[DashProxy] durl cache miss for {vid}:{idx}:qn={qn} (force={force}), re-resolving...")
    raise _DurlResolveError("progressive URL expired, please retry", status=404)


async def _load_dash_data(vid, idx) -> dict | None:
    """Read cached dash JSON, or fetch + cache it. Returns the dash dict or None."""
    from api import video

    cached = await appredis.get(f"miku_dash_{vid}_{idx}")
    if cached:
        data = safe_json_loads(cached)
        if isinstance(data, dict):
            return data
    v = video.Video(bvid=vid, credential=appcred)
    try:
        data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=DASH_FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"[DashProxy] Fetching dash for {vid}:{idx} timed out")
        return None
    if not data or not data.get("dash"):
        return None
    await appredis.setex(f"miku_dash_{vid}_{idx}", DASH_CACHE_TTL, orjson.dumps(data))
    return data


def _lookup_track(dash_data, media_type: str, qn: int, cid: int) -> dict | None:
    """Return a single normalized track from a dash node for a given media type."""
    if not dash_data or not isinstance(dash_data, dict):
        return None
    dash = dash_data.get("dash") or {}
    tracks = dash.get(media_type, [])
    if not tracks and media_type == "audio":
        tracks = (dash.get("dolby") or {}).get("audio", [])
        if not tracks:
            tracks = (dash.get("flac") or {}).get("audio", [])
    tracks = _normalize_track_urls(tracks)
    for t in tracks:
        if str(t.get("id")) == str(qn) and str(t.get("codecid", 0)) == str(cid):
            return t
    # Fall back to first match by id only (some responses lack codecid)
    for t in tracks:
        if str(t.get("id")) == str(qn):
            return t
    return None


def _sb_initialization_range(sb: dict) -> str:
    """Extract the ``Initialization`` byte range from a SegmentBase node.

    Bilibili returns ``Initialization`` either as a plain string (``"0-123"``)
    or as an object (``{"range": "0-123"}``). Handle both.
    """
    init = sb.get("Initialization") or sb.get("initialization")
    if isinstance(init, dict):
        return str(init.get("range") or init.get("indexRange") or "0-999")
    if isinstance(init, str) and init:
        return init
    return "0-999"


def generate_vod_mpd(vid, idx, dash_data) -> str | None:
    """Generate an ``isoff-on-demand`` MPD for dash.js playback.

    Each track becomes a Representation whose BaseURL points at the local
    Range-capable proxy (``/proxy/dash/...``). SegmentBase carries the exact
    ``Initialization`` and ``indexRange`` byte ranges so dash.js can compute
    segment offsets and issue HTTP Range requests.
    """
    if not dash_data or not isinstance(dash_data, dict):
        return None
    dash = dash_data.get("dash") or {}
    if not dash:
        return None

    duration = dash.get("duration", 0) or 0
    min_buffer = dash.get("minBufferTime", 1.5)
    period_dur = f' mediaPresentationDuration="PT{duration}S"' if duration else ""

    mpd = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static"{period_dur} minBufferTime="PT{min_buffer}S">',
        '  <Period id="1" start="PT0S">',
    ]

    # --- Video adaptation sets (one per codec) ---
    # Bilibili returns multiple codec variants (AVC/HEVC/AV1) at each quality
    # level.  Mixing them in one AdaptationSet breaks MSE: the SourceBuffer is
    # created for the first codec the player picks, and appending data encoded
    # with a *different* codec triggers CHUNK_DEMUXER_ERROR_APPEND_FAILED
    # ("Video stream codec hevc doesn't match SourceBuffer codecs.").
    # Fix: group tracks by their ``codecid`` into separate AdaptationSets
    # so each SourceBuffer only ever sees one codec family.
    as_id = 1
    videos = _normalize_track_urls(dash.get("video", []))
    if videos:
        from collections import OrderedDict
        # Group by codecid (7=AVC, 12=HEVC, 13=AV1) rather than the full
        # codecs string — Bilibili uses different HEVC level strings per
        # resolution (e.g. hvc1.1.6.L150.90 vs hvc1.1.6.L120.90) which would
        # unnecessarily fragment the groups.
        codec_groups: OrderedDict[int, list] = OrderedDict()
        for video_track in videos:
            codecid = video_track.get("codecid", 0)
            codec_groups.setdefault(codecid, []).append(video_track)
        as_id = 1
        for _codecid, tracks in codec_groups.items():
            # Use the codecs string from the first track in the group for the
            # AdaptationSet-level codecs attribute.
            codecs_str = tracks[0].get("codecs") or "avc1.64001F"
            mpd.append(
                f'    <AdaptationSet id="{as_id}" contentType="video" mimeType="video/mp4"'
                f' codecs="{codecs_str}" segmentAlignment="true"'
                f' subsegmentAlignment="true" startWithSAP="1">'
            )
            for video_track in tracks:
                qn = video_track.get("id")
                cid = video_track.get("codecid", 0)
                bandwidth = video_track.get("bandwidth", 0)
                width = video_track.get("width", 0)
                height = video_track.get("height", 0)
                frame_rate = video_track.get("frameRate") or video_track.get("frame_rate") or "24"
                if qn is None or not video_track.get("SegmentBase"):
                    continue
                sb = video_track["SegmentBase"]
                init_range = _sb_initialization_range(sb)
                index_range = sb.get("indexRange", "")
                index_exact = ' indexRangeExact="true"' if index_range else ""
                if not index_range:
                    continue
                track_codecs = video_track.get("codecs") or codecs_str
                mpd.append(
                    f'      <Representation id="video_{qn}_{cid}" codecs="{track_codecs}" bandwidth="{bandwidth}" width="{width}" height="{height}" frameRate="{frame_rate}">'
                )
                mpd.append(f"        <BaseURL>/proxy/dash/{vid}/{idx}/video/{qn}/{cid}</BaseURL>")
                mpd.append(f'        <SegmentBase indexRange="{index_range}"{index_exact}>')
                mpd.append(f'          <Initialization range="{init_range}"/>')
                mpd.append("        </SegmentBase>")
                mpd.append("      </Representation>")
            mpd.append("    </AdaptationSet>")
            as_id += 1

    # --- Audio adaptation set ---
    audios = _normalize_track_urls(dash.get("audio", []))
    if not audios:
        audios = _normalize_track_urls((dash.get("dolby") or {}).get("audio", []))
    if not audios:
        audios = _normalize_track_urls((dash.get("flac") or {}).get("audio", []))
    if audios:
        mpd.append(f'    <AdaptationSet id="{as_id}" contentType="audio" mimeType="audio/mp4" segmentAlignment="true" subsegmentAlignment="true" startWithSAP="1">')
        for audio_track in audios:
            qn = audio_track.get("id")
            cid = audio_track.get("codecid", 0)
            bandwidth = audio_track.get("bandwidth", 0)
            codecs = audio_track.get("codecs") or "mp4a.40.2"
            if qn is None or not audio_track.get("SegmentBase"):
                continue
            sb = audio_track["SegmentBase"]
            init_range = _sb_initialization_range(sb)
            index_range = sb.get("indexRange", "")
            index_exact = ' indexRangeExact="true"' if index_range else ""
            if not index_range:
                continue
            mpd.append(f'      <Representation id="audio_{qn}_{cid}" codecs="{codecs}" bandwidth="{bandwidth}">')
            mpd.append(f"        <BaseURL>/proxy/dash/{vid}/{idx}/audio/{qn}/{cid}</BaseURL>")
            mpd.append(f'        <SegmentBase indexRange="{index_range}"{index_exact}>')
            mpd.append(f'          <Initialization range="{init_range}"/>')
            mpd.append("        </SegmentBase>")
            mpd.append("      </Representation>")
        mpd.append("    </AdaptationSet>")

    mpd.append("  </Period>")
    mpd.append("</MPD>")
    return "\n".join(mpd)


# ---------------------------------------------------------------------------
# Muxed MP4 download (DASH video + audio -> single playable MP4 via ffmpeg)
# ---------------------------------------------------------------------------

# Anonymous scraping caps out at 1080p (quality 80). Anything higher (1080p+,
# 1080p60, 4K...) requires login / season-vip. Keep downloads capped at 1080p.
_FREE_DOWNLOAD_MAX_QN = 80
_MAX_DOWNLOAD_TRACK_BYTES = 1024 * 1024 * 1024
_DOWNLOAD_TOO_LARGE = -2
_download_limiter = asyncio.Semaphore(2)


def _download_size_status(headers: dict) -> int:
    content_length = headers.get("content-length")
    if content_length is None:
        return 0
    try:
        parsed_content_length = int(content_length)
    except ValueError:
        return -1
    if parsed_content_length < 0:
        return -1
    if parsed_content_length > _MAX_DOWNLOAD_TRACK_BYTES:
        return _DOWNLOAD_TOO_LARGE
    return 0

# Maximum permitted size per individual DASH track download (500 MB).
_MAX_DOWNLOAD_TRACK_BYTES = 500 * 1024 * 1024

# Sentinel returned by _download_track_to_file when a job cancel was observed.
_DOWNLOAD_CANCELLED = -3

# Resume-with-backoff when the CDN/WARP tunnel cuts a track download mid-body
# ("Upstream connection closed prematurely", connection resets, read timeouts).
# Up to _TRACK_DOWNLOAD_MAX_RETRIES resume attempts (Range requests) after the
# initial try, waiting a few seconds between attempts.
_TRACK_DOWNLOAD_MAX_RETRIES = 5
_TRACK_DOWNLOAD_RETRY_DELAYS = (2.0, 4.0, 8.0, 12.0, 16.0)
_RETRYABLE_TRACK_ERRORS = (
    CdnConnectError,
    CdnProtocolError,
    CdnTimeoutError,
    asyncio.TimeoutError,
    OSError,  # covers ConnectionResetError / BrokenPipeError
)


class _TrackCut(Exception):
    """Internal control flow: connection cut mid-attempt, carries partial progress."""

    def __init__(self, total: int, file_obj, cause: Exception):
        super().__init__(str(cause))
        self.total = total
        self.file_obj = file_obj
        self.cause = cause


_CUT_ERRORS = (_TrackCut, *_RETRYABLE_TRACK_ERRORS)

# Bounded semaphore to cap concurrent download & mux disk usage.
_download_limiter = asyncio.Semaphore(5)


async def _build_dash_cdn_headers() -> dict:
    """CDN header set for DASH track requests (proxy + download).

    Bilibili's .bilivideo.com DASH CDN returns 403 when the request carries the
    Android app User-Agent (used for the API layer) — the CDN only serves DASH
    tracks to a web-browser UA. So build CDN-specific headers here (web UA +
    Referer/Origin), NOT the android get_common_headers() set.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": appconf["bili"].get("referer", "https://www.bilibili.com"),
        "Origin": "https://www.bilibili.com",
        "Accept": "*/*",
    }
    ticket = await TicketManager.get_ticket()
    if ticket:
        headers["x-bili-ticket"] = ticket
    headers["session_id"] = TicketManager._generate_session_id()
    headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
    creds = appconf["credential"]
    if creds.get("buvid3"):
        headers["buvid"] = creds["buvid3"]
    if creds.get("buvid4"):
        headers["buvid4"] = creds["buvid4"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds.get("use_cred") else {}
    if cookie_jar:
        headers["cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_jar.items())
    return headers


def _pick_download_tracks(dash_data: dict | None, max_video_qn: int) -> tuple:
    """Pick the best (video, audio) track pair for a muxed download.

    Video: highest ``id`` (quality) <= ``max_video_qn``. Audio: highest ``id``
    from the standard ``dash.audio`` array (avoids Dolby/FLAC lossless tracks).
    """
    if not dash_data or not isinstance(dash_data, dict):
        return None, None
    dash = dash_data.get("dash") or {}

    video = None
    for t in _normalize_track_urls(dash.get("video")):
        try:
            qn = int(t.get("id", 0))
        except (TypeError, ValueError):
            qn = 0
        if qn <= max_video_qn and (video is None or qn > int(video.get("id") or 0)):
            video = t

    audio = None
    for t in _normalize_track_urls(dash.get("audio")):
        try:
            qn = int(t.get("id", 0))
        except (TypeError, ValueError):
            qn = 0
        if audio is None or qn > int(audio.get("id") or 0):
            audio = t
    return video, audio


async def _open_cdn_track(url: str, headers: dict, proxy_url: str):
    """Connect + send request + read headers, with one bili_ticket refresh retry.

    Returns ``(conn, resp_headers)``; the caller owns ``conn.close()``.
    """
    headers = dict(headers)
    conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
    try:
        await conn.connect()
        await conn.send_request()
        resp_headers = await conn.read_response_headers()
    except BaseException:
        await conn.close()
        raise
    if resp_headers.status_code in (403, 412, 514):
        await conn.close()
        ticket = await TicketManager.get_ticket(force_refresh=True)
        if ticket:
            headers["x-bili-ticket"] = ticket
        else:
            headers.pop("x-bili-ticket", None)
        conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
        try:
            await conn.connect()
            await conn.send_request()
            resp_headers = await conn.read_response_headers()
        except BaseException:
            await conn.close()
            raise
    return conn, resp_headers


def _parse_content_range_start(headers) -> int | None:
    """Parse the start offset from a ``Content-Range: bytes <start>-...`` header."""
    cr = ((headers or {}).get("content-range") or "")
    m = re.match(r"bytes\s+(\d+)-", cr)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


async def _await_track_retry(attempt: int, cancel_event: asyncio.Event | None,
                             note_cb, exc: Exception) -> str:
    """Back off before resume attempt ``attempt`` (0-based). Returns 'retry', 'abort' or 'cancelled'."""
    if attempt >= _TRACK_DOWNLOAD_MAX_RETRIES:
        print(f"[DashProxy] track download retries exhausted after {attempt} retries: {exc}")
        return "abort"
    print(f"[DashProxy] track download cut, will resume "
          f"(retry {attempt + 1}/{_TRACK_DOWNLOAD_MAX_RETRIES}): {exc}")
    delay = _TRACK_DOWNLOAD_RETRY_DELAYS[min(attempt, len(_TRACK_DOWNLOAD_RETRY_DELAYS) - 1)]
    if note_cb is not None:
        note_cb(f"连接中断，{delay:g}秒后重试（第{attempt + 1}/{_TRACK_DOWNLOAD_MAX_RETRIES}次）…")
    if cancel_event is None:
        await asyncio.sleep(delay)
        return "retry"
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=delay)
        return "cancelled"
    except asyncio.TimeoutError:
        return "retry"


async def _download_track_to_file(
    url: str,
    headers: dict,
    proxy_url: str,
    dest: str,
    max_bytes: int = _MAX_DOWNLOAD_TRACK_BYTES,
    progress_cb=None,
    cancel_event: asyncio.Event | None = None,
    note_cb=None,
    rewind_cb=None,
) -> int:
    """Download a full track body to ``dest`` via CdnConnection, resuming on cuts.

    When upstream cuts the connection mid-body (reset / premature close /
    read timeout), waits a few seconds and resumes from the downloaded offset
    with a ``Range`` request, up to ``_TRACK_DOWNLOAD_MAX_RETRIES`` retries.

    Returns byte count, ``-1`` on error/oversize, or ``_DOWNLOAD_CANCELLED``
    when ``cancel_event`` is set (checked per chunk and during backoff waits;
    the partial file is left for caller cleanup).
    """
    total = 0
    attempt = 0
    file_obj = None
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return _DOWNLOAD_CANCELLED
            try:
                total, file_obj, outcome = await _fetch_track_attempt(
                    url, headers, proxy_url, dest, file_obj, total,
                    max_bytes, progress_cb, cancel_event, rewind_cb,
                )
            except _CUT_ERRORS as exc:
                if isinstance(exc, _TrackCut):
                    # Keep the bytes streamed before the cut so we resume, not restart.
                    total, file_obj = exc.total, exc.file_obj
                    cause = exc.cause
                else:
                    # Cut during connect/headers: no progress made yet.
                    cause = exc
                decision = await _await_track_retry(attempt, cancel_event, note_cb, cause)
                if decision == "retry":
                    attempt += 1
                    continue
                return _DOWNLOAD_CANCELLED if decision == "cancelled" else -1
            if note_cb is not None:
                note_cb(None)
            if outcome == "cancelled":
                return _DOWNLOAD_CANCELLED
            return total if outcome == "done" else -1
    except Exception as exc:
        print(f"[DashProxy] track download error: {exc}")
        return -1
    finally:
        if file_obj is not None:
            await asyncio.to_thread(file_obj.close)


def _validate_track_response(resp_headers, total: int, max_bytes: int) -> str:
    """Classify a track GET response: 'ok', 'restart', or 'fatal'.

    'restart' means the server ignored our resume ``Range`` (HTTP 200) — the
    caller must truncate and start over. 'fatal' (bad status, resume offset
    mismatch, oversize) must not be retried.
    """
    status = resp_headers.status_code
    if status not in (200, 206):
        return "fatal"
    if total > 0:
        if status != 206:
            return "restart"
        if _parse_content_range_start(resp_headers.headers) != total:
            print(f"[DashProxy] resume offset mismatch "
                  f"(have {total}, server {resp_headers.headers.get('content-range')})")
            return "fatal"
        return "ok"
    cl_header = resp_headers.headers.get("content-length")
    if cl_header:
        try:
            if int(cl_header) > max_bytes:
                print(f"[DashProxy] track Content-Length {cl_header} exceeds limit {max_bytes}")
                return "fatal"
        except ValueError:
            pass
    return "ok"


async def _fetch_track_attempt(url: str, headers: dict, proxy_url: str, dest: str,
                               file_obj, total: int, max_bytes: int,
                               progress_cb, cancel_event, rewind_cb):
    """One GET (or Range-resume) attempt. Returns ``(total, file_obj, outcome)``.

    ``outcome`` is 'done' (clean EOF), 'fatal' (do not retry), or 'cancelled'.
    Raises ``_TrackCut`` (carrying partial progress) or ``_RETRYABLE_TRACK_ERRORS``
    on connection cuts so the caller can back off and resume.
    """
    req_headers = dict(headers)
    if total > 0:
        req_headers["Range"] = f"bytes={total}-"
    conn, resp_headers = await _open_cdn_track(url, req_headers, proxy_url)
    try:
        action = _validate_track_response(resp_headers, total, max_bytes)
        if action == "fatal":
            return total, file_obj, "fatal"
        if action == "restart":
            if file_obj is not None:
                await asyncio.to_thread(file_obj.close)
                file_obj = None
            if rewind_cb is not None:
                rewind_cb(total)
            total = 0
        if file_obj is None:
            file_obj = await asyncio.to_thread(open, dest, "wb" if total == 0 else "ab")
        total, outcome = await _stream_track_body(
            conn, file_obj, total, max_bytes, progress_cb, cancel_event
        )
        return total, file_obj, outcome
    finally:
        await conn.close()


async def _stream_track_body(conn, file_obj, total: int, max_bytes: int,
                             progress_cb, cancel_event):
    """Stream ``iter_chunks()`` to ``file_obj``. Returns ``(total, outcome)``.

    Raises ``_TrackCut`` (carrying the bytes streamed so far) on connection
    cuts so the caller can resume with ``Range`` instead of restarting.
    """
    try:
        async for chunk in conn.iter_chunks():
            if cancel_event is not None and cancel_event.is_set():
                return total, "cancelled"
            prospective_total = total + len(chunk)
            if prospective_total > max_bytes:
                print(f"[DashProxy] downloaded bytes {prospective_total} exceeded limit {max_bytes}")
                return total, "fatal"
            await asyncio.to_thread(file_obj.write, chunk)
            total = prospective_total
            if progress_cb is not None:
                progress_cb(len(chunk))
        return total, "done"
    except _RETRYABLE_TRACK_ERRORS as exc:
        raise _TrackCut(total, file_obj, exc) from exc


async def _mux_tracks(video_path: str, audio_path: str, out_path: str, job=None) -> None:
    """Remux video + audio tracks into a single faststart MP4 with ffmpeg (-c copy).

    When ``job`` (a ``_DownloadJob``) is given, the ffmpeg subprocess handle is
    tracked on it so a cancel request can kill the mux mid-flight.
    """
    ffmpeg = await asyncio.to_thread(shutil.which, "ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available on this server")
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if job is not None:
            job.ffmpeg_proc = proc
            comm_task = asyncio.ensure_future(proc.communicate())
            cancel_task = asyncio.ensure_future(job.cancel_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {comm_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if comm_task not in done:
                    proc.kill()
                    await proc.wait()
                    raise _JobCancelled()
                _, stderr = await comm_task
            finally:
                for t in (comm_task, cancel_task):
                    if not t.done():
                        t.cancel()
                job.ffmpeg_proc = None
        else:
            _, stderr = await proc.communicate()
    except _JobCancelled:
        try:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        except Exception:
            pass
        raise
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg mux failed: " + (stderr or b"").decode(errors="replace")[-600:])


def _choose_durl_entry(supported: list, want: int) -> tuple:
    """Pick ``(quality, ext)`` from a durl ``supported_src`` list.

    Best quality at or below the request (mirrors ``/proxy/video/`` fallback
    order); ``want=0`` (e.g. listen-page download) means "best available".
    """
    try:
        want = int(want)
    except (TypeError, ValueError):
        want = 0
    candidates = [s for s in supported if s["quality"] <= want] if want > 0 else []
    chosen = candidates[0] if candidates else supported[0]
    return chosen["quality"], chosen.get("ext") or ".mp4"


async def _proxy_download_durl_fallback(vid: str, idx: int, qual: int):
    """Download fallback for durl-only videos (no playable DASH tracks).

    Progressive MP4s are already muxed (video + audio in one file), so no
    ffmpeg step is needed: resolve the best progressive URL at or below the
    requested quality and redirect to the native ``/proxy/video/`` path with
    ``?dl=1`` (attachment), reusing its ticket-refresh + backup-URL +
    lower-quality fallbacks.
    """
    from api import video as video_mod

    try:
        v = video_mod.Video(bvid=vid, credential=appcred)
    except Exception:
        return Response("Bad Request: invalid video ID", status=400)
    try:
        play_data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=8.0)
    except Exception as exc:
        print(f"[DashProxy] durl download fetch failed for {vid}:{idx}: {exc}")
        play_data = None
    try:
        url, qn, ext = await _resolve_durl_download(v, vid, idx, qual, play_data=play_data)
    except _DurlResolveError as exc:
        if exc.status == 502:
            return Response("Upstream error", status=502)
        return Response("Not Found", status=404)
    return redirect(f"/proxy/video/{vid}_{idx}_{qn}{ext}?dl=1", code=302)


@dash_proxy_bp.route("/proxy/download/<vid>/<int:idx>/<int:qual>")
@rate_limit(**RATE_LIMITS["proxy"])
async def proxy_download(vid, idx, qual):
    """Mux the best DASH video (<=1080p anonymous cap) + audio into one MP4.

    Downloads both tracks through the WARP tunnel, remuxes with ffmpeg into a
    single faststart MP4, and streams it back as an attachment.

    Durl-only uploads (no DASH tracks, e.g. BV1kH35z9EzG) fall back to a
    redirect at the native progressive ``/proxy/video/`` path — those MP4s
    are already muxed, so no ffmpeg step is needed.
    """
    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)

    max_qn = min(qual, _FREE_DOWNLOAD_MAX_QN) if qual > 0 else _FREE_DOWNLOAD_MAX_QN
    dash_data = await _load_dash_data(vid, idx)
    if not has_valid_dash_tracks(dash_data):
        return await _proxy_download_durl_fallback(vid, idx, qual)

    video, audio = _pick_download_tracks(dash_data, max_qn)
    if not video or not audio:
        return Response("Not Found: no suitable DASH tracks", status=404)
    vurl = video.get("base_url") or video.get("baseUrl")
    aurl = audio.get("base_url") or audio.get("baseUrl")
    if not vurl or not aurl:
        return Response("Not Found: track has no URL", status=404)
    if not _is_safe_dash_url(vurl) or not _is_safe_dash_url(aurl):
        return Response("Forbidden: Invalid proxy target", status=403)

    proxy_url = Network.get_proxy()
    headers = await _build_dash_cdn_headers()

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix="miku_dl_")
    vpath = os.path.join(tmpdir, "video.m4s")
    apath = os.path.join(tmpdir, "audio.m4s")
    outpath = os.path.join(tmpdir, "out.mp4")

    try:
        async with _download_limiter:
            if await _download_track_to_file(vurl, headers, proxy_url, vpath) < 0:
                return Response("Upstream error (video track)", status=502)
            if await _download_track_to_file(aurl, headers, proxy_url, apath) < 0:
                return Response("Upstream error (audio track)", status=502)
            try:
                await _mux_tracks(vpath, apath, outpath)
            except RuntimeError as exc:
                print(f"[DashProxy] download mux failed for {vid}:{idx}: {exc}")
                return Response("Mux failed", status=502)
        actual_qn = int(video.get("id") or max_qn)

        async def generate():
            try:
                f = await asyncio.to_thread(open, outpath, "rb")
                try:
                    while True:
                        chunk = await asyncio.to_thread(f.read, 512 * 1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    await asyncio.to_thread(f.close)
            finally:
                await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)

        resp = Response(generate())
        resp.headers["Content-Type"] = "video/mp4"
        resp.headers["Content-Disposition"] = f'attachment; filename="{vid}_{idx}_p{actual_qn}.mp4"'
        resp.headers["X-Accel-Buffering"] = "no"
        response_owns_cleanup = True
        return resp
    except Exception as exc:
        await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
        print(f"[DashProxy] proxy_download error: {exc}")
        return Response("Upstream error", status=502)
    finally:
        if not response_owns_cleanup:
            if tmpdir is not None:
                await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
            _download_limiter.release()


@dash_proxy_bp.route("/proxy/dash/<vid>/<int:idx>/<media_type>/<int:qn>/<int:cid>")
@rate_limit(**RATE_LIMITS["proxy"])
async def proxy_dash(vid, idx, media_type, qn, cid):
    """Range-capable DASH track proxy through the WARP SOCKS5 tunnel.

    Resolves the track URL from the cached dash JSON, validates it, and proxies
    the client's Range request upstream, faithfully emitting ``206 Partial
    Content`` with ``Content-Range``/``Content-Length``/``ETag`` so the sidx
    (SegmentBase indexRange) can drive byte-range seeking in dash.js.
    """
    if media_type not in ("video", "audio"):
        return Response("Bad Request: media_type must be video|audio", status=400)

    dash_data = await _load_dash_data(vid, idx)
    if not dash_data:
        return Response("Not Found", status=404)
    track = _lookup_track(dash_data, media_type, qn, cid)
    if not track:
        return Response("Not Found", status=404)

    url = track.get("base_url") or track.get("baseUrl")
    if not url:
        return Response("Not Found: track has no URL", status=404)

    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)
    if not _is_safe_dash_url(url):
        return Response("Forbidden: Invalid proxy target", status=403)

    creds = appconf["credential"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds.get("use_cred") else {}

    headers = await _build_dash_cdn_headers()

    # Forward runtime Range/conditional headers from the browser player
    for k, v in request.headers.items():
        if k.lower() in ["range", "if-range", "x-playback-session-id", "if-modified-since", "if-none-match"]:
            headers[k.lower()] = v

    if cookie_jar:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_jar.items())
        existing = headers.get("cookie", "")
        headers["cookie"] = f"{existing}; {cookie_str}".lstrip("; ") if existing else cookie_str

    proxy_url = Network.get_proxy()
    conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)

    try:
        await conn.connect()
        await conn.send_request()
        resp_headers = await conn.read_response_headers()

        if resp_headers.status_code in [403, 412, 514]:
            await conn.close()
            ticket = await TicketManager.get_ticket(force_refresh=True)
            if ticket:
                headers["x-bili-ticket"] = ticket
            else:
                headers.pop("x-bili-ticket", None)
            headers["session_id"] = TicketManager._generate_session_id()
            headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
            conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
            await conn.connect()
            await conn.send_request()
            resp_headers = await conn.read_response_headers()

        async def generate():
            try:
                async for chunk in conn.iter_chunks():
                    yield chunk
            except (CdnProtocolError, CdnTimeoutError):
                pass
            finally:
                await conn.close()

        proxy_resp = Response(generate(), status=resp_headers.status_code)
        proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
        proxy_resp.headers["X-Accel-Buffering"] = "no"
        proxy_resp.headers["Accept-Ranges"] = "bytes"

        for k, v in resp_headers.headers.items():
            if k in [
                "content-type",
                "content-length",
                "content-range",
                "etag",
                "last-modified",
                "cache-control",
            ]:
                proxy_resp.headers[k] = v

        if resp_headers.status_code in [200, 206]:
            current_ct = proxy_resp.headers.get("Content-Type", "").lower()
            if not current_ct or "application/octet-stream" in current_ct:
                proxy_resp.headers["Content-Type"] = (
                    "video/mp4" if media_type == "video" else "audio/mp4"
                )
        return proxy_resp
    except Exception as exc:
        print(f"[DashProxy] proxy_dash error: {exc}")
        await conn.close()
        return Response("Upstream error", status=502)


@app.route("/video/dash/<vid>/<int:idx>/manifest.mpd")
@rate_limit(**RATE_LIMITS["proxy"])
async def video_dash_manifest_view(vid, idx):
    """Serve the isoff-on-demand MPD manifest for a video part."""
    dash_data = await _load_dash_data(vid, idx)
    mpd_content = generate_vod_mpd(vid, idx, dash_data) if dash_data else None
    if not mpd_content:
        return Response("Not Found", status=404)
    return Response(mpd_content, content_type="application/dash+xml")


# ---------------------------------------------------------------------------
# Background download jobs (floating progress dialog + cancel)
# ---------------------------------------------------------------------------
# POST /download (fetch/XHR branch in app.py) creates a job and returns
# immediately with a job_id; the browser then polls
# GET /download/status/<job_id> for live progress ("downloading %", "muxing",
# server speed) and finally downloads GET /download/file/<job_id>.
# POST /download/cancel/<job_id> (or closing the page) aborts the server-side
# fetch/mux and deletes the temp files.
#
# Job IDs are unguessable (uuid4), so concurrent downloads from many users are
# isolated from each other. The registry is in-memory: Granian runs a single
# worker (see python/main.py — no workers= arg), so no cross-process state or
# Redis coordination is needed for the task handles.

# Max concurrent actively-downloading/muxing jobs server-wide; extras queue.
_JOB_SLOTS = 3
# Bound active workers (running or queued) so overload is rejected before a
# task is registered. Completed jobs retained for download do not count.
_MAX_ACTIVE_JOBS = _JOB_SLOTS * 4
# Finished files stay available for re-download (retry link) this long.
_JOB_READY_TTL = 15 * 60
# Error/cancelled job records are swept after this long.
_JOB_END_TTL = 5 * 60
# Active jobs with no progress update for this long are treated as stalled.
_JOB_STALL_TIMEOUT = 30 * 60

# Job states: queued -> resolving -> downloading -> muxing -> ready
#                                                 \-> error / cancelled


class _JobCancelled(Exception):
    """Internal control-flow signal: the user cancelled this download job."""


class DownloadCapacityError(RuntimeError):
    """Raised when the server-wide active download queue is full."""


class _DownloadJob:
    """Mutable per-download state. Only touched from the single event loop."""

    def __init__(self, vid: str, idx: int, qual: int):
        self.job_id = uuid.uuid4().hex
        self.vid = vid
        self.idx = idx
        self.qual = qual
        self.state = "queued"
        self.total_bytes = 0
        self.done_bytes = 0
        self.speed_bps = 0.0
        self.status_note = None  # transient user-facing note (e.g. retry backoff)
        self.filename = f"{vid}_{idx}.mp4"
        self.tmpdir = None
        self.outpath = None
        self.error = None
        now = time.time()
        self.created_at = now
        self.updated_at = now
        self.cancel_event = asyncio.Event()
        self.ffmpeg_proc = None
        self.task = None
        self._samples = deque()  # (monotonic_ts, done_bytes) for speed calc

    def touch(self):
        self.updated_at = time.time()

    def throw_if_cancelled(self):
        if self.cancel_event.is_set():
            raise _JobCancelled()

    def set_note(self, msg: str | None):
        """Set/clear the transient user-facing status note (sync callback)."""
        self.status_note = msg
        self.touch()

    def rewind_progress(self, n: int):
        """Rewind progress accounting (server ignored our resume Range)."""
        self.done_bytes = max(0, self.done_bytes - n)
        self._samples.clear()
        self.speed_bps = 0.0
        self.touch()

    def add_progress(self, n: int):
        """Sync per-chunk progress callback: updates bytes + rolling speed."""
        self.done_bytes += n
        now = time.monotonic()
        samples = self._samples
        samples.append((now, self.done_bytes))
        while samples and now - samples[0][0] > 4.0:
            samples.popleft()
        if len(samples) >= 2:
            dt = samples[-1][0] - samples[0][0]
            if dt > 0.2:
                self.speed_bps = (samples[-1][1] - samples[0][1]) / dt
        self.updated_at = time.time()

    def to_status(self) -> dict:
        percent = None
        if self.total_bytes > 0:
            percent = round(min(self.done_bytes / self.total_bytes, 1.0) * 100, 1)
        return {
            "job_id": self.job_id,
            "state": self.state,
            "percent": percent,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "speed_bps": round(self.speed_bps, 1),
            "filename": self.filename,
            "note": self.status_note,
            "error": self.error,
        }


_download_jobs: dict[str, _DownloadJob] = {}
_jobs_lock = asyncio.Lock()
_job_slots = asyncio.Semaphore(_JOB_SLOTS)


def _remove_job_tmpdir(job: _DownloadJob):
    tmpdir = job.tmpdir
    job.tmpdir = None
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _sweep_jobs():
    """Drop expired/terminal jobs and reap stalled ones. Called on every job endpoint hit."""
    now = time.time()
    async with _jobs_lock:
        for jid, job in list(_download_jobs.items()):
            age = now - job.updated_at
            if job.state == "ready" and age > _JOB_READY_TTL:
                _remove_job_tmpdir(job)
                del _download_jobs[jid]
            elif job.state in ("error", "cancelled") and age > _JOB_END_TTL:
                _remove_job_tmpdir(job)
                del _download_jobs[jid]
            elif job.state in ("queued", "resolving", "downloading", "muxing") and age > _JOB_STALL_TIMEOUT:
                # Stalled (e.g. hung upstream socket): ask the worker to abort.
                job.cancel_event.set()
                if job.task is not None and job.task.done():
                    _remove_job_tmpdir(job)
                    del _download_jobs[jid]
        # Hard registry cap: drop oldest terminal jobs first.
        if len(_download_jobs) > 100:
            terminal = sorted(
                ((j.updated_at, jid) for jid, j in _download_jobs.items()
                 if j.state in ("ready", "error", "cancelled"))
            )
            for _, jid in terminal[: len(_download_jobs) - 100]:
                _remove_job_tmpdir(_download_jobs[jid])
                del _download_jobs[jid]


async def _get_job(job_id: str) -> _DownloadJob | None:
    if not job_id or len(job_id) > 64:
        return None
    async with _jobs_lock:
        return _download_jobs.get(job_id)


async def create_download_job(vid: str, idx: int, qual: int) -> str:
    """Register a download job and launch its background worker. Returns job_id."""
    if not appconf["proxy"]["use_proxy"]:
        raise RuntimeError("Proxying is disabled")
    await _sweep_jobs()
    async with _jobs_lock:
        active_jobs = sum(
            job.state not in ("ready", "error", "cancelled") for job in _download_jobs.values()
        )
        if active_jobs >= _MAX_ACTIVE_JOBS:
            raise DownloadCapacityError("Download queue is full; please try again later")
        job = _DownloadJob(vid, idx, qual)
        job.task = asyncio.ensure_future(_run_download_job(job))
        _download_jobs[job.job_id] = job
    return job.job_id


async def cancel_download_job(job_id: str) -> dict | None:
    """Signal a job to abort. Returns its status dict, or None if unknown."""
    job = await _get_job(job_id)
    if job is None:
        return None
    if job.state not in ("ready", "error", "cancelled"):
        job.cancel_event.set()
        proc = job.ffmpeg_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
    return job.to_status()


async def _peek_content_length(url: str, headers: dict, proxy_url: str,
                               cancel_event: asyncio.Event | None = None) -> int:
    """Fetch only response headers to learn a track's size (0 if unknown).

    Opens a throwaway connection and closes it without reading the body.
    Raises RuntimeError if the track exceeds the per-track size cap.
    """
    try:
        conn, resp_headers = await _open_cdn_track(url, headers, proxy_url)
    except Exception as exc:
        print(f"[DashProxy] size peek failed: {exc}")
        return 0
    try:
        if resp_headers.status_code not in (200, 206):
            return 0
        cl = (resp_headers.headers or {}).get("content-length")
        if not cl:
            return 0
        try:
            size = int(cl)
        except (TypeError, ValueError):
            return 0
        if size > _MAX_DOWNLOAD_TRACK_BYTES:
            raise RuntimeError("track exceeds server size limit")
        return max(size, 0)
    finally:
        await conn.close()


async def _run_dash_job(job: _DownloadJob, dash_data: dict):
    """Download best video (<=1080p cap) + audio tracks, then ffmpeg-mux."""
    max_qn = min(job.qual, _FREE_DOWNLOAD_MAX_QN) if job.qual > 0 else _FREE_DOWNLOAD_MAX_QN
    video, audio = _pick_download_tracks(dash_data, max_qn)
    if not video or not audio:
        raise RuntimeError("no suitable DASH tracks for download")
    vurl = video.get("base_url") or video.get("baseUrl")
    aurl = audio.get("base_url") or audio.get("baseUrl")
    if not vurl or not aurl:
        raise RuntimeError("track has no URL")
    if not _is_safe_dash_url(vurl) or not _is_safe_dash_url(aurl):
        raise RuntimeError("invalid CDN target")

    proxy_url = Network.get_proxy()
    headers = await _build_dash_cdn_headers()

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix=f"miku_dl_{job.job_id}_")
    job.tmpdir = tmpdir
    vpath = os.path.join(tmpdir, "video.m4s")
    apath = os.path.join(tmpdir, "audio.m4s")
    outpath = os.path.join(tmpdir, "out.mp4")
    job.outpath = outpath
    actual_qn = int(video.get("id") or max_qn)
    job.filename = f"{job.vid}_{job.idx}_p{actual_qn}.mp4"

    vsize = await _peek_content_length(vurl, headers, proxy_url)
    job.throw_if_cancelled()
    asize = await _peek_content_length(aurl, headers, proxy_url)
    job.throw_if_cancelled()
    job.total_bytes = vsize + asize
    job.state = "downloading"
    job.touch()

    n = await _download_track_to_file(
        vurl, headers, proxy_url, vpath,
        progress_cb=job.add_progress, cancel_event=job.cancel_event,
        note_cb=job.set_note, rewind_cb=job.rewind_progress,
    )
    if n == _DOWNLOAD_CANCELLED:
        raise _JobCancelled()
    if n < 0:
        raise RuntimeError("video track download failed")
    job.throw_if_cancelled()
    n = await _download_track_to_file(
        aurl, headers, proxy_url, apath,
        progress_cb=job.add_progress, cancel_event=job.cancel_event,
        note_cb=job.set_note, rewind_cb=job.rewind_progress,
    )
    if n == _DOWNLOAD_CANCELLED:
        raise _JobCancelled()
    if n < 0:
        raise RuntimeError("audio track download failed")

    job.state = "muxing"
    job.set_note(None)
    job.speed_bps = 0.0
    job.touch()
    await _mux_tracks(vpath, apath, outpath, job=job)
    for p in (vpath, apath):
        try:
            await asyncio.to_thread(os.remove, p)
        except Exception:
            pass


async def _run_durl_job(job: _DownloadJob):
    """Download fallback for durl-only videos: progressive MP4, no mux step."""
    from api import video as video_mod

    try:
        v = video_mod.Video(bvid=job.vid, credential=appcred)
    except Exception:
        raise RuntimeError("invalid video ID") from None
    try:
        play_data = await asyncio.wait_for(
            video_get_dash_for_qn(v, job.idx), timeout=DASH_FETCH_TIMEOUT
        )
    except Exception as exc:
        print(f"[DashProxy] job {job.job_id} durl fetch failed: {exc}")
        play_data = None
    job.throw_if_cancelled()
    try:
        url, qn, ext = await _resolve_durl_download(
            v, job.vid, job.idx, job.qual, play_data=play_data
        )
    except _DurlResolveError as exc:
        raise RuntimeError(str(exc)) from None
    if not _is_safe_dash_url(url):
        raise RuntimeError("invalid CDN target")

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix=f"miku_dl_{job.job_id}_")
    job.tmpdir = tmpdir
    outpath = os.path.join(tmpdir, f"out{ext}")
    job.outpath = outpath
    job.filename = f"{job.vid}_{job.idx}_p{qn}{ext}"

    headers = await _build_dash_cdn_headers()
    proxy_url = Network.get_proxy()
    job.total_bytes = await _peek_content_length(url, headers, proxy_url)
    job.throw_if_cancelled()
    job.state = "downloading"
    job.touch()
    n = await _download_track_to_file(
        url, headers, proxy_url, outpath,
        progress_cb=job.add_progress, cancel_event=job.cancel_event,
        note_cb=job.set_note, rewind_cb=job.rewind_progress,
    )
    if n == _DOWNLOAD_CANCELLED:
        raise _JobCancelled()
    if n < 0:
        raise RuntimeError("progressive download failed")


async def _run_download_job(job: _DownloadJob):
    """Background worker: resolve -> download (-> mux) -> ready. Never raises."""
    acquired = False
    try:
        await _job_slots.acquire()
        acquired = True
        job.throw_if_cancelled()
        job.state = "resolving"
        job.touch()
        dash_data = await _load_dash_data(job.vid, job.idx)
        job.throw_if_cancelled()
        if has_valid_dash_tracks(dash_data):
            await _run_dash_job(job, dash_data)
        else:
            await _run_durl_job(job)
        job.throw_if_cancelled()
        if not job.outpath or not os.path.exists(job.outpath):
            raise RuntimeError("finished file missing")
        job.state = "ready"
        job.set_note(None)
        job.speed_bps = 0.0
        job.touch()
        print(f"[DashProxy] download job {job.job_id} ready: {job.filename}")
    except _JobCancelled:
        job.state = "cancelled"
        job.touch()
        print(f"[DashProxy] download job {job.job_id} cancelled")
    except Exception as exc:
        job.state = "error"
        job.error = str(exc)[:300]
        job.touch()
        print(f"[DashProxy] download job {job.job_id} error: {exc}")
    finally:
        if acquired:
            _job_slots.release()
        if job.state in ("error", "cancelled"):
            await asyncio.to_thread(_remove_job_tmpdir, job)
        job.touch()


@dash_proxy_bp.route("/download/status/<job_id>")
@rate_limit(**RATE_LIMITS["proxy"])
async def download_status(job_id):
    """Live progress for a download job (polled by the floating dialog)."""
    await _sweep_jobs()
    job = await _get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found or expired"}), 404
    return jsonify(job.to_status())


@dash_proxy_bp.route("/download/file/<job_id>")
@rate_limit(**RATE_LIMITS["proxy"])
async def download_file(job_id):
    """Stream the finished file as an attachment. Kept until TTL for retries."""
    await _sweep_jobs()
    job = await _get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found or expired"}), 404
    if job.state != "ready":
        return jsonify(job.to_status()), 409
    outpath = job.outpath
    if not outpath or not os.path.exists(outpath):
        return jsonify({"error": "file no longer available"}), 410
    try:
        size = await asyncio.to_thread(os.path.getsize, outpath)
    except OSError:
        return jsonify({"error": "file no longer available"}), 410

    async def generate():
        f = await asyncio.to_thread(open, outpath, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(f.read, 512 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

    resp = Response(generate())
    resp.headers["Content-Type"] = "video/mp4"
    resp.headers["Content-Length"] = str(size)
    resp.headers["Content-Disposition"] = f'attachment; filename="{job.filename}"'
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@dash_proxy_bp.route("/download/cancel/<job_id>", methods=["POST"])
@csrf_protect()
@rate_limit(**RATE_LIMITS["normal"])
async def download_cancel(job_id):
    """Abort a running/queued job and delete its temp files."""
    await _sweep_jobs()
    status = await cancel_download_job(job_id)
    if status is None:
        return jsonify({"error": "job not found or expired"}), 404
    return jsonify(status)
