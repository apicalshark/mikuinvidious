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
from urllib.parse import urlparse

import orjson
from quart import Blueprint, Response, request
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
from stream import CdnConnection, CdnProtocolError, CdnTimeoutError

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


async def video_get_dash_for_qn(vi, idx, ep_id=None) -> dict:
    """Fetch canonical DASH play info, returning a ``{"dash":..., "support_formats":...}`` dict.

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
        if data and isinstance(data, dict) and (data.get("dash") or data.get("support_formats")):
            return {
                "code": data.get("code", 0),
                "dash": data.get("dash") or {},
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
        if isinstance(pgc, dict) and pgc.get("code") == 0:
            result = pgc.get("result") or {}
            dash = result.get("video_info", {}).get("dash") if isinstance(result.get("video_info"), dict) else result.get("dash")
            if not dash and isinstance(result, dict):
                dash = result.get("dash")
            support_formats = result.get("support_formats", []) if isinstance(result, dict) else []
            return {
                "code": 0,
                "dash": dash or {},
                "support_formats": support_formats,
                "quality": result.get("quality", 0) if isinstance(result, dict) else 0,
                "accept_quality": result.get("accept_quality", []) if isinstance(result, dict) else [],
            }
        print(f"[DashProxy] PGC fallback returned code {pgc.get('code')}")
        return {"code": pgc.get("code", -1), "message": pgc.get("message", "PGC playurl failed")}
    except Exception as exc:
        print(f"[DashProxy] PGC fallback failed for {v.get_bvid()}: {exc}")
        return {"code": -1, "message": str(exc)}


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

    # --- Video adaptation set ---
    videos = _normalize_track_urls(dash.get("video", []))
    if videos:
        mpd.append('    <AdaptationSet id="1" contentType="video" mimeType="video/mp4" segmentAlignment="true" subsegmentAlignment="true" startWithSAP="1">')
        for video_track in videos:
            qn = video_track.get("id")
            cid = video_track.get("codecid", 0)
            bandwidth = video_track.get("bandwidth", 0)
            width = video_track.get("width", 0)
            height = video_track.get("height", 0)
            frame_rate = video_track.get("frameRate") or video_track.get("frame_rate") or "24"
            codecs = video_track.get("codecs") or "avc1.64001F"
            if qn is None or not video_track.get("SegmentBase"):
                continue
            sb = video_track["SegmentBase"]
            init_range = _sb_initialization_range(sb)
            index_range = sb.get("indexRange", "")
            index_exact = ' indexRangeExact="true"' if index_range else ""
            if not index_range:
                continue
            mpd.append(
                f'      <Representation id="video_{qn}_{cid}" codecs="{codecs}" bandwidth="{bandwidth}" width="{width}" height="{height}" frameRate="{frame_rate}">'
            )
            mpd.append(f"        <BaseURL>/proxy/dash/{vid}/{idx}/video/{qn}/{cid}</BaseURL>")
            mpd.append(f'        <SegmentBase indexRange="{index_range}"{index_exact}>')
            mpd.append(f'          <Initialization range="{init_range}"/>')
            mpd.append("        </SegmentBase>")
            mpd.append("      </Representation>")
        mpd.append("    </AdaptationSet>")

    # --- Audio adaptation set ---
    audios = _normalize_track_urls(dash.get("audio", []))
    if not audios:
        audios = _normalize_track_urls((dash.get("dolby") or {}).get("audio", []))
    if not audios:
        audios = _normalize_track_urls((dash.get("flac") or {}).get("audio", []))
    if audios:
        mpd.append('    <AdaptationSet id="2" contentType="audio" mimeType="audio/mp4" segmentAlignment="true" subsegmentAlignment="true" startWithSAP="1">')
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


async def _download_track_to_file(url: str, headers: dict, proxy_url: str, dest: str) -> int:
    """Download a full DASH track body to ``dest`` via CdnConnection. Returns byte count."""
    conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
    try:
        await conn.connect()
        await conn.send_request()
        resp_headers = await conn.read_response_headers()
        if resp_headers.status_code in (403, 412, 514):
            await conn.close()
            ticket = await TicketManager.get_ticket(force_refresh=True)
            if ticket:
                headers["x-bili-ticket"] = ticket
            else:
                headers.pop("x-bili-ticket", None)
            conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
            await conn.connect()
            await conn.send_request()
            resp_headers = await conn.read_response_headers()
        if resp_headers.status_code not in (200, 206):
            await conn.close()
            return -1
        total = 0
        with open(dest, "wb") as f:
            async for chunk in conn.iter_chunks():
                f.write(chunk)
                total += len(chunk)
        await conn.close()
        return total
    except Exception:
        await conn.close()
        return -1


async def _mux_tracks(video_path: str, audio_path: str, out_path: str) -> None:
    """Remux video + audio tracks into a single faststart MP4 with ffmpeg (-c copy)."""
    ffmpeg = shutil.which("ffmpeg")
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
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg mux failed: " + (stderr or b"").decode(errors="replace")[-600:])


@dash_proxy_bp.route("/proxy/download/<vid>/<int:idx>/<int:qual>")
@rate_limit(**RATE_LIMITS["proxy"])
async def proxy_download(vid, idx, qual):
    """Mux the best DASH video (<=1080p anonymous cap) + audio into one MP4.

    Downloads both tracks through the WARP tunnel, remuxes with ffmpeg into a
    single faststart MP4, and streams it back as an attachment.
    """
    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)

    max_qn = min(qual, _FREE_DOWNLOAD_MAX_QN) if qual > 0 else _FREE_DOWNLOAD_MAX_QN
    dash_data = await _load_dash_data(vid, idx)
    if not dash_data or not dash_data.get("dash"):
        return Response("Not Found", status=404)

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

    tmpdir = tempfile.mkdtemp(prefix="miku_dl_")
    vpath = os.path.join(tmpdir, "video.m4s")
    apath = os.path.join(tmpdir, "audio.m4s")
    outpath = os.path.join(tmpdir, "out.mp4")
    try:
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
                with open(outpath, "rb") as f:
                    while True:
                        chunk = f.read(512 * 1024)
                        if not chunk:
                            break
                        yield chunk
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        resp = Response(generate())
        resp.headers["Content-Type"] = "video/mp4"
        resp.headers["Content-Disposition"] = f'attachment; filename="{vid}_{idx}_p{actual_qn}.mp4"'
        resp.headers["X-Accel-Buffering"] = "no"
        return resp
    except Exception as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"[DashProxy] proxy_download error: {exc}")
        return Response("Upstream error", status=502)


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
