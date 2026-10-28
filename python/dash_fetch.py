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

import asyncio
import time
from urllib.parse import parse_qs, urlparse

import orjson
from api import video
from dash_common import (
    DASH_CACHE_TTL,
    DASH_FETCH_TIMEOUT,
    _extract_ep_id,
)
from shared import Network, appcred, appredis, safe_json_loads


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
            if (
                data
                and isinstance(data, dict)
                and (data.get("dash") or data.get("durl") or data.get("support_formats"))
            ):
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
        async with asyncio.timeout(4.0):
            res = await api.request()
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
        except TypeError, ValueError:
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
        except TypeError, ValueError:
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
    except TypeError, ValueError:
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
    for f in (play_data or {}).get("support_formats") or []:
        if not isinstance(f, dict) or f.get("quality") is None:
            continue
        try:
            desc = f.get("new_description") or f.get("display_desc")
            if desc:
                fmt_desc[int(f["quality"])] = desc
        except TypeError, ValueError:
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
    except TypeError, ValueError:
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


def _choose_durl_entry(supported: list, want: int) -> tuple:
    """Pick (quality, ext) from a durl supported_src list.

    Best quality at or below the request (mirrors /proxy/video/ fallback
    order); want=0 (e.g. listen-page download) means "best available".
    """
    try:
        want = int(want)
    except TypeError, ValueError:
        want = 0
    candidates = [s for s in supported if s["quality"] <= want] if want > 0 else []
    chosen = candidates[0] if candidates else supported[0]
    return chosen["quality"], chosen.get("ext") or ".mp4"


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
            async with asyncio.timeout(25.0):
                supported = await fetch_durl_supported_src(v, vid, idx, play_data=node_data, force=force)
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
# Deliberately lock-free: registration and settling are synchronous, so they
# cannot be interrupted by a cancellation landing on the await, which would
# otherwise strand joiners on a dead future.
_dash_inflight: dict[str, asyncio.Future] = {}
_DASH_MISS_TTL = 30


async def _fetch_and_cache_dash_data(vid, idx, key):
    """Fetch dash play info and cache it. Returns the dash dict or None."""

    v = video.Video(bvid=vid, credential=appcred)
    try:
        async with asyncio.timeout(DASH_FETCH_TIMEOUT):
            data = await video_get_dash_for_qn(v, idx)
    except TimeoutError:
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
    A cancelled owner still settles joiners (with ``None``) instead of
    leaving them waiting on a future that will never resolve.

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
    fut = _dash_inflight.get(key)
    if fut is None:
        fut = _dash_inflight[key] = asyncio.get_running_loop().create_future()
    else:
        # Shield: a cancelled joiner must not cancel the shared future for
        # the others (and the owner settles it either way).
        return await asyncio.shield(fut)
    result = None
    try:
        result = await _fetch_and_cache_dash_data(vid, idx, key)
    except Exception as exc:
        print(f"[DashProxy] dash fetch failed for {vid}:{idx}: {exc}")
        result = None
    finally:
        # Runs on cancellation too: a client disconnect must never leave
        # joiners waiting on an unsettled future. They get None (no negative
        # cache was written, so the next request retries) while the
        # CancelledError keeps propagating.
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
            except TypeError, ValueError:
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


