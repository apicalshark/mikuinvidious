# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import re
import sys

import orjson
from quart import Response, g, request

from miku.api import comment, user, video
from miku.api.bvid import av2bv
from miku.core.rate_limit import RATE_LIMITS, get_client_ip, ip_family_key, rate_limit
from miku.core.singleflight import SingleflightGroup
from miku.core.url_signing import sign_dash_manifest_url
from miku.dash.common import _is_valid_vid
from miku.media.extra import video_get_src_for_qn
from miku.state import (
    app,
    appcred,
    appredis,
    cache_get,
    cache_get_stale,
    cache_minutes,
    cache_set,
    render_template_with_theme,
    safe_json_loads,
)

_background_tasks = set()
_video_sf = SingleflightGroup()


async def _fetch_owner_meta(vinfo: dict) -> dict:
    owner = vinfo.get("owner") if isinstance(vinfo, dict) else None
    mid = owner.get("mid") if isinstance(owner, dict) else None
    if not mid:
        return {}
    cache_key = f"owner:meta:{mid}"
    ttl = max(cache_minutes("space_minutes"), 30) * 60
    cached = await cache_get(cache_key, ttl)
    if isinstance(cached, dict) and cached:
        return cached
    try:
        async with asyncio.timeout(2.5):
            uinfo = await user.User(mid, credential=appcred).get_user_info()
    except Exception:
        return {}
    if not isinstance(uinfo, dict) or not uinfo:
        return {}
    raw_level_info = uinfo.get("level_info")
    meta = {
        "pendant": uinfo.get("pendant"),
        "level": uinfo.get("level"),
        "level_info": raw_level_info if isinstance(raw_level_info, dict) else {},
        "is_senior_member": uinfo.get("is_senior_member"),
        "vip": uinfo.get("vip"),
        "ip_location": uinfo.get("ip_location"),
    }
    await cache_set(cache_key, meta, ttl)
    return meta


async def _component_ip_key(req) -> str:
    """One coarse bucket per client IP across all ``/api/component/*`` routes.

    The default ``ip:path`` key embeds the vid, so a crawler walking video
    ids got a fresh allowance per video; this caps the IP as a whole while
    the per-path limit keeps throttling repeat hits on one video.
    """
    return f"{get_client_ip(req)}:component"


# Comment routes are also reached from video_listen.html, which serves audio
# pages too (au/am ids, or bare digits) — accept those shapes as well.
_COMMENT_OID_RE = re.compile(r"^(BV[A-Za-z0-9]{10}|(?:av|au|am)?\d{1,20})$")


def _is_valid_comment_oid(vid: str) -> bool:
    return bool(vid) and bool(_COMMENT_OID_RE.match(vid))


_ALLOWED_COMMENT_TYPES = frozenset(t.value for t in comment.CommentResourceType)


def _parse_comment_type() -> int:
    raw = request.args.get("type", comment.CommentResourceType.VIDEO.value, type=int)
    return raw if raw in _ALLOWED_COMMENT_TYPES else comment.CommentResourceType.VIDEO.value


def _bad_vid_response() -> Response:
    return Response("Bad Request: invalid video ID", status=400)


@app.route("/video_listen/<vid>")
@app.route("/video_listen/<vid>:<idx>")
@app.route("/video_listen/<vid>/")
@app.route("/video_listen/<vid>:<idx>/")
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("video_listen"))
async def video_listen_view(vid, idx=0):
    # Validate video ID format
    import re

    if not re.match(r"^(BV[a-zA-Z0-9]{10}|av\d+)$", vid):
        return Response("Invalid video ID format", status=400)

    ato = request.args.get("ato") == "1"
    try:
        idx = int(idx)
        if idx < 0:
            return Response("Invalid video index", status=400)
    except TypeError, ValueError:
        return Response("Invalid video index", status=400)
    vid = av2bv(vid[2:]) if vid.startswith("av") else vid
    v = video.Video(bvid=vid, credential=appcred)

    async def get_audio_url():
        try:
            exists = await appredis.exists(f"mikuinv_{vid}_{idx}_0")
        except Exception:
            exists = False
        if not exists:
            try:
                vsrc = await video_get_src_for_qn(v, idx, 16)
            except Exception:
                return
            if "durl" in vsrc and vsrc["durl"]:
                try:
                    await appredis.setex(f"mikuinv_{vid}_{idx}_0", 1800, vsrc["durl"][0]["url"])
                except Exception:
                    pass

    cache_ttl = cache_minutes("video_minutes") * 60
    cache_key = f"video:data:{vid}:{idx}"
    if cache_ttl > 0:
        data = await cache_get(cache_key, cache_ttl)
        if (
            isinstance(data, dict)
            and isinstance(data.get("vinfo"), dict)
            and (data["vinfo"].get("bvid") or data["vinfo"].get("title"))
            and isinstance(data.get("vtags"), list)
            and isinstance(data.get("vrelated"), list)
            and isinstance(data.get("vset"), list)
            and data.get("vset")
        ):
            vinfo, vtags, vrelated, vset = (
                data["vinfo"],
                data["vtags"],
                data["vrelated"],
                data["vset"],
            )
            res_cached = await asyncio.gather(
                comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE),
                get_audio_url(),
                return_exceptions=True,
            )
            vcomments = res_cached[0] if isinstance(res_cached[0], dict) else {"page": {"count": 0}, "replies": []}
            owner_info = await _fetch_owner_meta(vinfo)
            html = await render_template_with_theme(
                "video_listen.html",
                vid=vid,
                vinfo=vinfo,
                owner_info=owner_info,
                vrelated=vrelated[:10],
                vcomments=vcomments,
                keywords=",".join(x.get("tag_name", "") for x in vtags if isinstance(x, dict)),
                vtags=vtags,
                ato=ato,
                idx=idx,
                vset=vset,
            )
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})

    results = await asyncio.gather(
        v.get_info(),
        v.get_tags(idx),
        v.get_related(),
        comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE),
        v.get_pages(),
        get_audio_url(),
        return_exceptions=True,
    )

    # Core data check
    if isinstance(results[0], BaseException) or not isinstance(results[0], dict):
        err_msg = str(results[0]) if results[0] else "Bilibili returned empty data (possibly region-restricted)"
        return await render_template_with_theme(
            "error.html",
            status="Audio mode load failed",
            desc=err_msg,
            suggest="This content may be region-restricted or removed.",
        ), 404

    vinfo = results[0]
    vtags = results[1] if isinstance(results[1], list) else []
    vrelated = results[2] if isinstance(results[2], list) else []
    vcomments = results[3] if isinstance(results[3], dict) else {"page": {"count": 0}, "replies": []}
    vset = results[4] if isinstance(results[4], list) else [{"page": 1, "part": vid}]
    if (
        cache_ttl > 0
        and isinstance(results[1], list)
        and isinstance(results[2], list)
        and isinstance(results[4], list)
        and results[4]
        and (vinfo.get("bvid") or vinfo.get("title"))
    ):
        await cache_set(
            cache_key,
            {"vinfo": vinfo, "vtags": vtags, "vrelated": vrelated, "vset": vset},
            cache_ttl,
        )
    owner_info = await _fetch_owner_meta(vinfo)
    html = await render_template_with_theme(
        "video_listen.html",
        vid=vid,
        vinfo=vinfo,
        owner_info=owner_info,
        vrelated=vrelated[:10],
        vcomments=vcomments,
        keywords=",".join(x.get("tag_name", "") for x in vtags),
        vtags=vtags,
        ato=ato,
        idx=idx,
        vset=vset,
    )
    return Response(html, status=200, content_type="text/html", headers={"X-Cache": "MISS"})


# --- ASYNC COMPONENT API ---


def _is_final_play_data(data) -> bool:
    """True when play data is renderable (dash/durl) or a terminal paywall marker."""
    return bool(isinstance(data, dict) and (data.get("paywall") or data.get("dash") or data.get("durl")))


async def _resolve_progressive_sources(v, vid, idx, dash_data, ep_id, paywall, cid=None) -> list:
    """Progressive fallback list; [] for paywalled content (gated on every endpoint)."""
    if paywall:
        return []
    try:
        from miku.dash import fetch_durl_supported_src

        async with asyncio.timeout(20.0):
            return await fetch_durl_supported_src(v, vid, idx, play_data=dash_data, ep_id=ep_id, cid=cid)
    except Exception as e:
        print(f"[Player] durl fallback failed for {vid}:{idx}: {e}")
        return []


@app.route("/api/component/player/<vid>/<int:idx>")
@rate_limit(**RATE_LIMITS["loose"], key_func=_component_ip_key)
async def api_component_player(vid, idx):
    if not _is_valid_vid(vid):
        return _bad_vid_response()
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce
    v = video.Video(bvid=vid, credential=appcred)
    ep_id = request.args.get("ep_id")
    if ep_id and ep_id.isdigit():
        ep_id = int(ep_id)
    else:
        ep_id = None

    async def get_dash_data():
        """Fetch canonical play info (DASH, or durl-only for some uploads).

        Returns the raw play-data dict when it carries either a ``dash``
        node or a ``durl`` node. DASH payloads are cached under
        ``miku_dash_*``; durl-only payloads are returned uncached here and
        their per-quality URLs are cached under ``mikuinv_*`` by the
        progressive fallback below.
        """
        try:
            cached = await appredis.get(f"miku_dash_{vid}_{idx}")
        except Exception as e:
            # A sick Redis must degrade to a playurl fetch, not fail the page.
            print(f"[Player] dash cache read failed for {vid}:{idx}: {e}")
            cached = None
        if cached:
            cached_data = safe_json_loads(cached)
            if isinstance(cached_data, dict) and cached_data.get("dash"):
                return cached_data
        try:
            from miku.dash import video_get_dash_for_qn

            async with asyncio.timeout(8.0):
                data = await video_get_dash_for_qn(v, idx, ep_id=ep_id, cid=pgc_cid)
        except Exception as e:
            print(f"[Player] playurl fetch failed for {vid}:{idx}: {e}")
            return None
        if _is_final_play_data(data):
            if isinstance(data, dict) and data.get("dash"):
                try:
                    await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(data))
                except Exception as e:
                    print(f"[Player] dash cache write failed for {vid}:{idx}: {e}")
            return data
        return None

    # Known PGC cid from the season lookup below (None for plain UGC).
    # Threaded into playurl resolution so a gated UGC cid lookup can't
    # veto the PGC path. Defined here because get_dash_data() above
    # closes over it and runs after this block.
    pgc_cid = None
    try:
        if ep_id:
            from miku.api.client import Api

            api = Api(
                "https://api.bilibili.com/pgc/view/web/season",
                "GET",
                verify=(not not (appcred and appcred.sessdata)),
                credential=appcred,
            )
            api.params = {"ep_id": ep_id}
            pgc_data = await api.request()
            res = pgc_data.get("result", pgc_data)
            eps = res.get("episodes", [])
            current_ep = next((e for e in eps if e["id"] == ep_id), None)
            if not current_ep:
                for section in res.get("section", []):
                    for ep in section.get("episodes", []):
                        if ep["id"] == ep_id:
                            current_ep = ep
                            break
                    if current_ep:
                        break

            if current_ep:
                vinfo = {
                    "pic": current_ep.get("cover") or res.get("cover"),
                    "title": f"{res.get('title', '')} - {current_ep.get('title', '')}",
                }
                pgc_cid = current_ep.get("cid")
            else:
                vinfo = await v.get_info()
        else:
            vinfo = await v.get_info()
    except Exception:
        vinfo = {"pic": ""}

    dash_data = await get_dash_data()
    from miku.dash import has_valid_dash_tracks

    paywall = bool((dash_data or {}).get("paywall"))
    is_dash = has_valid_dash_tracks(dash_data)
    dash_url = sign_dash_manifest_url(vid, idx) if is_dash else ""
    dash_video_tracks = ((dash_data or {}).get("dash") or {}).get("video") or []
    dash_tracks_by_quality = {}
    for track in dash_video_tracks:
        if not isinstance(track, dict):
            continue
        quality = track.get("id")
        segment_base = track.get("SegmentBase") or {}
        if quality is not None and segment_base.get("indexRange"):
            dash_tracks_by_quality.setdefault(str(quality), track)
    if is_dash:
        # Only offer qualities backed by an actual track in the manifest.
        # support_formats advertises every quality (e.g. 4K) even when the
        # playurl response carries no playable track for it (anonymous
        # sessions top out at qn 80); listing those would highlight a
        # quality the player can never render.
        supported_src = [
            {
                "quality": f.get("quality"),
                "new_description": f.get("new_description") or f.get("display_desc") or "",
                "bandwidth": (dash_tracks_by_quality.get(str(f.get("quality"))) or {}).get("bandwidth"),
            }
            for f in (dash_data or {}).get("support_formats") or []
            if isinstance(f, dict) and f.get("quality") is not None and str(f.get("quality")) in dash_tracks_by_quality
        ]
    else:
        # Progressive (durl) fallback: some UGC uploads return no DASH
        # ``dash`` node at all — only a progressive
        # MP4 ``durl``. Serve those through the native /proxy/video/ path.
        supported_src = await _resolve_progressive_sources(v, vid, idx, dash_data, ep_id, paywall, cid=pgc_cid)

    return await render_template_with_theme(
        "components/player_part.html",
        vid=vid,
        vinfo=vinfo,
        idx=idx,
        supported_src=supported_src,
        is_live=False,
        is_dash=is_dash,
        dash_url=dash_url,
        subtitles=await _get_subtitles(v, idx),
        paywall=paywall,
    )


async def _get_subtitles(v, idx) -> list:
    """Best-effort subtitle list for the player (never delays rendering).

    Login-gated like PipePipe's ``ai_subtitle`` cookie function: anonymous
    sessions get []. Failures/timeouts degrade to no tracks.
    """
    try:
        from miku.state import appcred as _cred

        if not (_cred and _cred.sessdata):
            return []
        async with asyncio.timeout(6.0):
            subs = await v.get_subtitle_meta(page_index=idx)
        from miku.media.res import _subtitle_entries

        return _subtitle_entries(subs)
    except Exception as e:
        print(f"[Player] subtitle list failed: {e}")
        return []


@app.route("/api/component/meta/<vid>/<int:idx>")
@rate_limit(**RATE_LIMITS["loose"], key_func=_component_ip_key)
async def api_component_meta(vid, idx):
    if not _is_valid_vid(vid):
        return _bad_vid_response()
    # Use passed CSP nonce from main page to avoid CSP mismatch
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce

    def debug(*args):
        print("[comments]", *args, file=sys.stderr, flush=True)

    comments_closed = False
    try:
        raw_result = await comment.get_comments(
            vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE, timeout=4.0
        )
    except Exception as exc:
        debug(f"safe_api exception for vid={vid}: {type(exc).__name__}: {exc}")
        comments_closed = getattr(exc, "code", None) == comment.COMMENTS_CLOSED_CODE
        raw_result = None

    _empty = {"page": {"count": 0}, "replies": [], "next_offset": "", "is_end": True}
    vcomments = raw_result if raw_result and not isinstance(raw_result, Exception) else _empty

    return await render_template_with_theme(
        "components/meta_part.html",
        vid=vid,
        idx=idx,
        vcomments=vcomments,
        is_live=False,
        comments_closed=comments_closed,
    )


@app.route("/api/component/comments/<vid>/<int:idx>/more")
@rate_limit(**RATE_LIMITS["loose"], key_func=_component_ip_key)
async def api_component_meta_more(vid, idx):
    """Fetch the next page of top-level comments (infinite scroll, matching PipePipe)."""
    if not _is_valid_comment_oid(vid):
        return _bad_vid_response()
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce

    next_offset = request.args.get("next", "")
    if not isinstance(next_offset, str) or len(next_offset) > 512:
        next_offset = ""
    comment_type = _parse_comment_type()
    order = comment.OrderType.TIME if request.args.get("mode", type=int) == 2 else comment.OrderType.LIKE

    try:
        raw = await comment.get_comments(vid, comment_type, 1, order, next_offset=next_offset, timeout=4.0)
    except Exception:
        raw = None
    failed = not isinstance(raw, dict)
    vcomments = {"page": {"count": 0}, "replies": [], "next_offset": "", "is_end": True} if failed else raw

    body = await render_template_with_theme(
        "components/comment_items.html",
        vid=vid,
        vcomments=vcomments,
        comment_type=comment_type,
        is_live=False,
    )
    return (body, 503) if failed else body


@app.route("/api/component/comments/<vid>/<int:rpid>")
@rate_limit(**RATE_LIMITS["loose"], key_func=_component_ip_key)
async def api_component_sub_comments(vid, rpid):
    """Return rendered sub-comment HTML for a given parent comment."""
    if not _is_valid_comment_oid(vid):
        return _bad_vid_response()
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce

    page = request.args.get("page", 1, type=int)
    if page is None or page < 1:
        page = 1
    comment_type = _parse_comment_type()

    try:
        async with asyncio.timeout(4.0):
            result = await comment.get_sub_comments(vid, rpid, comment_type, page)
    except Exception:
        result = None
    if not isinstance(result, dict):
        return Response("Failed to load replies", status=502)

    return await render_template_with_theme("components/sub_comments.html", sub_comments=result, parent_rpid=rpid)


@app.route("/video/<vid>")
@app.route("/video/<vid>/")
@app.route("/video/<vid>:<idx>")
@app.route("/video/<vid>:<idx>/")
@rate_limit(**RATE_LIMITS["normal"])
async def video_view(vid, idx=0):
    # Validate video ID format
    import re

    if not re.match(r"^(BV[a-zA-Z0-9]{10}|av\d+)$", vid):
        return Response("Invalid video ID format", status=400)

    ato = request.args.get("ato") == "1"
    try:
        idx = int(idx)
        if idx < 0:
            return Response("Invalid video index", status=400)
    except TypeError, ValueError:
        return Response("Invalid video index", status=400)
    if request.args.get("listen") == "1":
        return await video_listen_view(vid, idx)
    vid = av2bv(vid[2:]) if vid.startswith("av") else vid
    v = video.Video(bvid=vid, credential=appcred)

    # Pre-caching history
    try:
        hist_id = getattr(g, "hist_id", None)
        if hist_id:
            hist_key = f"miku_hist_{hist_id}"
            pipe = appredis.pipeline(transaction=False)
            pipe.lrem(hist_key, 0, vid)
            pipe.lpush(hist_key, vid)
            pipe.ltrim(hist_key, 0, 49)
            pipe.expire(hist_key, 3600 * 24 * 30)
            await pipe.execute()
    except Exception:
        pass

    def _is_valid_video_cache(data) -> bool:
        return (
            isinstance(data, dict)
            and isinstance(data.get("vinfo"), dict)
            and bool(data["vinfo"].get("bvid") or data["vinfo"].get("title"))
            and isinstance(data.get("vtags"), list)
            and isinstance(data.get("vrelated"), list)
            and isinstance(data.get("vset"), list)
            and bool(data.get("vset"))
        )

    cache_ttl = cache_minutes("video_minutes") * 60
    cache_key = f"video:data:{vid}:{idx}"

    async def _load_video():
        if cache_ttl > 0:
            cached = await cache_get(cache_key, cache_ttl)
            if _is_valid_video_cache(cached):
                return {
                    "vinfo": cached["vinfo"],
                    "vtags": cached["vtags"],
                    "vrelated": cached["vrelated"],
                    "vset": cached["vset"],
                    "cache_hit": True,
                    "stale": False,
                }

        async def fetch_info_timeout(timeout=4.0):
            async with asyncio.timeout(timeout):
                return await v.get_info()

        async def safe_api(fn, *args, timeout=4.0):
            try:
                async with asyncio.timeout(timeout):
                    return await fn(*args)
            except Exception:
                return None

        tasks = [
            fetch_info_timeout(timeout=4.0),
            safe_api(v.get_tags, idx, timeout=2.0),
            safe_api(v.get_related, timeout=5.0),
            safe_api(v.get_pages, timeout=4.0),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        if isinstance(results[0], BaseException) or not isinstance(results[0], dict):
            err_msg = (
                (str(results[0]) or type(results[0]).__name__)
                if results[0]
                else "Bilibili returned empty data (possibly region-restricted)"
            )
            print(f"[Video] Error fetching info for {vid}: {err_msg}")
            if cache_ttl > 0:
                stale_data, is_stale = await cache_get_stale(cache_key, cache_ttl)
                if _is_valid_video_cache(stale_data):
                    return {
                        "vinfo": stale_data["vinfo"],
                        "vtags": stale_data["vtags"],
                        "vrelated": stale_data["vrelated"],
                        "vset": stale_data["vset"],
                        "cache_hit": not is_stale,
                        "stale": is_stale,
                    }
            return {"error": err_msg}

        loaded_vinfo = results[0]
        tags_ok = isinstance(results[1], list)
        related_ok = isinstance(results[2], list)
        pages_ok = isinstance(results[3], list) and bool(results[3])
        loaded_vtags = results[1] if tags_ok else []
        loaded_vrelated = results[2] if related_ok else []
        loaded_vset = results[3] if pages_ok else [{"page": 1, "part": vid}]

        if (
            cache_ttl > 0
            and isinstance(loaded_vinfo, dict)
            and (loaded_vinfo.get("bvid") or loaded_vinfo.get("title"))
            and tags_ok
            and related_ok
            and pages_ok
        ):
            await cache_set(
                cache_key,
                {"vinfo": loaded_vinfo, "vtags": loaded_vtags, "vrelated": loaded_vrelated, "vset": loaded_vset},
                cache_ttl,
            )

        async def precache_dash():
            try:
                from miku.dash import (
                    fetch_durl_supported_src,
                    has_valid_dash_tracks,
                    video_get_dash_for_qn,
                )

                async with asyncio.timeout(8.0):
                    data = await video_get_dash_for_qn(v, idx)
                if has_valid_dash_tracks(data):
                    await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(data))
                elif data and data.get("durl"):
                    try:
                        async with asyncio.timeout(25.0):
                            await fetch_durl_supported_src(v, vid, idx, play_data=data)
                    except Exception as e:
                        print(f"[Video] Pre-cache durl fallback failed for {vid}: {e}")
            except Exception as e:
                print(f"[Video] Pre-cache playurl failed for {vid}: {e}")

        task = asyncio.create_task(precache_dash())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return {
            "vinfo": loaded_vinfo,
            "vtags": loaded_vtags,
            "vrelated": loaded_vrelated,
            "vset": loaded_vset,
            "cache_hit": False,
            "stale": False,
        }

    outcome = await _video_sf.do(f"{vid}:{idx}", _load_video)
    if "error" in outcome:
        err_msg = outcome["error"]
        if "啥都木有" in err_msg or "-404" in err_msg:
            return await render_template_with_theme(
                "error.html",
                status="Video load failed",
                desc="Bilibili returned empty result (-404)",
                suggest="This video may have been deleted, is under review, or is region-restricted.",
            ), 404
        return await render_template_with_theme(
            "error.html",
            status="Video load failed",
            desc=err_msg,
            suggest="Please try refreshing the page, or check the server network connection.",
        ), 500

    vinfo, vtags, vrelated, vset = (
        outcome["vinfo"],
        outcome["vtags"],
        outcome["vrelated"],
        outcome["vset"],
    )
    vcomments = {"page": {"count": 0}, "replies": []}
    supported_src = []
    owner_info = await _fetch_owner_meta(vinfo)
    html = await render_template_with_theme(
        "video.html",
        vid=vid,
        vinfo=vinfo,
        owner_info=owner_info,
        vcomments=vcomments,
        vrelated=vrelated[:15],
        keywords=",".join(x.get("tag_name", "") for x in vtags if isinstance(x, dict)),
        vtags=vtags,
        supported_src=supported_src,
        ato=ato,
        idx=idx,
        vset=vset,
    )
    if outcome["stale"]:
        return Response(
            html,
            status=200,
            content_type="text/html",
            headers={"X-Cache": "STALE", "X-Degraded": "risk-control"},
        )
    if cache_ttl > 0:
        return Response(
            html,
            status=200,
            content_type="text/html",
            headers={"X-Cache": "HIT" if outcome["cache_hit"] else "MISS"},
        )
    return html
