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
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections import deque
from urllib.parse import parse_qs, urlparse

import aiofiles
import orjson
from csrf import csrf_protect
from quart import Blueprint, Response, jsonify, redirect, request
from rate_limit import RATE_LIMITS, get_client_ip, rate_limit
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

# Per-attempt deadline for reaching a DASH CDN URL (connect + request +
# response headers). Healthy edges answer in well under a second; a stalled
# edge must fail over to a backup URL instead of hanging. Kept at 5s (not
# lower): WARP SOCKS5 + TLS handshakes under load need headroom, and each
# "stalled" line below is this many seconds of pure zero-progress waste.
DASH_ATTEMPT_TIMEOUT = 5.0

# Mid-body watchdog for DASH track proxying. The handshake deadline above only
# covers connect + request + response headers; some edges answer headers in ms
# and then tarpit the body (~100 KB/s on a sick cache object), which used to
# hang until dash.js abandoned the request (bare 500s, no failover). Past
# DASH_BODY_GRACE seconds, a cumulative average under DASH_BODY_FLOOR_KBPS
# aborts the edge and retries the remaining Range on the next mirror.
# The floor sits well above tarpit speeds but far below healthy edge
# throughput, so sick mirrors fail over long before dash.js' fragment timeout
# fires (see player.js) instead of burning all of its retries on one edge.
DASH_BODY_GRACE = 3.0
DASH_BODY_FLOOR_KBPS = 100

# Playback fail-fast budgets. dash.js abandons slow fragments itself (~7s,
# proven live) and retries up to 5x with ABR downshift — so the proxy must
# fail a fragment fast instead of heroically churning mirrors for longer
# than the client waits (abandoned work + spins). Downloads keep the patient
# 8s/all-mirror budgets: no client intelligence there, the server must
# succeed. Caps: 2 mirrors per playback request, 4s handshakes.
DASH_PLAYBACK_ATTEMPT_TIMEOUT = 4.0
_PLAYBACK_MAX_MIRRORS = 2

# Idle-stall trip for response bodies: no bytes at all for this long means a
# wedged edge (the cumulative-average watchdog above can't see pure idleness
# — a 7s stall followed by a dribble reads as merely "slow"). Shared by
# playback and downloads; both resume byte-exact, so tripping early only
# costs a handshake.
DASH_BODY_IDLE_TIMEOUT = 5.0

# Raw CDN domains allowed through the DASH track proxy.
_ALLOWED_DASH_DOMAINS = [
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
    ".mountaintoys.cn",
]


def _is_mcdn_url(url: str) -> bool:
    """Detect Bilibili M-CDN (PCDN) edge nodes (PipePipe b3303f4).

    Typically ``*.mcdn.bilivideo.cn`` / ``*.edge.mountaintoys.cn``. They are
    built for the web player: flaky HEAD, non-browser 403s, short-lived
    signatures — bad first choice for proxying/downloads.
    """
    return "mcdn.bilivideo" in url or "mountaintoys" in url or "os=mcdn" in url


def _pick_stable_dash_url(primary: str | None, backups) -> str | None:
    """Prefer a non-M-CDN URL (PipePipe ``pickStableStreamUrl``).

    Keeps the primary when it is stable; otherwise returns the first stable
    backup; falls back to the primary when everything is M-CDN.
    """
    if primary and not _is_mcdn_url(primary):
        return primary
    for cand in backups or []:
        if isinstance(cand, str) and cand and not _is_mcdn_url(cand):
            return cand
    return primary


def _is_safe_dash_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    if not any(hostname == d.lstrip(".") or hostname.endswith(d) for d in _ALLOWED_DASH_DOMAINS):
        return False
    return True


async def _is_safe_dash_url_async(url: str) -> bool:
    """Full DASH URL check: domain allowlist + private-IP DNS reject.

    Mirrors ``proxy.is_safe_proxy_url`` so the DASH track proxy, muxed
    downloads, and background jobs enforce the same SSRF bar as the
    progressive ``/proxy/video/`` path (L1). The sync
    :func:`_is_safe_dash_url` above stays as a fast pre-filter for
    candidate lists; call this before connecting/fetching.
    """
    if not _is_safe_dash_url(url):
        return False
    try:
        from dns_cache import resolve_host

        hostname = urlparse(url).hostname
        if not hostname:
            return False
        addr_infos = await resolve_host(hostname)
        for _, _, _, _, sockaddr in addr_infos:
            ip_obj = ipaddress.ip_address(sockaddr[0])
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
            ):
                return False
    except socket.gaierror:
        return False
    except Exception:
        return False
    return True


# Bilibili video IDs for Content-Disposition filenames (M2). Quart route
# params allow `"`, `;`, CR/LF-encoded chars that would break out of the
# quoted filename and inject response headers (CWE-113). Validate strictly
# and sanitize defensively at emission.
_VID_RE = re.compile(r"^(BV[a-zA-Z0-9]{10}|av\d{1,20})$")


def _is_valid_vid(vid: str) -> bool:
    return bool(vid) and bool(_VID_RE.match(vid))


def _safe_vid(vid: str) -> str:
    """Strip anything outside ``[A-Za-z0-9_-]`` for safe header embedding."""
    return "".join(c for c in (vid or "") if c.isalnum() or c in ("_", "-"))[:64]


def _safe_download_filename(vid: str, idx: int, qn: int, ext: str = ".mp4") -> str:
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = 0
    try:
        qn = int(qn)
    except (TypeError, ValueError):
        qn = 0
    ext = ext if ext in (".mp4", ".flv") else ".mp4"
    return f"{_safe_vid(vid) or 'video'}_{idx}_p{qn}{ext}"


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


# Bilibili paywall codes from the UGC playurl endpoint (player bundle
# pay-conf enum: 87005 Uni_NeedPay, 87007 Old_Charing_NeedPay,
# 87008 Charing_NeedPay). Anonymous sessions can never clear these, so they
# short-circuit to a paywall marker instead of the PGC fallback.
PAYWALL_CODES = frozenset({87005, 87007, 87008})


def _paywall_short_circuit(exc: Exception, ep_id) -> dict | None:
    """Return a paywall marker for charged UGC, else None (keep PGC fallback).

    Anonymous sessions can never clear these codes, and the PGC endpoint
    cannot serve UGC charging content — except when an ep_id is present,
    where premium PGC routing may still apply.
    """
    if ep_id is None and getattr(exc, "code", None) in PAYWALL_CODES:
        return {"code": exc.code, "message": "paywall", "paywall": True}
    return None


# In-flight DASH playurl fetches, keyed (bvid, idx, ep_id, cid). The video
# page fires a background precache AND the player partial fetches the same
# playurl ~immediately after; without coalescing every first view costs two
# upstream playurl calls (double risk-control exposure). Entries live only
# for the fetch duration — result caching stays in Redis (miku_dash_*).
_dash_fetch_inflight: dict[tuple, list] = {}
_dash_fetch_lock = asyncio.Lock()


async def video_get_dash_for_qn(vi, idx, ep_id=None, cid=None) -> dict:
    """Fetch canonical DASH play info, returning a ``{"dash":..., "durl":..., "support_formats":...}`` dict.

    Concurrent identical fetches coalesce behind one upstream call
    (singleflight). Cancellation-safe: a timed-out owner settles waiters
    with a retryable error instead of leaving them hanging.

    Uses :meth:`api.video.Video.get_dash_playurl` (wbi-signed UGC endpoint).
    Falls back to the PGC playurl endpoint (not wbi-signed) when the UGC path
    returns an error / empty dash, which happens for premium (PGC) content.

    UGC detail endpoints fake-404 PGC-only BVs (``-404`` empty-result,
    Bilibili's "啥都木有") under
    risk control; that must not veto the PGC path, which needs no UGC cid
    at all. Pass the known PGC ``cid`` (e.g. from season data) when
    available — it is used for the UGC attempt and PGC params alike.
    """
    get_bvid = getattr(vi, "get_bvid", None)
    bvid = get_bvid() if callable(get_bvid) else vi
    key = (bvid, idx, ep_id, cid)

    fut, owner = await _dash_fetch_join(key)
    if not owner:
        return await asyncio.shield(fut)

    try:
        data = await _video_get_dash_for_qn_uncached(vi, idx, ep_id=ep_id, cid=cid)
    except asyncio.CancelledError:
        # wait_for timeouts on the owner must not hang joiners: hand them a
        # plain error (their own except-Exception paths degrade to the
        # progressive fallback) and let the cancellation propagate.
        _dash_fetch_settle(key, ok=False, payload=RuntimeError("dash playurl fetch cancelled"))
        raise
    except Exception as e:
        _dash_fetch_settle(key, ok=False, payload=e)
        raise
    _dash_fetch_settle(key, ok=True, payload=data)
    return data


async def _dash_fetch_join(key: tuple):
    """Register on the in-flight fetch for *key*; returns (future, is_owner)."""
    async with _dash_fetch_lock:
        entry = _dash_fetch_inflight.get(key)
        if entry is None:
            fut = asyncio.get_running_loop().create_future()
            _dash_fetch_inflight[key] = [fut, 0]
            return fut, True
        entry[1] += 1
        return entry[0], False


def _dash_fetch_settle(key: tuple, ok: bool, payload):
    """Settle joiners of *key*; no-op when nobody joined (avoids unretrieved-exception noise)."""
    entry = _dash_fetch_inflight.pop(key, None)
    if entry is not None:
        fut, waiters = entry
        if waiters and not fut.done():
            if ok:
                fut.set_result(payload)
            else:
                fut.set_exception(payload)


async def _video_get_dash_for_qn_uncached(vi, idx, ep_id=None, cid=None) -> dict:
    """Single upstream DASH play-info fetch (see :func:`video_get_dash_for_qn`)."""
    from api import video

    v = vi if isinstance(vi, video.Video) else video.Video(bvid=vi, credential=appcred)
    if cid is None:
        try:
            cid = await v.get_cid(idx)
        except Exception as exc:
            print(f"[DashProxy] cid resolve failed for {v.get_bvid()}: {exc}")
            cid = None
            if ep_id is None:
                return {"code": -1, "message": f"failed to resolve cid: {exc}"}
    if ep_id is None:
        ep_id = await _extract_ep_id(v)

    # 1) UGC wbi playurl (canonical path) — needs a UGC cid.
    if cid is not None:
        try:
            data = await v.get_dash_playurl(page_index=idx, cid=cid, qn=120)
            if isinstance(data, dict) and data.get("v_voucher"):
                # Gaia risk gate survived the avoidance retry (no captcha UI
                # server-side) — fall through to the PGC fallback below.
                print(f"[DashProxy] UGC playurl risk-gated (v_voucher) for {v.get_bvid()}; trying PGC fallback")
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
            paywalled = _paywall_short_circuit(exc, ep_id)
            if paywalled is not None:
                return paywalled

    # 2) PGC playurl fallback (premium / non-wbi endpoint)
    if ep_id is None and cid is None:
        return {"code": -1, "message": "no playable source (UGC failed, no ep_id/cid for PGC fallback)"}
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
            "qn": 120,
            "fnval": 4048,
            "fourk": 1,
            "platform": "html5",
            "high_quality": 1,
        }
        if cid is not None:
            pgc_params["cid"] = cid
        if ep_id:
            pgc_params["ep_id"] = ep_id
        pgc_raw = await client.get(
            "https://api.bilibili.com/pgc/player/web/playurl",
            params=pgc_params,
            cookies=cookies,
            headers={
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
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
        if code == -10403:
            # PGC VIP/region gate ("大会员专享限制"): same paywall marker the
            # UGC 8700x path produces, so the player renders the "Restricted
            # video" overlay instead of spinning forever.
            return {"code": code, "message": msg, "paywall": True}
        return {"code": code, "message": msg}
    except Exception as exc:
        print(f"[DashProxy] PGC fallback failed for {v.get_bvid()}: {exc}")
        return {"code": -1, "message": str(exc)}


def has_valid_dash_tracks(dash_data: dict | None) -> bool:
    """Check whether a dash payload has at least one playable track.

    A track is playable when it carries a ``SegmentBase`` with an
    ``indexRange`` — dash.js needs the sidx range to compute segment
    byte-offsets. Some UGC uploads are ``durl``-only (progressive MP4, no
    ``dash`` node at all); those must fall back to the
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
                    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
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


async def _fetch_single_durl(v, idx: int, qn: int, ep_id=None, cid=None) -> dict | None:
    """Fetch one progressive (``durl``) playurl for a quality level.

    Uses the non-wbi ``/x/player/playurl`` endpoint (per-quality ``qn``),
    which still returns a ``durl`` node for durl-only uploads. Returns the
    ``data``-shaped dict on success, else None. When UGC cid resolution is
    gated but an ``ep_id`` is known, the PGC endpoint is tried directly.
    """
    from api.client import Api

    if cid is None:
        try:
            cid = await v.get_cid(idx)
        except Exception:
            if ep_id is None:
                return None
            return await _fetch_single_durl_pgc(
                v,
                {"avid": v.get_aid(), "qn": qn, "platform": "html5", "high_quality": 1},
                ep_id=ep_id,
            )
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


def _select_durl_results(results: list, play_data: dict | None) -> list:
    """Collapse same-file dupes to the truthful entry (or [] when empty).

    PGC durl endpoints answer every quality request with the same
    single-quality node (e.g. everything resolves to qn 32 wearing each
    other's labels — seen live on bangumi ep98604). When any request resolves
    exactly, keep one entry per actual quality, preferring exact matches.
    Otherwise keep only the best file served. Relabel mismatches from
    support_formats by actual quality when available. Temp
    ``_requested_qn`` keys are stripped before returning.
    """
    results = [r for r in results if r]
    fmt_desc = {}
    for f in ((play_data or {}).get("support_formats") or []):
        if not isinstance(f, dict) or f.get("quality") is None:
            continue
        try:
            desc = f.get("new_description") or f.get("display_desc")
            if desc:
                fmt_desc[int(f["quality"])] = desc
        except (TypeError, ValueError):
            continue
    if any(r.get("quality") == r.get("_requested_qn") for r in results):
        by_quality = {}
        for r in results:
            quality = r.get("quality")
            if quality not in by_quality or quality == r.get("_requested_qn"):
                by_quality[quality] = r
        picked = list(by_quality.values())
    else:
        picked = [max(results, key=lambda r: r.get("quality", 0))] if results else []
    for r in picked:
        if r.get("quality") != r.get("_requested_qn") and fmt_desc.get(r.get("quality")):
            r["new_description"] = fmt_desc[r["quality"]]
        r.pop("_requested_qn", None)
    return picked


async def fetch_durl_supported_src(
    v,
    vid: str,
    idx: int,
    play_data: dict | None = None,
    ep_id=None,
    max_qualities: int = 4,
    force: bool = False,
    cid=None,
) -> list:
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
            node = await _fetch_single_durl(v, idx, qn, ep_id=ep_id, cid=cid)
        entry = await _cache_durl_entry(vid, idx, qn, desc, node)
        if entry is None:
            return None
        # PGC durl endpoints answer every quality request with the same
        # single-quality node (e.g. everything resolves to qn 32 wearing
        # each other's labels). Track the request so selection can prefer
        # exact matches and relabel mismatches by the quality actually served.
        entry["_requested_qn"] = qn
        return entry

    results = await asyncio.gather(*[resolve_one(qn, desc) for qn, desc in qualities])
    supported = sorted(
        _select_durl_results(results, play_data),
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


# Per-video in-flight dash fetches (singleflight) + negative-cache window for
# failed fetches, so concurrent manifest/track hits share one playurl call.
_dash_inflight: dict[str, asyncio.Future] = {}
_dash_inflight_lock = asyncio.Lock()
_DASH_MISS_TTL = 30


async def _fetch_and_cache_dash_data(vid, idx, key):
    """Fetch dash play info and cache it. Returns the dash dict or None."""
    from api import video

    v = video.Video(bvid=vid, credential=appcred)
    try:
        data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=DASH_FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"[DashProxy] Fetching dash for {vid}:{idx} timed out")
        data = None
    if not data or not data.get("dash"):
        try:
            await appredis.setex(f"{key}:miss", _DASH_MISS_TTL, "1")
        except Exception:
            pass
        return None
    await appredis.setex(key, DASH_CACHE_TTL, orjson.dumps(data))
    return data


async def _load_dash_data(vid, idx) -> dict | None:
    """Read cached dash JSON, or fetch + cache it. Returns the dash dict or None.

    Concurrent callers for the same video share one in-flight fetch
    (singleflight), and failed fetches are negative-cached briefly so a
    dead video does not stampede playurl on every manifest/track hit.

    Cached entries whose signed CDN URLs have expired (see
    :func:`_dash_data_urls_stale`, the server-side ``isUrlExpired``) are
    dropped and re-fetched instead of serving known-dead URLs.
    """
    key = f"miku_dash_{vid}_{idx}"
    cached = await appredis.get(key)
    if cached:
        data = safe_json_loads(cached)
        if isinstance(data, dict):
            if _dash_data_urls_stale(data):
                print(f"[DashProxy] cached playurl for {vid}:{idx} expired, refetching")
                try:
                    await appredis.delete(key)
                except Exception:
                    pass
            else:
                return data
    if await appredis.get(f"{key}:miss"):
        return None
    async with _dash_inflight_lock:
        fut = _dash_inflight.get(key)
        if fut is None:
            fut = _dash_inflight[key] = asyncio.get_running_loop().create_future()
            fetch = True
        else:
            fetch = False
    if not fetch:
        return await fut
    try:
        result = await _fetch_and_cache_dash_data(vid, idx, key)
    except Exception as exc:
        print(f"[DashProxy] dash fetch failed for {vid}:{idx}: {exc}")
        result = None
    async with _dash_inflight_lock:
        _dash_inflight.pop(key, None)
        if not fut.done():
            fut.set_result(result)
    return result


async def _refresh_dash_data(vid, idx) -> dict | None:
    """Drop cached (and negative-cached) dash JSON and re-fetch fresh upstream.

    The official player's ``prefetchPlayUrl`` recovery: signed CDN URLs die
    after ~2h, so retrying the same cached URL set can never succeed. All
    mirrors of one playurl response share the same expiry window, which is
    why mirror failover alone is not enough. Concurrent refreshers share one
    upstream fetch via :func:`_load_dash_data` singleflight. Returns fresh
    dash_data or None.
    """
    key = f"miku_dash_{vid}_{idx}"
    try:
        await appredis.delete(key)
        await appredis.delete(f"{key}:miss")
    except Exception:
        pass
    return await _load_dash_data(vid, idx)


# Signature expiry params, in the official player's isUrlExpired check order
# (core.*.js): expires | wsTime | txTime | um_deadline | deadline. The spell
# varies by CDN edge, so all five must be tried.
_EXPIRY_PARAM_NAMES = ("expires", "wsTime", "txTime", "um_deadline", "deadline")

# Refresh playurl this far ahead of actual expiry: segments in flight when the
# signature dies would 403 mid-body.
_DASH_EXPIRY_SKEW = 60


def _playurl_url_expiry(url: str | None) -> int | None:
    """Unix expiry from a CDN URL's signature params, else None.

    Server-side mirror of the player's ``isUrlExpired`` param chain. Unknown
    schemes (no stamp found) return None and are never treated as stale.
    """
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except Exception:
        return None
    for name in _EXPIRY_PARAM_NAMES:
        for raw in params.get(name) or []:
            try:
                ts = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if ts > 0:
                return ts
    return None


def _dash_data_urls_stale(dash_data: dict | None, skew: int = _DASH_EXPIRY_SKEW) -> bool:
    """True when the cached tracks' signatures are (nearly) expired.

    Uses the minimum stamp across playable tracks; all tracks of one playurl
    response share the same signing window in practice. No stamp anywhere
    means "unknown, assume fresh" — never nuke the cache on guesswork.
    """
    if not dash_data or not isinstance(dash_data, dict):
        return False
    dash = dash_data.get("dash") or {}
    if not isinstance(dash, dict):
        return False
    best: int | None = None
    for key in ("video", "audio"):
        for t in _normalize_track_urls(dash.get(key)):
            if not isinstance(t, dict):
                continue
            ts = _playurl_url_expiry(t.get("base_url") or t.get("baseUrl"))
            if ts is not None and (best is None or ts < best):
                best = ts
    if best is None:
        return False
    return time.time() + skew > best


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
        mpd.append(
            f'    <AdaptationSet id="{as_id}" contentType="audio" mimeType="audio/mp4" segmentAlignment="true" subsegmentAlignment="true" startWithSAP="1">'
        )
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
_DOWNLOAD_TOO_LARGE = -2

# Fallback per-track download size cap (MB) when the hoster did not configure one.
_DEFAULT_MAX_DOWNLOAD_MB = 1024


def _max_download_size_mb() -> int:
    """Hoster-configured per-track download cap in MB (``[site] max_download_size_mb``)."""
    try:
        return max(int(appconf["site"].get("max_download_size_mb", _DEFAULT_MAX_DOWNLOAD_MB)), 1)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_DOWNLOAD_MB


def _max_download_track_bytes() -> int:
    """Hoster-configured per-track download cap in bytes."""
    return _max_download_size_mb() * 1024 * 1024


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
    if parsed_content_length > _max_download_track_bytes():
        return _DOWNLOAD_TOO_LARGE
    return 0

# Sentinel returned by _download_track_to_file when a job cancel was observed.
_DOWNLOAD_CANCELLED = -3

# Resume-with-backoff when the CDN/WARP tunnel cuts a track download mid-body
# ("Upstream connection closed prematurely", connection resets, read timeouts).
# Retries are UNBOUNDED (a download never gives up unless the user cancels);
# backoff delays plateau at the last entry (16s) so a troubled download waits
# patiently instead of hammering.
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
    tracks to a web-browser UA. So build on the shared CDN base (web UA +
    Referer/Origin), NOT the android get_common_headers() set.
    """
    from api.client import build_cdn_headers

    headers = build_cdn_headers(
        referer=appconf["bili"].get("referer", "https://www.bilibili.com")
    )
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

    Video: highest ``id`` (quality) <= ``max_video_qn``. Audio: highest
    ``bandwidth`` from the standard ``dash.audio`` array, independent of the
    video resolution (avoids Dolby/FLAC lossless tracks).
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
    audio_bw = -1
    for t in _normalize_track_urls(dash.get("audio")):
        try:
            bw = int(t.get("bandwidth") or 0)
        except (TypeError, ValueError):
            bw = 0
        if bw < 0:
            bw = 0
        if audio is None or bw > audio_bw:
            audio, audio_bw = t, bw
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
    cr = (headers or {}).get("content-range") or ""
    m = re.match(r"bytes\s+(\d+)-", cr)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


async def _await_track_retry(
    attempt: int, cancel_event: asyncio.Event | None, note_cb, exc: Exception, label: str | None = None
) -> str:
    """Back off before resume attempt ``attempt`` (0-based). Returns 'retry' or 'cancelled'.

    Never 'abort's on its own: downloads persist until the user cancels.
    Backoff plateaus at the last entry of ``_TRACK_DOWNLOAD_RETRY_DELAYS``.
    """
    where = f" {label}" if label else ""
    print(f"[DashProxy] track download{where} cut, will resume (retry #{attempt + 1}): {exc}")
    delay = _TRACK_DOWNLOAD_RETRY_DELAYS[min(attempt, len(_TRACK_DOWNLOAD_RETRY_DELAYS) - 1)]
    if note_cb is not None:
        note_cb(f"Connection interrupted, retrying in {delay:g}s (attempt #{attempt + 1})…")
    if cancel_event is None:
        await asyncio.sleep(delay)
        return "retry"
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=delay)
        return "cancelled"
    except asyncio.TimeoutError:
        return "retry"


async def _download_track_to_file(
    url: str | list,
    headers: dict,
    proxy_url: str,
    dest: str,
    max_bytes: int | None = None,
    progress_cb=None,
    cancel_event: asyncio.Event | None = None,
    note_cb=None,
    rewind_cb=None,
) -> int:
    """Download a full track body to ``dest`` via CdnConnection, resuming on cuts.

    ``url`` may be a single URL or a candidate mirror list (primary +
    backups, see :func:`_dash_candidate_urls`). Each resume retry rotates to
    the next mirror: individual CDN objects stall/cut on one edge while
    siblings serve fine, and hammering the same sick edge is what used to
    fail jobs after a couple of cuts.

    When upstream cuts the connection mid-body (reset / premature close /
    read timeout) or answers a resume with a transient status / offset
    mismatch, waits a few seconds and resumes from the downloaded offset
    with a ``Range`` request, retrying without limit until the user cancels
    (backoff plateaus at 16s between attempts).

    Returns byte count, ``-1`` on error/oversize, or ``_DOWNLOAD_CANCELLED``
    when ``cancel_event`` is set (checked per chunk and during backoff waits;
    the partial file is left for caller cleanup).

    ``max_bytes`` defaults to the hoster-configured per-track cap.
    """
    if isinstance(url, (list, tuple)):
        candidates = [u for u in url if isinstance(u, str) and u]
    else:
        candidates = [url] if isinstance(url, str) and url else []
    if not candidates:
        print("[DashProxy] track download error: no candidate URLs")
        return -1
    if max_bytes is None:
        max_bytes = _max_download_track_bytes()
    total = 0
    attempt = 0
    mirror_idx = 0
    file_obj = None
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return _DOWNLOAD_CANCELLED
            current_url = candidates[mirror_idx % len(candidates)]
            try:
                total, file_obj, outcome = await _fetch_track_attempt(
                    current_url,
                    headers,
                    proxy_url,
                    dest,
                    file_obj,
                    total,
                    max_bytes,
                    progress_cb,
                    cancel_event,
                    rewind_cb,
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
                    # Rotate mirrors so the resume hits a different edge.
                    mirror_idx += 1
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
    """Classify a track GET response: 'ok', 'restart', 'retry', or 'fatal'.

    'restart' means the server ignored our resume ``Range`` (HTTP 200) — the
    caller must truncate and start over. 'retry' (transient HTTP status,
    resume offset mismatch) must be retried, preferably on the next mirror.
    'fatal' (oversize) must not be retried.
    """
    status = resp_headers.status_code
    if status not in (200, 206):
        print(
            f"[DashProxy] track GET -> HTTP {status} "
            f"(have {total} bytes, content-range={resp_headers.headers.get('content-range')}), will retry on next mirror"
        )
        return "retry"
    if total > 0:
        if status != 206:
            return "restart"
        if _parse_content_range_start(resp_headers.headers) != total:
            print(
                f"[DashProxy] resume offset mismatch (have {total}, server {resp_headers.headers.get('content-range')}), "
                "will retry on next mirror"
            )
            return "retry"
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


async def _fetch_track_attempt(
    url: str,
    headers: dict,
    proxy_url: str,
    dest: str,
    file_obj,
    total: int,
    max_bytes: int,
    progress_cb,
    cancel_event,
    rewind_cb,
):
    """One GET (or Range-resume) attempt. Returns ``(total, file_obj, outcome)``.

    ``outcome`` is 'done' (clean EOF), 'fatal' (do not retry), or 'cancelled'.
    Raises ``_TrackCut`` (carrying partial progress) or ``_RETRYABLE_TRACK_ERRORS``
    on connection cuts so the caller can back off and resume. A 'retry'
    validation (transient HTTP status / offset mismatch) raises
    ``CdnProtocolError`` for the same resume path.
    """
    req_headers = dict(headers)
    if total > 0:
        req_headers["Range"] = f"bytes={total}-"
    conn, resp_headers = await _open_cdn_track(url, req_headers, proxy_url)
    try:
        action = _validate_track_response(resp_headers, total, max_bytes)
        if action == "retry":
            raise CdnProtocolError(f"CDN returned HTTP {resp_headers.status_code} for Range resume (have {total} bytes)")
        if action == "fatal":
            print(
                f"[DashProxy] track GET fatal: HTTP {resp_headers.status_code} "
                f"(have {total} bytes, content-range={resp_headers.headers.get('content-range')}, "
                f"content-length={resp_headers.headers.get('content-length')})"
            )
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
        total, outcome = await _stream_track_body(conn, file_obj, total, max_bytes, progress_cb, cancel_event)
        return total, file_obj, outcome
    finally:
        await conn.close()


async def _stream_track_body(conn, file_obj, total: int, max_bytes: int, progress_cb, cancel_event):
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
        ffmpeg,
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
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
                done, _ = await asyncio.wait({comm_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
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

    Durl-only uploads (no DASH tracks) fall back to a
    redirect at the native progressive ``/proxy/video/`` path — those MP4s
    are already muxed, so no ffmpeg step is needed.
    """
    if not _is_valid_vid(vid):
        return Response("Bad Request: invalid video ID", status=400)

    max_qn = min(qual, _FREE_DOWNLOAD_MAX_QN) if qual > 0 else _FREE_DOWNLOAD_MAX_QN
    dash_data = await _load_dash_data(vid, idx)
    if not has_valid_dash_tracks(dash_data):
        return await _proxy_download_durl_fallback(vid, idx, qual)

    video, audio = _pick_download_tracks(dash_data, max_qn)
    if not video or not audio:
        return Response("Not Found: no suitable DASH tracks", status=404)
    vurls = _dash_candidate_urls(video)
    aurls = _dash_candidate_urls(audio)
    if not vurls or not aurls:
        return Response("Not Found: track has no URL", status=404)
    v_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in vurls))
    a_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in aurls))
    vurls = [u for u, ok in zip(vurls, v_checks, strict=False) if ok]
    aurls = [u for u, ok in zip(aurls, a_checks, strict=False) if ok]
    if not vurls or not aurls:
        return Response("Forbidden: Invalid proxy target", status=403)

    proxy_url = Network.get_proxy()
    headers = await _build_dash_cdn_headers()

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix="miku_dl_")
    vpath = os.path.join(tmpdir, "video.m4s")
    apath = os.path.join(tmpdir, "audio.m4s")
    outpath = os.path.join(tmpdir, "out.mp4")
    response_owns_cleanup = False
    try:
        vsize = await _peek_content_length(vurls, headers, proxy_url)
        asize = await _peek_content_length(aurls, headers, proxy_url)
    except RuntimeError as exc:
        await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
        return Response(str(exc), status=413)

    try:
        v_qn = int(video.get("id") or 0)
    except (TypeError, ValueError):
        v_qn = 0
    try:
        a_qn = int(audio.get("id") or 0)
    except (TypeError, ValueError):
        a_qn = 0

    async def _refresh_legacy_track_urls(media_type: str, qn: int):
        fresh = await _refresh_dash_data(vid, idx)
        if not has_valid_dash_tracks(fresh):
            return None
        pick = _pick_download_tracks(fresh, max_qn)
        track = pick[0] if media_type == "video" else pick[1]
        if track is None:
            return None
        try:
            if int(track.get("id") or -1) != qn:
                return None
        except (TypeError, ValueError):
            return None
        urls = _dash_candidate_urls(track)
        checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls))
        urls = [u for u, ok in zip(urls, checks, strict=False) if ok]
        return urls or None

    async def _refresh_legacy_video_urls():
        return await _refresh_legacy_track_urls("video", v_qn)

    async def _refresh_legacy_audio_urls():
        return await _refresh_legacy_track_urls("audio", a_qn)

    try:
        async with _download_limiter:
            vn, an = await asyncio.gather(
                _download_track_file(
                    vurls,
                    headers,
                    proxy_url,
                    vpath,
                    vsize,
                    f"{vid}:{idx} video",
                    refresh_cb=_refresh_legacy_video_urls,
                ),
                _download_track_file(
                    aurls,
                    headers,
                    proxy_url,
                    apath,
                    asize,
                    f"{vid}:{idx} audio",
                    refresh_cb=_refresh_legacy_audio_urls,
                ),
            )
            if vn < 0:
                return Response("Upstream error (video track)", status=502)
            if an < 0:
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
        resp.headers["Content-Disposition"] = f'attachment; filename="{_safe_download_filename(vid, idx, actual_qn)}"'
        resp.headers["X-Accel-Buffering"] = "no"
        response_owns_cleanup = True
        return resp
    except Exception as exc:
        await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
        print(f"[DashProxy] proxy_download error: {exc}")
        return Response("Upstream error", status=502)
    finally:
        if not response_owns_cleanup:
            await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
        # NOTE: no manual _download_limiter.release() — the `async with` above
        # already released it; a manual release would corrupt the semaphore.


def _dash_candidate_urls(track: dict) -> list:
    """Primary + backup CDN URLs for a DASH track, safety-checked.

    Bilibili serves every track from ``base_url`` with 2-3 mirrors in
    ``backup_url``. The primary edge sometimes stalls individual objects
    (cold cache / tarpit: latency swinging from ms to 25s+), so the proxy
    must be able to fail over instead of hanging on the primary.

    M-CDN (PCDN) edges are deprioritized but kept as last-resort mirrors
    (PipePipe b3303f4 ``pickStableStreamUrl``): the stable URL goes first,
    remaining mirrors follow for failover.
    """
    candidates = []
    primary = track.get("base_url") or track.get("baseUrl")
    backups = track.get("backup_url") or track.get("backupUrl") or []
    if isinstance(backups, str):
        backups = [backups]
    if not isinstance(backups, list):
        backups = []
    ordered = []
    stable = _pick_stable_dash_url(primary, backups)
    if stable:
        ordered.append(stable)
    for url in [primary, *backups]:
        if isinstance(url, str) and url and url not in ordered:
            ordered.append(url)
    # Stable (non-M-CDN) first, M-CDN last as failover-only.
    ordered.sort(key=lambda u: (1 if _is_mcdn_url(u) else 0))
    for url in ordered:
        if isinstance(url, str) and url and url not in candidates and _is_safe_dash_url(url):
            candidates.append(url)
    return candidates


async def _fetch_dash_attempt(url: str, headers: dict, proxy_url: str, timeout: float = DASH_ATTEMPT_TIMEOUT):
    """Connect + request + read headers for one candidate URL.

    Bounded by ``timeout`` so a stalled edge fails fast and
    the caller can try the next mirror. Returns ``(conn, resp_headers)``;
    the caller owns ``conn.close()``. Raises ``TimeoutError`` on stall
    (closed connection included) and propagates other errors.
    """
    conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
    try:
        resp_headers = await asyncio.wait_for(
            _dash_handshake(conn),
            timeout=timeout,
        )
        return conn, resp_headers
    except BaseException:
        await conn.close()
        raise


async def _dash_handshake(conn: CdnConnection):
    """One connect + request + header-read sequence (deadline applied by caller)."""
    await conn.connect()
    await conn.send_request()
    return await conn.read_response_headers()


async def _refresh_dash_ticket(headers: dict) -> dict:
    """Refresh the bili ticket after a 403-class response. Returns updated headers."""
    headers = dict(headers)
    ticket = await TicketManager.get_ticket(force_refresh=True)
    if ticket:
        headers["x-bili-ticket"] = ticket
    else:
        headers.pop("x-bili-ticket", None)
    headers["session_id"] = TicketManager._generate_session_id()
    headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
    return headers


class _DashUpstreamError(Exception):
    """All candidate mirrors failed for a DASH track.

    ``forbidden`` is True when at least one mirror answered 403/412/514
    (signature/risk-control rejection — the URL set itself is likely dead,
    not just a sick edge, so callers may try a fresh playurl instead of
    merely another mirror).
    """

    def __init__(self, message="", *, forbidden=False):
        super().__init__(message)
        self.forbidden = forbidden


_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)(?:/(\d+|\*))?")


def _dash_response_lengths(resp_headers) -> tuple[str | None, str | None]:
    """Derive guaranteed ``(Content-Length, Content-Range)`` for a track response.

    dash.js measures throughput and drives its ABR/abandon decisions from XHR
    progress events. When the response carries no computable length
    (``lengthComputable`` false), ``bytesTotal`` stays NaN, the
    abandon-requests rule can never trigger, and a slow fragment hangs until
    the XHR timeout fires as "Request timeout: non-computable download size"
    (dash.js#4716) with ABR stuck on the top rendition.

    Returns ``(content_length, content_range)`` with ``content_range`` None
    for ``200`` responses. Returns ``(None, None)`` when a ``200``/``206``
    response cannot provide computable lengths (missing/invalid headers) —
    the caller must treat that mirror as unusable and try the next one
    instead of serving a lengthless stream. Error statuses also return
    ``(None, None)`` (lengths irrelevant there).
    """
    status = resp_headers.status_code
    if status not in (200, 206):
        return None, None
    headers = resp_headers.headers or {}

    cl: int | None = None
    cl_raw = headers.get("content-length")
    if cl_raw is not None:
        try:
            cl = int(str(cl_raw).strip())
        except (TypeError, ValueError):
            cl = None
        if cl is not None and cl < 0:
            cl = None

    content_range: str | None = None
    if status == 206:
        cr_raw = (headers.get("content-range") or "").strip()
        m = _CONTENT_RANGE_RE.match(cr_raw)
        if not m:
            return None, None
        start, end = int(m.group(1)), int(m.group(2))
        if end < start:
            return None, None
        total = m.group(3) or "*"
        content_range = f"bytes {start}-{end}/{total}"
        # A 206 length must equal the range span; prefer the range-derived
        # value so a mismatched/odd upstream Content-Length cannot desync
        # the byte count dash.js expects.
        cl = end - start + 1

    if cl is None:
        return None, None
    return str(cl), content_range


class _SlowDashBody(Exception):
    """Upstream body trickling below the watchdog floor (retryable on next mirror)."""


async def _yield_dash_body(conn, label: str, floor_watchdog: bool = True):
    """Yield upstream body chunks with a throughput watchdog.

    Past ``DASH_BODY_GRACE`` seconds, a cumulative average under
    ``DASH_BODY_FLOOR_KBPS`` raises ``_SlowDashBody`` so the caller retries the
    remaining Range on the next mirror instead of tarpitting until dash.js
    abandons the request. Small (sidx/init) responses finish inside the grace
    period and never trip.

    Separately, no bytes at all for ``DASH_BODY_IDLE_TIMEOUT`` raises too: a
    wedged edge that idles (then dribbles) is invisible to the cumulative
    average until far too late.

    ``floor_watchdog=False`` disables the speed trip (keeps the idle trip).
    Slow is not failure: like Bilibili's own player (backup URLs only on
    failure), the playback proxy must not switch mirrors merely because an
    edge is slow — dash.js owns slowness (abandon + ABR). Speed trips stay on
    for downloads, where no client exists to catch them.
    """
    got = 0
    t0 = time.monotonic()
    iterator = conn.iter_chunks()
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=DASH_BODY_IDLE_TIMEOUT)
        except StopAsyncIteration:
            return
        except (asyncio.TimeoutError, TimeoutError):
            raise _SlowDashBody(f"no data for {DASH_BODY_IDLE_TIMEOUT:.0f}s (idle stall)")
        got += len(chunk)
        el = time.monotonic() - t0
        if floor_watchdog and el > DASH_BODY_GRACE and got / el < DASH_BODY_FLOOR_KBPS * 1024:
            raise _SlowDashBody(f"{got / el / 1024:.0f} KB/s < {DASH_BODY_FLOOR_KBPS} KB/s after {el:.1f}s")
        yield chunk


def _shift_dash_range(orig_range: str | None, yielded: int) -> str | None:
    """Remaining Range after ``yielded`` bytes were already sent downstream.

    Returns None when nothing was sent yet (keep the original request shape).
    The byte sequence stays contiguous across mirror switches, so the retry is
    invisible to the player: response headers were already emitted from the
    first attempt.
    """
    if not yielded:
        return None
    m = re.match(r"bytes=(\d+)-(\d*)$", (orig_range or "").strip())
    if not m:
        return f"bytes={yielded}-"
    return f"bytes={int(m.group(1)) + yielded}-{m.group(2)}"


def _expected_resume_start(orig_range: str | None, yielded: int) -> int | None:
    """Expected Content-Range start after ``yielded`` bytes were sent.

    Returns None when the original request shape is unknown (failover keeps
    whatever the mirror returns).
    """
    m = re.match(r"bytes=(\d+)-(\d*)$", (orig_range or "").strip())
    if m:
        try:
            return int(m.group(1)) + yielded
        except (TypeError, ValueError):
            return None
    if not orig_range:
        # Full-file (no Range) request starting at 0.
        return yielded
    return None


def _parse_content_range_full(cr: str | None) -> tuple[int | None, int | None, int | None]:
    """Parse ``Content-Range: bytes <start>-<end>/<total>`` into ints.

    Returns ``(start, end, total)`` with ``total`` None for ``*``/missing.
    Returns ``(None, None, None)`` when unparseable.
    """
    m = _CONTENT_RANGE_RE.match((cr or "").strip())
    if not m:
        return None, None, None
    try:
        start, end = int(m.group(1)), int(m.group(2))
    except (TypeError, ValueError):
        return None, None, None
    total: int | None = None
    raw_total = m.group(3)
    if raw_total and raw_total != "*":
        try:
            total = int(raw_total)
        except (TypeError, ValueError):
            total = None
    return start, end, total


class _ResumedDashConn:
    """Wrap a mirror that did not resume contiguously (Range ignored / offset).

    Skips ``skip`` prefix bytes of the upstream body and caps output at
    ``limit`` bytes so the byte sequence stays contiguous with what was
    already sent downstream (downstream headers were emitted from the first
    mirror and cannot change). ``limit`` None means unbounded.
    """

    def __init__(self, conn, skip: int, limit: int | None):
        self._conn = conn
        self._skip = max(int(skip or 0), 0)
        self._limit = limit

    async def iter_chunks(self):
        skip = self._skip
        limit = self._limit
        async for chunk in self._conn.iter_chunks():
            if skip > 0:
                if len(chunk) <= skip:
                    skip -= len(chunk)
                    continue
                chunk = chunk[skip:]
                skip = 0
            if limit is not None:
                if limit <= 0:
                    break
                if len(chunk) > limit:
                    chunk = chunk[:limit]
                limit -= len(chunk)
                yield chunk
                if limit <= 0:
                    break
            else:
                yield chunk

    async def close(self):
        try:
            await self._conn.close()
        except Exception:
            pass


async def _failover_dash_conn(
    pending: list, headers: dict, proxy_url: str, label: str, orig_range, yielded: int, downstream_total: int | None = None,
    attempt_timeout: float | None = None
):
    """Fail over a cut/slow body to the next mirror.

    Shifts ``headers["range"]`` when bytes were already sent, then walks
    ``pending`` until a mirror answers with a byte-contiguous resume point.
    Mirrors that answer 206 at the expected offset are used directly.
    Mirrors that ignore the resume Range (200 full body) or answer 206 at an
    earlier offset that still covers the resume point are salvaged by
    skipping the already-sent prefix (capped to the downstream remainder),
    because re-requesting cannot change bytes already flushed to the player.
    Mirrors that start past the resume point (gap) or whose body is shorter
    than the resume point are skipped. Removes the used URL from ``pending``
    and returns ``(conn, used_url, resp_headers)``; raises ``_DashUpstreamError``
    when no mirror is usable. The caller owns ``conn.close()`` (wrapper or raw).
    """
    if yielded:
        headers["range"] = _shift_dash_range(orig_range, yielded) or headers.get("range")
    expected = _expected_resume_start(orig_range, yielded) if yielded else None
    remaining = downstream_total - yielded if downstream_total is not None and yielded else None
    if remaining is not None and remaining < 0:
        remaining = 0
    last_error: Exception | None = None
    forbidden_seen = False
    while pending:
        try:
            conn, resp_headers, used, _, _ = await _fetch_dash_track(
                pending, headers, proxy_url, label, attempt_timeout=attempt_timeout
            )
        except _DashUpstreamError as exc:
            raise exc
        if resp_headers.status_code in (403, 412, 514):
            # Auth-level rejection (expired signature): remember it for the
            # caller's refresh decision even if later failures differ.
            forbidden_seen = True
        if yielded:
            status = resp_headers.status_code
            if status == 206:
                start, end, _ = _parse_content_range_full(resp_headers.headers.get("content-range"))
                if expected is None or start == expected:
                    pending.remove(used)
                    return conn, used, resp_headers
                if start is not None and end is not None and expected is not None and start < expected <= end:
                    skip = expected - start
                    pending.remove(used)
                    print(
                        f"[DashProxy] {label} {urlparse(used).hostname} resume overlap "
                        f"(got {start}-{end}, want {expected}, skipping {skip}), salvaging"
                    )
                    return _ResumedDashConn(conn, skip, remaining), used, resp_headers
                print(
                    f"[DashProxy] {label} {urlparse(used).hostname} resume mismatch "
                    f"(want start={expected}, got status={status} "
                    f"range={resp_headers.headers.get('content-range')}), trying next mirror"
                )
                pending.remove(used)
                try:
                    await conn.close()
                except Exception:
                    pass
                last_error = RuntimeError(f"resume offset mismatch on {used}")
                continue
            if status == 200 and expected is not None:
                # Server ignored Range: full body from 0, discard the prefix.
                try:
                    total = int(str(resp_headers.headers.get("content-length") or "").strip())
                except (TypeError, ValueError):
                    total = None
                if total is not None and total > expected:
                    limit = total - expected if remaining is None else min(remaining, total - expected)
                    pending.remove(used)
                    print(
                        f"[DashProxy] {label} {urlparse(used).hostname} ignored resume Range "
                        f"(200 full body, discarding {expected} prefix), salvaging"
                    )
                    return _ResumedDashConn(conn, expected, limit), used, resp_headers
                print(
                    f"[DashProxy] {label} {urlparse(used).hostname} resume mismatch "
                    f"(want start={expected}, got status={status} "
                    f"range={resp_headers.headers.get('content-range')}), trying next mirror"
                )
                pending.remove(used)
                try:
                    await conn.close()
                except Exception:
                    pass
                last_error = RuntimeError(f"resume offset mismatch on {used}")
                continue
            print(
                f"[DashProxy] {label} {urlparse(used).hostname} resume mismatch "
                f"(want start={expected}, got status={resp_headers.status_code} "
                f"range={resp_headers.headers.get('content-range')}), trying next mirror"
            )
            pending.remove(used)
            try:
                await conn.close()
            except Exception:
                pass
            last_error = RuntimeError(f"resume offset mismatch on {used}")
            continue
        pending.remove(used)
        return conn, used, resp_headers
    raise _DashUpstreamError(
        f"all mirrors failed for {label}: {last_error}",
        forbidden=forbidden_seen or getattr(last_error, "forbidden", False),
    )


# Cross-request sick-mirror memory. Per-request failover always restarts at
# the primary, so one sick edge poisons every fragment (each burns seconds
# failing over) while the player sees only slowness. These marks make the
# *next* request start on a healthy mirror instead.
#
# Only transport health is tracked: handshake stalls, connection/protocol
# errors, mid-body cuts, slow bodies, unusable responses. 403/412/514 are
# auth-level (dead signatures — handled by the ticket/playurl refresh paths)
# and never mark a mirror sick. Success clears immediately; failures expire
# after _MIRROR_SICK_COOLDOWN so recovered edges rejoin automatically.
# Single worker, so a plain dict is fine.
_MIRROR_SICK_COOLDOWN = 120.0
_MIRROR_HEALTH_MAX = 1024
_mirror_health: dict = {}  # host -> [consecutive_fails, last_fail_monotonic]


def _note_mirror_ok(host: str | None):
    """Clear a mirror's failure record after a clean full-body transfer."""
    if not host:
        return
    _mirror_health.pop(host, None)


def _note_mirror_bad(host: str | None):
    """Record a transport failure for a mirror (sick for _MIRROR_SICK_COOLDOWN)."""
    if not host:
        return
    now = time.monotonic()
    entry = _mirror_health.get(host)
    if entry is None:
        _mirror_health[host] = [1, now]
    else:
        entry[0] += 1
        entry[1] = now
    if len(_mirror_health) > _MIRROR_HEALTH_MAX:
        cutoff = now - _MIRROR_SICK_COOLDOWN
        for h in [h for h, (_, last) in _mirror_health.items() if last < cutoff]:
            del _mirror_health[h]


def _mirror_last_fail(host: str) -> float:
    entry = _mirror_health.get(host)
    return entry[1] if entry is not None else 0.0


def _mirror_is_sick(host: str | None) -> bool:
    """True when a mirror failed recently and hasn't proven healthy since."""
    if not host:
        return False
    entry = _mirror_health.get(host)
    if entry is None or entry[0] <= 0:
        return False
    return time.monotonic() - entry[1] < _MIRROR_SICK_COOLDOWN


def _order_urls_by_health(urls: list) -> list:
    """Healthy mirrors first (original order), recently-failed last (oldest failure first)."""
    healthy, sick = [], []
    for u in urls:
        (sick if _mirror_is_sick(urlparse(u).hostname or "") else healthy).append(u)
    sick.sort(key=lambda u: _mirror_last_fail(urlparse(u).hostname or ""))
    return healthy + sick


def _segment_mirror_order(candidates: list, seg_index: int) -> list:
    """Per-segment mirror order: rotate healthy stable mirrors first.

    Rotation spreads back-to-back load so no single edge throttles (the
    decaying-speed failure mode); sick mirrors trail for failover only;
    M-CDN stays last-resort.
    """
    stable = [u for u in candidates if not _is_mcdn_url(u)] or list(candidates)
    tail = [u for u in candidates if u not in stable]
    healthy = [u for u in stable if not _mirror_is_sick(urlparse(u).hostname or "")]
    sick = [u for u in stable if u not in healthy]
    if not healthy:
        return sick + tail
    rot = seg_index % len(healthy)
    return healthy[rot:] + healthy[:rot] + sick + tail


async def _fetch_dash_track(urls: list, headers: dict, proxy_url: str, label: str, attempt_timeout: float | None = None):
    """Try each candidate mirror in order; return ``(conn, resp_headers, used_url, content_length, content_range)``.
    Stalled edges fail fast (``DASH_ATTEMPT_TIMEOUT``) so playback falls over
    to the next mirror instead of hanging until dash.js abandons the request
    (which used to surface as a bare 500 with no app-side log). Mirrors whose
    ``200``/``206`` response cannot provide computable lengths (see
    ``_dash_response_lengths``) are likewise skipped: serving them would leave
    dash.js with non-computable progress events, breaking throughput/ABR and
    ending in "Request timeout: non-computable download size".     Raises
    ``_DashUpstreamError`` when every mirror fails; the caller owns
    ``conn.close()`` on success.

    Recently-failed mirrors are tried last (see sick-mirror memory): every
    fragment otherwise restarts at a sick primary and burns seconds before
    failing over.
    """
    ticket_refreshed = False
    last_error: Exception | None = None
    forbidden_seen = False
    handshake_timeout = attempt_timeout or DASH_ATTEMPT_TIMEOUT
    urls = _order_urls_by_health(urls)
    for url in urls:
        host = urlparse(url).hostname or url
        try:
            conn, resp_headers = await _fetch_dash_attempt(url, headers, proxy_url, timeout=handshake_timeout)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            last_error = exc
            _note_mirror_bad(urlparse(url).hostname or "")
            print(f"[DashProxy] {label} {host} stalled, trying next mirror")
            continue
        except (CdnConnectError, CdnProtocolError, CdnTimeoutError, OSError) as exc:
            last_error = exc
            _note_mirror_bad(urlparse(url).hostname or "")
            print(f"[DashProxy] {label} {host} failed ({exc}), trying next mirror")
            continue
        if resp_headers.status_code in [403, 412, 514]:
            await conn.close()
            forbidden_seen = True
            last_error = RuntimeError(f"CDN returned {resp_headers.status_code}")
            if not ticket_refreshed:
                headers = await _refresh_dash_ticket(headers)
                ticket_refreshed = True
            print(f"[DashProxy] {label} {host} -> {resp_headers.status_code}, trying next mirror")
            continue
        if resp_headers.status_code in (200, 206):
            content_length, content_range = _dash_response_lengths(resp_headers)
            if content_length is None:
                await conn.close()
                _note_mirror_bad(urlparse(url).hostname or "")
                last_error = RuntimeError("CDN response has no computable length")
                print(f"[DashProxy] {label} {host} has no computable length, trying next mirror")
                continue
        else:
            content_length, content_range = None, None
        return conn, resp_headers, url, content_length, content_range
    raise _DashUpstreamError(
        f"all mirrors failed for {label}: {last_error}",
        forbidden=forbidden_seen,
    )


async def _retry_proxy_with_fresh_playurl(vid, idx, media_type, qn, cid, old_urls, headers, proxy_url, label, attempt_timeout=None):
    """One fresh-playurl retry for a 403-swept track (official player recovery).

    Drops the cached playurl (whose signatures likely expired — all mirrors
    of one response share the expiry window), re-fetches, and retries the
    request on the new mirror set. Returns ``((conn, resp_headers, used_url,
    content_length, content_range), urls)`` or None when there is nothing
    usable to retry with. The caller owns ``conn.close()`` on success.

    Never loops: at most one refresh per call, and a refresh that yields the
    same expiry stamp as the dead set (the player's same-stamp dedup) is
    declined — retrying identical signatures cannot succeed.
    """
    fresh_data = await _refresh_dash_data(vid, idx)
    if not has_valid_dash_tracks(fresh_data):
        return None
    track = _lookup_track(fresh_data, media_type, qn, cid)
    if not track:
        return None
    new_urls = _dash_candidate_urls(track)
    verdicts = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in new_urls))
    new_urls = [u for u, ok in zip(new_urls, verdicts, strict=False) if ok]
    if not new_urls:
        return None
    old_stamp = _playurl_url_expiry(old_urls[0]) if old_urls else None
    new_stamp = _playurl_url_expiry(new_urls[0])
    if old_stamp is not None and new_stamp == old_stamp:
        print(f"[DashProxy] {label} refresh returned same URL stamp ({new_stamp}), not retrying")
        return None
    print(f"[DashProxy] {label} refreshed playurl (stamp {old_stamp} -> {new_stamp}), retrying request")
    try:
        fetched = await _fetch_dash_track(new_urls, headers, proxy_url, label, attempt_timeout=attempt_timeout)
    except _DashUpstreamError as exc:
        print(f"[DashProxy] {label} fresh playurl also failed: {exc}")
        return None
    return fetched, new_urls


# Player-style segmented download: fetch the track the way dash.js does.
#
# The player never GETs a whole track: it issues small Range requests
# (sidx/init/media fragments, a few MB each) through ``proxy_dash``, each with
# its own handshake deadline, mirror failover, and throughput watchdog, plus
# up to 5 retries per fragment and ABR abandon of slow ones. The old
# downloader did the opposite — one giant whole-file GET with no speed
# watchdog, so a tarpit edge dribbling ~100 KB/s never tripped the 30s *idle*
# read timeout and the job looked "stuck" for tens of minutes while the player
# sailed past the same edge. This fetches sequential fixed-size segments with
# the exact same machinery (``_failover_dash_conn`` + ``_yield_dash_body`` +
# ``_ResumedDashConn``), writing them to ``dest`` instead of the HTTP
# response, so a sick edge costs one segment — not the file.
_DOWNLOAD_SEGMENT_SIZE = 32 * 1024 * 1024

# Disk writes are batched to this size: per-64KB-chunk asyncio.to_thread hops
# cost ~800 threadpool round-trips/sec at speed for zero benefit.
_DOWNLOAD_WRITE_BATCH = 1024 * 1024

# Adaptive pacing ladder for downloads: dash.js fragment requests arrive seconds
# apart (buffer-driven), while this loop fires back-to-back at full speed —
# sustained hammering earns shed load (empty closes) and escalating per-IP
# throttling that reads as decaying speed (fast start, then clamp). Pauses let
# the punishment decay: each consecutive slow trip steps down the ladder
# (4/6/9/12/14s, holding 14s); clean fast segments reset to the base.
_DOWNLOAD_PACE_DELAYS = (2.0, 4.0, 6.0, 8.0, 10.0)
_DOWNLOAD_GOOD_BPS = 1024 * 1024


def _pace_delay(slow_streak: int) -> float:
    """Pause for the current consecutive-slow-trip count (0 = base inter-segment gap)."""
    return _DOWNLOAD_PACE_DELAYS[min(max(slow_streak, 0), len(_DOWNLOAD_PACE_DELAYS) - 1)]


# Current-speed slowdown trip: fixed 2s windows; N consecutive windows under
# _SLOW_WINDOW_KBPS treatments the connection like a premature disconnect and
# fails over. Averages react too slowly (a collapsed edge hides behind its own
# fast start for minutes) and burst-set peaks trip healthy-hundreds speeds —
# the current window is always the truth about right now. Armed only after
# capability is proven (a >= _SLOW_ARM_BPS window seen this track — uniformly
# slow links never trip; the absolute watchdog still guards true stalls), and
# bounded to one probe per mirror per segment, then patience.
_SLOW_WINDOW = 2.0
_SLOW_WINDOW_KBPS = 300
_SLOW_ARM_BPS = 1024 * 1024
_SLOW_WINDOW_STRIKES = 3
# Pause after a slow trip before reconnecting: the edge is throttling, and a
# fresh hammer lands back in the penalty box. Fixed 10s (not escalating —
# escalation lives in the inter-segment pacing ladder).
_SLOW_TRIP_PAUSE = 10.0


def _has_exact_content_length(resp_headers, span_left: int) -> bool:
    """True when a 206 response carries Content-Length equal to the wanted span.

    Length-framed exact bodies are the only ones safe to (a) stream to a
    known end — a close-delimited 206 on a reused keep-alive socket would hang
    forever waiting for a close that never comes — and (b) keep alive
    afterwards, since exact consumption leaves the socket precisely at the
    next message boundary.
    """
    if resp_headers.status_code != 206:
        return False
    try:
        return int(str((resp_headers.headers or {}).get("content-length") or "").strip()) == span_left
    except (TypeError, ValueError):
        return False


async def _download_track_segmented(
    urls,
    headers: dict,
    proxy_url: str,
    dest: str,
    total_size: int,
    label: str,
    max_bytes: int | None = None,
    progress_cb=None,
    cancel_event: asyncio.Event | None = None,
    note_cb=None,
    refresh_cb=None,
    max_refreshes: int | None = None,
) -> int:
    """Download a track as sequential player-sized Range segments to ``dest``.

    Same per-request behaviour as ``proxy_dash`` (primary-first mirrors,
    ``DASH_ATTEMPT_TIMEOUT`` handshakes, bili-ticket refresh, computable-length
    gate, ``DASH_BODY_GRACE``/``DASH_BODY_FLOOR_KBPS`` watchdog, byte-exact
    resume across mirrors), but appended to a file: mid-segment cuts and
    slow-loris bodies fail over to the next mirror for the *remainder* of the
    segment, and a fully exhausted mirror set backs off
    (``_await_track_retry``) and retries the segment from the next mirror,
    without limit until the user cancels (backoff plateaus at 16s).

    ``refresh_cb`` (``async () -> list | None``) fetches a fresh playurl and
    returns replacement mirror URLs for the *same* track when the current
    set is exhausted — all mirrors of one response share the expiry window,
    so rotating them cannot fix dead signatures. Refresh fires only for dead
    signatures (403-class sweep, or a provably-past URL stamp), never for
    mere slowness; ``max_refreshes=None`` (default) means unbounded, and a
    refresh returning the same URL stamp is always declined (the official
    player's same-stamp dedup): retrying identical signatures cannot succeed.

    Returns the byte count, ``-1`` on error/oversize, or
    ``_DOWNLOAD_CANCELLED`` when ``cancel_event`` is set.
    """
    if isinstance(urls, (list, tuple)):
        candidates = [u for u in urls if isinstance(u, str) and u]
    else:
        candidates = [urls] if isinstance(urls, str) and urls else []
    if not candidates:
        print(f"[DashProxy] {label} segmented download error: no candidate URLs")
        return -1
    if max_bytes is None:
        max_bytes = _max_download_track_bytes()
    if total_size > max_bytes:
        print(f"[DashProxy] {label} size {total_size} exceeds limit {max_bytes}")
        return -1
    file_obj = None
    written = 0
    seg_index = 0
    refreshes_used = 0
    last_stamp = _playurl_url_expiry(candidates[0])
    pending_writes: list = []
    pending_len = 0
    keepalive = None  # (conn, url): fully-consumed length-framed connection, reusable
    track_best_bps = 0.0  # best completed-segment (or early-window) rate this track
    slow_streak = 0  # consecutive slow trips: indexes _DOWNLOAD_PACE_DELAYS
    try:
        file_obj = await asyncio.to_thread(open, dest, "wb")
        while written < total_size:
            if cancel_event is not None and cancel_event.is_set():
                return _DOWNLOAD_CANCELLED
            seg_start = written
            seg_end = min(written + _DOWNLOAD_SEGMENT_SIZE - 1, total_size - 1)
            span = seg_end - seg_start + 1
            seg_label = f"{label} seg{seg_index}[{seg_start}-{seg_end}]"
            orig_range = f"bytes={seg_start}-{seg_end}"
            seg_headers = dict(headers)
            seg_headers["range"] = orig_range
            # Per-segment mirror order (rotation + sick-mirror memory, M-CDN
            # last): unlike the player (fragments spaced seconds apart), this
            # loop fires back-to-back at full speed — hammering one edge
            # invites throttling that reads as decaying speed.
            order = _segment_mirror_order(candidates, seg_index)
            start_idx = 0
            pending = order[:]
            seg_done = 0
            seg_attempt = 0
            seg_host = "-"
            seg_t0 = time.monotonic()
            win_t0 = seg_t0
            win_bytes = 0
            win_best = 0.0
            slow_strikes = 0
            slow_trips = 0
            seg_clean = True
            while seg_done < span:
                if cancel_event is not None and cancel_event.is_set():
                    return _DOWNLOAD_CANCELLED
                if pending_len:
                    # Disk must catch up to seg_done before any (re)fetch:
                    # resume ranges are computed from seg_done, so buffered
                    # bytes must be on disk first (abort paths skip this —
                    # their partial files are deleted by the caller).
                    batch = b"".join(pending_writes)
                    pending_writes.clear()
                    pending_len = 0
                    await asyncio.to_thread(file_obj.write, batch)
                conn = used = resp_headers = None
                if keepalive is not None:
                    # Kept-alive connection from the previous segment: no
                    # handshake, no slow-start restart. Only banked after an
                    # exactly-consumed length-framed body, so the socket is
                    # precisely at the next boundary. Any trouble falls back
                    # to full mirror failover below.
                    ka_conn, ka_url = keepalive
                    keepalive = None
                    try:
                        resp_headers = await ka_conn.next_request(seg_headers)
                        conn, used = ka_conn, ka_url
                    except (
                        CdnConnectError,
                        CdnProtocolError,
                        CdnTimeoutError,
                        OSError,
                        TimeoutError,
                        asyncio.TimeoutError,
                    ) as exc:
                        try:
                            await ka_conn.close()
                        except Exception:
                            pass
                        print(f"[DashProxy] {seg_label} keep-alive reuse failed ({exc}), failing over")
                if conn is None:
                    try:
                        conn, used, resp_headers = await _failover_dash_conn(
                            pending, seg_headers, proxy_url, seg_label, orig_range, seg_done, span
                        )
                    except _DashUpstreamError as exc:
                        # Refresh means dead signatures, never "edge is slow": only
                        # when mirrors actively reject auth (403-class sweep) or
                        # the stamp is provably past. A tarpit exhaustion must
                        # rotate/backoff instead — new signatures on the same sick
                        # edge change nothing.
                        provably_expired = last_stamp is not None and time.time() > last_stamp
                        if (
                            refresh_cb is not None
                            and (max_refreshes is None or refreshes_used < max_refreshes)
                            and (exc.forbidden or provably_expired)
                        ):
                            if note_cb is not None:
                                note_cb("Video links expired, refreshing…")
                            try:
                                new_urls = await refresh_cb()
                            except Exception as refresh_exc:
                                print(f"[DashProxy] {seg_label} playurl refresh failed: {refresh_exc}")
                                new_urls = None
                            if new_urls:
                                new_stamp = _playurl_url_expiry(new_urls[0])
                                if new_stamp != last_stamp:
                                    print(
                                        f"[DashProxy] {seg_label} refreshed playurl "
                                        f"(stamp {last_stamp} -> {new_stamp}), resuming"
                                    )
                                    candidates = new_urls
                                    last_stamp = new_stamp
                                    refreshes_used += 1
                                    seg_attempt = 0
                                    slow_strikes = 0
                                    slow_trips = 0
                                    order = _segment_mirror_order(candidates, seg_index)
                                    start_idx = 0
                                    pending = order[:]
                                    if note_cb is not None:
                                        note_cb(None)
                                    continue
                                print(
                                    f"[DashProxy] {seg_label} refresh returned same URL stamp, "
                                    "not retrying refresh"
                                )
                        decision = await _await_track_retry(seg_attempt, cancel_event, note_cb, exc, label=seg_label)
                        if decision == "retry":
                            seg_attempt += 1
                            start_idx = (start_idx + 1) % len(order)
                            pending = order[start_idx:] + order[:start_idx]
                            continue
                        return _DOWNLOAD_CANCELLED if decision == "cancelled" else -1
                seg_host = urlparse(used).hostname or "-"
                slow_strikes = 0  # fresh mirror: benefit of the doubt for its first windows
                exact_206 = False
                # Validate the mirror answered at the wanted offset. The
                # resume path inside _failover_dash_conn already guarantees
                # this for seg_done > 0; this covers the initial fetch, where
                # an edge may ignore the Range (200) or answer off-offset.
                want = seg_start + seg_done
                span_left = span - seg_done
                use_conn = conn
                status = resp_headers.status_code
                if status == 206:
                    rs, re, _ = _parse_content_range_full(resp_headers.headers.get("content-range"))
                    if rs == want:
                        # Exact length framing is also what makes a response
                        # keep-alive-safe (see _has_exact_content_length).
                        if _has_exact_content_length(resp_headers, span_left):
                            exact_206 = True
                        else:
                            print(
                                f"[DashProxy] {seg_label} 206 without exact length, trying next mirror"
                            )
                            try:
                                await conn.close()
                            except Exception:
                                pass
                            continue
                    elif rs is not None and re is not None and rs < want <= re:
                        print(f"[DashProxy] {seg_label} resume overlap, salvaging")
                        use_conn = _ResumedDashConn(conn, want - rs, span_left)
                    else:
                        print(
                            f"[DashProxy] {seg_label} offset mismatch "
                            f"(want {want}, got {resp_headers.headers.get('content-range')}), trying next mirror"
                        )
                        try:
                            await conn.close()
                        except Exception:
                            pass
                        continue
                elif status == 200:
                    try:
                        full_len = int(str(resp_headers.headers.get("content-length") or "").strip())
                    except (TypeError, ValueError):
                        full_len = None
                    if full_len is not None and full_len > want:
                        print(f"[DashProxy] {seg_label} ignored Range, salvaging full body")
                        use_conn = _ResumedDashConn(conn, want, span_left)
                    else:
                        try:
                            await conn.close()
                        except Exception:
                            pass
                        continue
                else:
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    continue
                try:
                    async for chunk in _yield_dash_body(use_conn, seg_label):
                        if cancel_event is not None and cancel_event.is_set():
                            try:
                                await use_conn.close()
                            except Exception:
                                pass
                            return _DOWNLOAD_CANCELLED
                        if written + seg_done + len(chunk) > max_bytes:
                            print(f"[DashProxy] {seg_label} exceeds limit {max_bytes}")
                            try:
                                await use_conn.close()
                            except Exception:
                                pass
                            return -1
                        pending_writes.append(chunk)
                        pending_len += len(chunk)
                        seg_done += len(chunk)
                        if progress_cb is not None:
                            progress_cb(len(chunk))
                        win_bytes += len(chunk)
                        now_w = time.monotonic()
                        if now_w - win_t0 >= _SLOW_WINDOW:
                            win_el = now_w - win_t0
                            if win_el > 0:
                                win_rate = win_bytes / win_el
                                if win_rate > win_best:
                                    win_best = win_rate
                                capable = (
                                    track_best_bps >= _SLOW_ARM_BPS or win_best >= _SLOW_ARM_BPS
                                )
                                if win_rate >= _SLOW_WINDOW_KBPS * 1024:
                                    slow_strikes = 0
                                elif capable:
                                    slow_strikes += 1
                            win_t0, win_bytes = now_w, 0
                            if slow_strikes >= _SLOW_WINDOW_STRIKES and slow_trips < len(order):
                                slow_trips += 1
                                raise _SlowDashBody(
                                    f"slow {win_rate / 1024:.0f} KB/s x{slow_strikes} windows "
                                    f"(< {_SLOW_WINDOW_KBPS} KB/s)"
                                )
                        if pending_len >= _DOWNLOAD_WRITE_BATCH:
                            batch = b"".join(pending_writes)
                            pending_writes.clear()
                            pending_len = 0
                            await asyncio.to_thread(file_obj.write, batch)
                except (_SlowDashBody, CdnConnectError, CdnProtocolError, CdnTimeoutError, OSError, TimeoutError) as exc:
                    try:
                        await use_conn.close()
                    except Exception:
                        pass
                    print(f"[DashProxy] {seg_label} cut after {seg_done}/{span} bytes ({exc}), failing over")
                    _note_mirror_bad(seg_host)
                    seg_clean = False
                    slow = isinstance(exc, _SlowDashBody)
                    if slow:
                        # Slow (not dead): cut the upstream connection and wait
                        # a full _SLOW_TRIP_PAUSE before reconnecting — a fresh
                        # hammer lands back in the penalty box. slow_streak
                        # still steps (drives the inter-segment pacing ladder).
                        slow_streak += 1
                        pause = _SLOW_TRIP_PAUSE
                        if note_cb is not None:
                            note_cb(f"Too slow, pausing {pause:g}s before retry…")
                    else:
                        # Settle before reconnecting: hammering a shedding edge
                        # with instant reconnects earns empty closes ("Bad status
                        # line") and deepens the hole. Cancel-aware
                        # (user-cancel still aborts promptly).
                        pause = 1.0
                    if cancel_event is not None:
                        try:
                            await asyncio.wait_for(cancel_event.wait(), timeout=pause)
                            return _DOWNLOAD_CANCELLED
                        except (asyncio.TimeoutError, TimeoutError):
                            pass
                    else:
                        await asyncio.sleep(pause)
                    continue
                if seg_done >= span and use_conn is conn and exact_206:
                    # Fully consumed a length-framed body on the raw conn:
                    # the socket sits exactly at the next boundary, so keep
                    # it for the next segment (no handshake, no slow-start
                    # restart). Anything else (salvage wraps, short bodies,
                    # reuses that errored) is closed as before.
                    keepalive = (conn, used)
                else:
                    try:
                        await use_conn.close()
                    except Exception:
                        pass
                if seg_done >= span:
                    break
                # Close-delimited body ended early with no error: resume the remainder.
                print(f"[DashProxy] {seg_label} short body ({seg_done}/{span} bytes), resuming remainder")
            if pending_len:
                batch = b"".join(pending_writes)
                pending_writes.clear()
                pending_len = 0
                await asyncio.to_thread(file_obj.write, batch)
            written += seg_done
            seg_el = time.monotonic() - seg_t0
            if seg_el > 0:
                track_best_bps = max(track_best_bps, seg_done / seg_el)
                if seg_clean and seg_attempt == 0 and slow_trips == 0 and seg_done / seg_el > _DOWNLOAD_GOOD_BPS:
                    slow_streak = 0
            _note_mirror_ok(seg_host)
            print(
                f"[DashProxy] {seg_label} done {seg_done}/{span} bytes in {seg_el:.1f}s "
                f"({seg_done / seg_el / 1024:.0f} KB/s) via {seg_host}"
            )
            seg_index += 1
            if note_cb is not None:
                note_cb(None)
            if written < total_size:
                # Pacing breather before the next segment (cancel-aware),
                # at the current ladder rung (escalated by slow trips).
                pause = _pace_delay(slow_streak)
                if cancel_event is not None:
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=pause)
                        return _DOWNLOAD_CANCELLED
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                else:
                    await asyncio.sleep(pause)
        if note_cb is not None:
            note_cb(None)
        return written
    except Exception as exc:
        print(f"[DashProxy] {label} segmented download error: {exc}")
        return -1
    finally:
        if keepalive is not None:
            try:
                await keepalive[0].close()
            except Exception:
                pass
            keepalive = None
        if file_obj is not None:
            await asyncio.to_thread(file_obj.close)


async def _download_track_file(
    urls,
    headers: dict,
    proxy_url: str,
    dest: str,
    total_size: int,
    label: str,
    max_bytes: int | None = None,
    progress_cb=None,
    cancel_event: asyncio.Event | None = None,
    note_cb=None,
    rewind_cb=None,
    refresh_cb=None,
    max_refreshes: int | None = None,
) -> int:
    """Download a track file, player-style when the size is known.

    With a known ``total_size`` (the normal case: callers peek it first) the
    track is fetched as sequential player-sized segments
    (:func:`_download_track_segmented`); when the size is unknown (peek
    failed) it falls back to the legacy whole-file resumable GET
    (:func:`_download_track_to_file`). Same return contract as both.
    """
    if total_size and total_size > 0:
        return await _download_track_segmented(
            urls,
            headers,
            proxy_url,
            dest,
            total_size,
            label,
            max_bytes=max_bytes,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            note_cb=note_cb,
            refresh_cb=refresh_cb,
            max_refreshes=max_refreshes,
        )
    return await _download_track_to_file(
        urls,
        headers,
        proxy_url,
        dest,
        max_bytes=max_bytes,
        progress_cb=progress_cb,
        cancel_event=cancel_event,
        note_cb=note_cb,
        rewind_cb=rewind_cb,
    )


async def _dash_ip_key(req) -> str:
    """Coarse per-IP bucket across all DASH fragment requests (abuse guard)."""
    return f"{get_client_ip(req)}:dash"


async def _dash_frag_key(req) -> str:
    """Per-fragment rate-limit bucket: path + Range header.

    Every fragment of a rendition shares one route path (the byte range rides
    in a header), so the default ``{ip}:{path}`` key lumps the whole stream
    into a single bucket — seeks, 2x playback, and abandon re-requests then
    429 legitimate burst traffic and dash.js stalls on our own 429s. Keying on
    the requested Range gives each fragment its own bucket while identical
    retries still share one.
    """
    rng = (req.headers.get("Range") or req.headers.get("range") or "").strip()
    return f"{get_client_ip(req)}:{req.path}:{rng}"


@dash_proxy_bp.route("/proxy/dash/<vid>/<int:idx>/<media_type>/<int:qn>/<int:cid>")
@rate_limit(limit=600, window=60, key_func=_dash_ip_key)
@rate_limit(limit=100, window=60, key_func=_dash_frag_key)
async def proxy_dash(vid, idx, media_type, qn, cid):
    """Range-capable DASH track proxy through the WARP SOCKS5 tunnel.

    Resolves the track URL from the cached dash JSON, validates it, and proxies
    the client's Range request upstream, faithfully emitting ``206 Partial
    Content`` with ``Content-Range``/``Content-Length``/``ETag`` so the sidx
    (SegmentBase indexRange) can drive byte-range seeking in dash.js.

    Each track is tried on at most two mirrors (healthy-first): dash.js
    abandons slow fragments itself and retries with ABR downshift, so deeper
    per-request sweeps only outlive client patience. Individual objects
    sometimes stall on one edge while siblings serve in milliseconds — the
    sick-mirror memory routes the next fragment straight to a healthy one.
    """
    started = time.monotonic()
    host = "-"
    conn = None
    if media_type not in ("video", "audio"):
        return Response("Bad Request: media_type must be video|audio", status=400)

    try:
        dash_data = await _load_dash_data(vid, idx)
        if not dash_data:
            return Response("Not Found", status=404)
        track = _lookup_track(dash_data, media_type, qn, cid)
        if not track:
            return Response("Not Found", status=404)

        urls = _dash_candidate_urls(track)
        if not urls:
            return Response("Not Found: track has no URL", status=404)
        # Full SSRF check (allowlist + private-IP DNS reject, same bar as
        # /proxy/video/) before touching any candidate edge. Checked
        # concurrently: sequential awaits cost one DNS RTT per candidate.
        verdicts = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls))
        urls = [u for u, ok in zip(urls, verdicts, strict=False) if ok]
        if not urls:
            return Response("Forbidden: Invalid proxy target", status=403)
        # Fail fast, not heroically: dash.js abandons slow fragments itself
        # (~7s, with 5 retries + ABR downshift), so churning more than two
        # mirrors per request only guarantees the client gives up first and
        # the work is wasted. Sick-mirror memory (health ordering) makes
        # those two attempts count.
        urls = _order_urls_by_health(urls)[:_PLAYBACK_MAX_MIRRORS]
        if not urls:
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
        label = f"{vid}:{idx} {media_type}/{qn}/{cid}"
        try:
            conn, resp_headers, used_url, content_length, content_range = await _fetch_dash_track(
                urls, headers, proxy_url, label, attempt_timeout=DASH_PLAYBACK_ATTEMPT_TIMEOUT
            )
        except _DashUpstreamError as exc:
            old_stamp = _playurl_url_expiry(urls[0]) if urls else None
            provably_expired = old_stamp is not None and time.time() > old_stamp
            if not exc.forbidden and not provably_expired:
                print(f"[DashProxy] proxy_dash error: {exc}")
                return Response("Upstream error", status=502)
            # Every mirror rejected the signature (expired playurl): fetch a
            # fresh playurl once and retry, like the official player's
            # backup-URL-then-prefetchPlayUrl recovery. Gated on 403-class or
            # provably-past signatures so ordinary dead videos and slow edges
            # don't amplify upstream playurl calls.
            reason = "forbidden" if exc.forbidden else "expired"
            print(f"[DashProxy] {label} URLs {reason} ({exc}), refreshing playurl once")
            retry = await _retry_proxy_with_fresh_playurl(
                vid, idx, media_type, qn, cid, urls, headers, proxy_url, label,
                attempt_timeout=DASH_PLAYBACK_ATTEMPT_TIMEOUT,
            )
            if retry is None:
                return Response("Upstream error", status=502)
            (conn, resp_headers, used_url, content_length, content_range), urls = retry

        host = urlparse(used_url).hostname or "-"
        pending = [u for u in urls if u != used_url]
        orig_range = headers.get("range")
        yielded = 0
        try:
            downstream_total = int(str(content_length).strip()) if content_length is not None else None
        except (TypeError, ValueError):
            downstream_total = None

        async def generate():
            nonlocal conn, host, yielded
            while True:
                try:
                    # No speed trip here (floor_watchdog=False): slow is not
                    # failure — backup URLs are for failures, and dash.js owns
                    # slowness (abandon + ABR). Idle wedges still trip.
                    async for chunk in _yield_dash_body(conn, label, floor_watchdog=False):
                        yielded += len(chunk)
                        yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    # Client went away mid-stream: just close upstream.
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    print(f"[DashProxy] {label} {host} client disconnected after {yielded} bytes")
                    raise
                except (_SlowDashBody, CdnProtocolError, CdnTimeoutError, CdnConnectError, OSError, TimeoutError) as exc:
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    _note_mirror_bad(host)
                    kind = "body too slow" if isinstance(exc, _SlowDashBody) else "mid-body cut"
                    if not pending:
                        # No mirrors left: end the stream early WITHOUT
                        # raising. Downstream Content-Length was already
                        # emitted, so the short body surfaces as a length
                        # mismatch and dash.js retries the Range — instead
                        # of a 500 + TaskGroup traceback from re-raising
                        # inside the generator.
                        print(f"[DashProxy] {label} {host} {kind} after {yielded} bytes ({exc}); mirrors exhausted, truncating")
                        return
                    print(f"[DashProxy] {label} {host} {kind} after {yielded} bytes ({exc}), trying next mirror")
                    try:
                        conn, used, _ = await _failover_dash_conn(
                            pending, headers, proxy_url, label, orig_range, yielded, downstream_total,
                            attempt_timeout=DASH_PLAYBACK_ATTEMPT_TIMEOUT,
                        )
                    except _DashUpstreamError as exc2:
                        print(f"[DashProxy] {label} body failover failed: {exc2}")
                        return
                    host = urlparse(used).hostname or "-"
                    continue
                try:
                    await conn.close()
                except Exception:
                    pass
                _note_mirror_ok(host)
                return

        proxy_resp = Response(generate(), status=resp_headers.status_code)
        proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
        proxy_resp.headers["X-Accel-Buffering"] = "no"
        proxy_resp.headers["Accept-Ranges"] = "bytes"

        for k, v in resp_headers.headers.items():
            if k in [
                "content-type",
                "etag",
                "last-modified",
                "cache-control",
            ]:
                proxy_resp.headers[k] = v

        # Computable lengths are load-bearing for dash.js (throughput, ABR,
        # abandon rule): always emit the validated/derived values instead of
        # blindly forwarding upstream, which may omit or mismatch them.
        if content_length is not None:
            proxy_resp.headers["Content-Length"] = content_length
        if content_range is not None:
            proxy_resp.headers["Content-Range"] = content_range

        # Segments are immutable for a given track+Range, so let the browser
        # cache them aggressively: ABR flapping between renditions then
        # re-requests identical ranges, which become instant cache hits
        # instead of fresh trips over a shaky uplink.
        proxy_resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"

        if resp_headers.status_code in [200, 206]:
            current_ct = proxy_resp.headers.get("Content-Type", "").lower()
            if not current_ct or "application/octet-stream" in current_ct:
                proxy_resp.headers["Content-Type"] = "video/mp4" if media_type == "video" else "audio/mp4"
        return proxy_resp
    except asyncio.CancelledError:
        # Client (dash.js/Caddy) abandoned a hung upstream wait. Re-raise so
        # Granian handles the disconnect, but leave a trace: these used to be
        # the mysterious log-free 500s.
        print(f"[DashProxy] {media_type}/{qn}/{cid} {host} abandoned after {time.monotonic() - started:.1f}s")
        if conn is not None:
            await conn.close()
        raise
    except Exception as exc:
        print(
            f"[DashProxy] proxy_dash error: {vid}:{idx} {media_type}/{qn}/{cid} {host} "
            f"after {time.monotonic() - started:.1f}s: {exc}"
        )
        if conn is not None:
            await conn.close()
        return Response("Upstream error", status=502)


@app.route("/video/dash/<vid>/<int:idx>/manifest.mpd")
@rate_limit(**RATE_LIMITS["proxy"])
async def video_dash_manifest_view(vid, idx):
    """Serve the isoff-on-demand MPD manifest for a video part.

    ``?fresh=1`` drops the cached playurl first so the manifest is built
    from freshly re-fetched CDN URLs — the player recovery path for expired
    signatures (backend half of the official player's ``prefetchPlayUrl``).
    """
    if request.args.get("fresh") == "1":
        try:
            await appredis.delete(f"miku_dash_{vid}_{idx}")
            await appredis.delete(f"miku_dash_{vid}_{idx}:miss")
        except Exception:
            pass
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
        self.actual_qn = None  # quality actually being fetched (fallback of qual)
        self.state = "queued"
        self.total_bytes = 0
        self.done_bytes = 0
        self.speed_bps = 0.0
        self.status_note = None  # transient user-facing note (e.g. retry backoff)
        self.filename = _safe_download_filename(vid, idx, qual)
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
            "actual_qn": self.actual_qn,
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
                ((j.updated_at, jid) for jid, j in _download_jobs.items() if j.state in ("ready", "error", "cancelled"))
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
    if not _is_valid_vid(vid):
        raise RuntimeError("invalid video ID")
    await _sweep_jobs()
    async with _jobs_lock:
        active_jobs = sum(job.state not in ("ready", "error", "cancelled") for job in _download_jobs.values())
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


async def _peek_content_length(
    url: str | list, headers: dict, proxy_url: str, cancel_event: asyncio.Event | None = None
) -> int:
    """Fetch only response headers to learn a track's size (0 if unknown).

    ``url`` may be a single URL or a candidate mirror list; each mirror is
    tried in order until one answers 200/206 with a Content-Length. Opens a
    throwaway connection per attempt and closes it without reading the body.
    Raises RuntimeError if the track exceeds the per-track size cap.
    """
    if isinstance(url, (list, tuple)):
        candidates = [u for u in url if isinstance(u, str) and u]
    else:
        candidates = [url] if isinstance(url, str) and url else []
    last_exc: Exception | None = None
    for cand in candidates:
        try:
            conn, resp_headers = await _open_cdn_track(cand, headers, proxy_url)
        except Exception as exc:
            last_exc = exc
            continue
        try:
            if resp_headers.status_code not in (200, 206):
                last_exc = RuntimeError(f"HTTP {resp_headers.status_code}")
                continue
            cl = (resp_headers.headers or {}).get("content-length")
            if not cl:
                return 0
            try:
                size = int(cl)
            except (TypeError, ValueError):
                return 0
            if size > _max_download_track_bytes():
                raise RuntimeError(
                    f"this instance maximum allowed download size is {_max_download_size_mb()} MB"
                )
            return max(size, 0)
        finally:
            await conn.close()
    if last_exc is not None:
        print(f"[DashProxy] size peek failed on all mirrors: {last_exc}")
    return 0


async def _run_dash_job(job: _DownloadJob, dash_data: dict):
    """Download best video (<=1080p cap) + audio tracks, then ffmpeg-mux."""
    max_qn = min(job.qual, _FREE_DOWNLOAD_MAX_QN) if job.qual > 0 else _FREE_DOWNLOAD_MAX_QN
    video, audio = _pick_download_tracks(dash_data, max_qn)
    if not video or not audio:
        raise RuntimeError("no suitable DASH tracks for download")
    vurls = _dash_candidate_urls(video)
    aurls = _dash_candidate_urls(audio)
    if not vurls or not aurls:
        raise RuntimeError("track has no URL")
    v_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in vurls))
    a_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in aurls))
    vurls = [u for u, ok in zip(vurls, v_checks, strict=False) if ok]
    aurls = [u for u, ok in zip(aurls, a_checks, strict=False) if ok]
    if not vurls or not aurls:
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
    job.actual_qn = actual_qn
    job.filename = _safe_download_filename(job.vid, job.idx, actual_qn)

    vsize = await _peek_content_length(vurls, headers, proxy_url)
    job.throw_if_cancelled()
    asize = await _peek_content_length(aurls, headers, proxy_url)
    job.throw_if_cancelled()
    job.total_bytes = vsize + asize
    job.state = "downloading"
    job.touch()

    try:
        v_qn = int(video.get("id") or 0)
    except (TypeError, ValueError):
        v_qn = 0
    try:
        v_cid = int(video.get("codecid") or 0)
    except (TypeError, ValueError):
        v_cid = 0
    try:
        a_qn = int(audio.get("id") or 0)
    except (TypeError, ValueError):
        a_qn = 0
    try:
        a_cid = int(audio.get("codecid") or 0)
    except (TypeError, ValueError):
        a_cid = 0

    async def _refresh_job_track_urls(media_type: str, qn: int, cid: int):
        """Fresh-playurl mirrors for the *same* track (byte-identical content).

        Returns a safety-checked candidate list, or None. A refreshed
        response that no longer carries this exact track id is declined:
        switching qualities mid-file would corrupt the download.
        """
        fresh = await _refresh_dash_data(job.vid, job.idx)
        if not has_valid_dash_tracks(fresh):
            return None
        track = _lookup_track(fresh, media_type, qn, cid)
        if track is None:
            pick = _pick_download_tracks(fresh, max_qn)
            track = pick[0] if media_type == "video" else pick[1]
        if track is None:
            return None
        try:
            if int(track.get("id") or -1) != qn:
                print(
                    f"[DashProxy] job {job.job_id} refresh changed track id "
                    f"(want {qn}, got {track.get('id')}), declining"
                )
                return None
        except (TypeError, ValueError):
            return None
        urls = _dash_candidate_urls(track)
        checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls))
        urls = [u for u, ok in zip(urls, checks, strict=False) if ok]
        return urls or None

    async def _refresh_video_urls():
        return await _refresh_job_track_urls("video", v_qn, v_cid)

    async def _refresh_audio_urls():
        return await _refresh_job_track_urls("audio", a_qn, a_cid)

    # Video + audio fetch concurrently (like dash.js's two adaptation sets):
    # independent files, shared progress/cancel accounting. All shared-state
    # updates are synchronous (no awaits inside), so coroutines cannot
    # interleave mid-update on the single event loop. Callees never raise
    # (they return byte counts / _DOWNLOAD_CANCELLED), so gather is safe.
    vn, an = await asyncio.gather(
        _download_track_file(
            vurls,
            headers,
            proxy_url,
            vpath,
            vsize,
            f"{job.vid}:{job.idx} video",
            progress_cb=job.add_progress,
            cancel_event=job.cancel_event,
            note_cb=job.set_note,
            rewind_cb=job.rewind_progress,
            refresh_cb=_refresh_video_urls,
        ),
        _download_track_file(
            aurls,
            headers,
            proxy_url,
            apath,
            asize,
            f"{job.vid}:{job.idx} audio",
            progress_cb=job.add_progress,
            cancel_event=job.cancel_event,
            note_cb=job.set_note,
            rewind_cb=job.rewind_progress,
            refresh_cb=_refresh_audio_urls,
        ),
    )
    if vn == _DOWNLOAD_CANCELLED or an == _DOWNLOAD_CANCELLED:
        raise _JobCancelled()
    job.throw_if_cancelled()
    if vn < 0:
        raise RuntimeError("video track download failed")
    if an < 0:
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
        play_data = await asyncio.wait_for(video_get_dash_for_qn(v, job.idx), timeout=DASH_FETCH_TIMEOUT)
    except Exception as exc:
        print(f"[DashProxy] job {job.job_id} durl fetch failed: {exc}")
        play_data = None
    job.throw_if_cancelled()
    try:
        url, qn, ext = await _resolve_durl_download(v, job.vid, job.idx, job.qual, play_data=play_data)
    except _DurlResolveError as exc:
        raise RuntimeError(str(exc)) from None
    job.actual_qn = qn
    durl_urls = [url]
    try:
        bak = await appredis.get(f"mikuinv_{job.vid}_{job.idx}_{qn}_bak")
        if bak:
            if isinstance(bak, bytes):
                bak = bak.decode()
            if bak and bak != url and _is_safe_dash_url(bak):
                durl_urls.append(bak)
    except Exception:
        pass
    safe_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in durl_urls))
    durl_urls = [u for u, ok in zip(durl_urls, safe_checks, strict=False) if ok]
    if not durl_urls:
        raise RuntimeError("invalid CDN target")

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix=f"miku_dl_{job.job_id}_")
    job.tmpdir = tmpdir
    outpath = os.path.join(tmpdir, f"out{ext}")
    job.outpath = outpath
    job.filename = _safe_download_filename(job.vid, job.idx, qn, ext)

    headers = await _build_dash_cdn_headers()
    proxy_url = Network.get_proxy()
    job.total_bytes = await _peek_content_length(durl_urls, headers, proxy_url)
    job.throw_if_cancelled()
    job.state = "downloading"
    job.touch()

    async def _refresh_durl_urls():
        """Re-resolve fresh progressive URLs for the *same* quality.

        Drops the quality-list cache so every quality is re-fetched (never
        reusing the possibly-stale initial ``durl``), then resolves again.
        A refreshed response pointing at a different quality is declined:
        switching files mid-download would corrupt it.
        """
        try:
            await appredis.delete(f"mikuinv_{job.vid}_{job.idx}")
        except Exception:
            pass
        ladder = {
            "support_formats": (play_data or {}).get("support_formats") or [],
            "quality": job.qual,
            "durl": None,
        }
        try:
            url2, qn2, _ext2 = await _resolve_durl_download(v, job.vid, job.idx, job.qual, play_data=ladder)
        except _DurlResolveError:
            return None
        if qn2 != qn:
            print(f"[DashProxy] job {job.job_id} refresh changed quality (want {qn}, got {qn2}), declining")
            return None
        urls2 = [url2]
        try:
            bak2 = await appredis.get(f"mikuinv_{job.vid}_{job.idx}_{qn2}_bak")
            if bak2:
                if isinstance(bak2, bytes):
                    bak2 = bak2.decode()
                if bak2 and bak2 != url2 and _is_safe_dash_url(bak2):
                    urls2.append(bak2)
        except Exception:
            pass
        checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls2))
        urls2 = [u for u, ok in zip(urls2, checks, strict=False) if ok]
        return urls2 or None

    n = await _download_track_file(
        durl_urls,
        headers,
        proxy_url,
        outpath,
        job.total_bytes,
        f"{job.vid}:{job.idx} progressive",
        progress_cb=job.add_progress,
        cancel_event=job.cancel_event,
        note_cb=job.set_note,
        rewind_cb=job.rewind_progress,
        refresh_cb=_refresh_durl_urls,
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
    safe_name = _safe_download_filename(job.vid, job.idx, job.actual_qn or job.qual)
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
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
