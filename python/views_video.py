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
import re
import sys

import orjson
from api import comment, video
from extra import av2bv, video_get_src_for_qn
from quart import Response, g, request
from rate_limit import RATE_LIMITS, rate_limit
from shared import (
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


@app.route("/video_listen/<vid>")
@app.route("/video_listen/<vid>:<idx>")
@app.route("/video_listen/<vid>/")
@app.route("/video_listen/<vid>:<idx>/")
async def video_listen_view(vid, idx=0):
    # Validate video ID format
    import re

    if not re.match(r"^(BV[a-zA-Z0-9]{10}|av\d+)$", vid):
        return Response("Invalid video ID format", status=400)

    ato, idx = request.args.get("ato") == "1", int(idx)
    vid = av2bv(vid[2:]) if vid.startswith("av") else vid
    v = video.Video(bvid=vid, credential=appcred)

    async def get_audio_url():
        if not await appredis.exists(f"mikuinv_{vid}_{idx}_0"):
            try:
                vsrc = await video_get_src_for_qn(v, idx, 16)
                if "durl" in vsrc and vsrc["durl"]:
                    await appredis.setex(f"mikuinv_{vid}_{idx}_0", 1800, vsrc["durl"][0]["url"])
            except Exception:
                pass

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
    return await render_template_with_theme(
        "video_listen.html",
        vid=vid,
        vinfo=vinfo,
        vrelated=vrelated[:10],
        vcomments=vcomments,
        keywords=",".join(x.get("tag_name", "") for x in vtags),
        vtags=vtags,
        ato=ato,
        idx=idx,
        vset=vset,
    )


# --- ASYNC COMPONENT API ---


def _is_final_play_data(data) -> bool:
    """True when play data is renderable (dash/durl) or a terminal paywall marker."""
    return bool(isinstance(data, dict) and (data.get("paywall") or data.get("dash") or data.get("durl")))


async def _resolve_progressive_sources(v, vid, idx, dash_data, ep_id, paywall, cid=None) -> list:
    """Progressive fallback list; [] for paywalled content (gated on every endpoint)."""
    if paywall:
        return []
    try:
        from dash_proxy import fetch_durl_supported_src

        async with asyncio.timeout(20.0):
            return await fetch_durl_supported_src(v, vid, idx, play_data=dash_data, ep_id=ep_id, cid=cid)
    except Exception as e:
        print(f"[Player] durl fallback failed for {vid}:{idx}: {e}")
        return []


@app.route("/api/component/player/<vid>/<int:idx>")
@rate_limit(**RATE_LIMITS["normal"])
async def api_component_player(vid, idx):
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
            from dash_proxy import video_get_dash_for_qn

            async with asyncio.timeout(8.0):
                data = await video_get_dash_for_qn(v, idx, ep_id=ep_id, cid=pgc_cid)
            if _is_final_play_data(data):
                if isinstance(data, dict) and data.get("dash"):
                    await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(data))
                return data
        except Exception as e:
            print(f"[Player] playurl fetch failed for {vid}:{idx}: {e}")
        return None

    # Known PGC cid from the season lookup below (None for plain UGC).
    # Threaded into playurl resolution so a gated UGC cid lookup can't
    # veto the PGC path. Defined here because get_dash_data() above
    # closes over it and runs after this block.
    pgc_cid = None
    try:
        if ep_id:
            from api.client import Api

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
    from dash_proxy import has_valid_dash_tracks

    paywall = bool((dash_data or {}).get("paywall"))
    is_dash = has_valid_dash_tracks(dash_data)
    dash_url = f"/video/dash/{vid}/{idx}/manifest.mpd" if is_dash else ""
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
        from shared import appcred as _cred

        if not (_cred and _cred.sessdata):
            return []
        async with asyncio.timeout(6.0):
            subs = await v.get_subtitle_meta(page_index=idx)
        from res import _subtitle_entries

        return _subtitle_entries(subs)
    except Exception as e:
        print(f"[Player] subtitle list failed: {e}")
        return []


@app.route("/api/component/meta/<vid>/<int:idx>")
@rate_limit(**RATE_LIMITS["normal"])
async def api_component_meta(vid, idx):
    # Use passed CSP nonce from main page to avoid CSP mismatch
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce

    def debug(*args):
        print("[comments]", *args, file=sys.stderr, flush=True)

    comments_closed = False
    try:
        async with asyncio.timeout(4.0):
            raw_result = await comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE)
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
@rate_limit(**RATE_LIMITS["normal"])
async def api_component_meta_more(vid, idx):
    """Fetch the next page of top-level comments (infinite scroll, matching PipePipe)."""
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce

    next_offset = request.args.get("next", "")
    if not isinstance(next_offset, str) or len(next_offset) > 512:
        next_offset = ""

    try:
        async with asyncio.timeout(4.0):
            raw = await comment.get_comments(
                vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE, next_offset=next_offset
            )
    except Exception:
        raw = None
    failed = not isinstance(raw, dict)
    vcomments = {"page": {"count": 0}, "replies": [], "next_offset": "", "is_end": True} if failed else raw

    body = await render_template_with_theme(
        "components/comment_items.html",
        vid=vid,
        vcomments=vcomments,
        is_live=False,
    )
    return (body, 503) if failed else body


@app.route("/api/component/comments/<vid>/<int:rpid>")
@rate_limit(**RATE_LIMITS["normal"])
async def api_component_sub_comments(vid, rpid):
    """Return rendered sub-comment HTML for a given parent comment."""
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce

    page = request.args.get("page", 1, type=int)
    if page is None or page < 1:
        page = 1

    try:
        async with asyncio.timeout(4.0):
            result = await comment.get_sub_comments(vid, rpid, comment.CommentResourceType.VIDEO.value, page)
    except Exception:
        result = None
    sub = result if isinstance(result, dict) else {"page": {"count": 0, "num": page, "size": 20}, "replies": []}

    return await render_template_with_theme("components/sub_comments.html", sub_comments=sub, parent_rpid=rpid)


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

    idx, ato = int(idx), request.args.get("ato") == "1"
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
            html = await render_template_with_theme(
                "video.html",
                vid=vid,
                vinfo=vinfo,
                vcomments={"page": {"count": 0}, "replies": []},
                vrelated=vrelated[:15],
                keywords=",".join(x.get("tag_name", "") for x in vtags if isinstance(x, dict)),
                vtags=vtags,
                supported_src=[],
                ato=ato,
                idx=idx,
                vset=vset,
            )
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})

    # LIGHTWEIGHT FETCH ONLY
    async def safe_api(fn, *args, timeout=4.0):
        try:
            async with asyncio.timeout(timeout):
                return await fn(*args)
        except Exception:
            return None

    tasks = [
        safe_api(v.get_info, timeout=4.0),
        safe_api(v.get_tags, idx, timeout=2.0),
        safe_api(v.get_related, timeout=5.0),
        safe_api(v.get_pages, timeout=4.0),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Check that core data was fetched successfully
    if isinstance(results[0], BaseException) or not isinstance(results[0], dict):
        err_msg = str(results[0]) if results[0] else "Bilibili returned empty data (possibly region-restricted)"
        print(f"[Video] Error fetching info for {vid}: {err_msg}")
        if cache_ttl > 0:
            # Upstream detail fetch failed — including risk-control fake-404s
            # ("啥都木有" on PGC BVs from datacenter IPs). Serve the last good
            # payload, marked STALE, instead of erroring.
            stale_data, is_stale = await cache_get_stale(cache_key, cache_ttl)
            if (
                is_stale
                and isinstance(stale_data, dict)
                and isinstance(stale_data.get("vinfo"), dict)
                and (stale_data["vinfo"].get("bvid") or stale_data["vinfo"].get("title"))
                and isinstance(stale_data.get("vtags"), list)
                and isinstance(stale_data.get("vrelated"), list)
                and isinstance(stale_data.get("vset"), list)
                and stale_data.get("vset")
            ):
                vinfo, vtags, vrelated, vset = (
                    stale_data["vinfo"],
                    stale_data["vtags"],
                    stale_data["vrelated"],
                    stale_data["vset"],
                )
                html = await render_template_with_theme(
                    "video.html",
                    vid=vid,
                    vinfo=vinfo,
                    vcomments={"page": {"count": 0}, "replies": []},
                    vrelated=vrelated[:15],
                    keywords=",".join(x.get("tag_name", "") for x in vtags if isinstance(x, dict)),
                    vtags=vtags,
                    supported_src=[],
                    ato=ato,
                    idx=idx,
                    vset=vset,
                )
                return Response(
                    html,
                    status=200,
                    content_type="text/html",
                    headers={"X-Cache": "STALE", "X-Degraded": "risk-control"},
                )
        # Show a friendly message for 404 / "啥都木有" (empty-result) responses
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

    vinfo = results[0]  # results[0] is guaranteed to be valid vinfo data at this point
    vtags = results[1] if isinstance(results[1], list) else []
    vrelated = results[2] if isinstance(results[2], list) else []
    vset = results[3] if isinstance(results[3], list) else [{"page": 1, "part": vid}]

    # Cache on healthy detail; tags/related/pages may fall back to empty
    # (those sections degrade gracefully, unlike a missing vinfo).
    if cache_ttl > 0 and isinstance(vinfo, dict) and (vinfo.get("bvid") or vinfo.get("title")):
        await cache_set(
            cache_key,
            {"vinfo": vinfo, "vtags": vtags, "vrelated": vrelated, "vset": vset},
            cache_ttl,
        )

    # Pre-cache play info (DASH, or durl fallback); proxying is always on.
    async def precache_dash():
        try:
            from dash_proxy import (
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

    vcomments = {"page": {"count": 0}, "replies": []}
    supported_src = []

    html = await render_template_with_theme(
        "video.html",
        vid=vid,
        vinfo=vinfo,
        vcomments=vcomments,
        vrelated=vrelated[:15],
        keywords=",".join(x.get("tag_name", "") for x in vtags),
        vtags=vtags,
        supported_src=supported_src,
        ato=ato,
        idx=idx,
        vset=vset,
    )
    if cache_ttl > 0:
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "MISS"})
    return html
