# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import re
from urllib.parse import urlparse

from quart import Response, g, jsonify, redirect, request, url_for

import miku.media.transformers as transformers
from miku.api import ResponseCodeException, homepage, search, video, video_zone
from miku.api.exceptions import NetworkException, is_risk_error
from miku.core.csrf import csrf_protect
from miku.core.rate_limit import RATE_LIMITS, ip_family_key, rate_limit
from miku.core.singleflight import SingleflightGroup
from miku.media.b23_resolver import extract_b23_or_bili_url, resolve_b23_target
from miku.state import (
    app,
    appcred,
    appredis,
    cache_get,
    cache_get_stale,
    cache_minutes,
    cache_set,
    render_template_with_theme,
)

_home_sf = SingleflightGroup()


@app.route("/licenses")
async def static_licenses_view():
    return await render_template_with_theme("licenses.html")


@app.route("/")
@rate_limit(**RATE_LIMITS["normal"])
async def home_view():
    cache_ttl = cache_minutes("home_minutes") * 60
    cache_hit = False
    api_res = None
    if cache_ttl > 0:
        data = await cache_get("home:data", cache_ttl)
        if isinstance(data, dict) and isinstance(data.get("payload"), (dict, list)):
            api_res = data["payload"]
            cache_hit = True
    if not cache_hit:

        async def _load_home():
            if cache_ttl > 0:
                cached = await cache_get("home:data", cache_ttl)
                if isinstance(cached, dict) and isinstance(cached.get("payload"), (dict, list)):
                    return cached["payload"], True
            try:
                res = await homepage.get_videos()
            except Exception:
                if cache_ttl > 0:
                    stale_data, is_stale = await cache_get_stale("home:data", cache_ttl)
                    if (
                        is_stale
                        and isinstance(stale_data, dict)
                        and isinstance(stale_data.get("payload"), (dict, list))
                    ):
                        return stale_data["payload"], "STALE"
                raise
            if cache_ttl > 0 and isinstance(res, (dict, list)):
                raw_probe = res.get("item") if isinstance(res, dict) else res
                if isinstance(raw_probe, list) and raw_probe:
                    await cache_set("home:data", {"payload": res}, cache_ttl)
            return res, False

        api_res, cache_hit = await _home_sf.do("home:data", _load_home)
    processed_videos = []
    raw_list = []
    if isinstance(api_res, dict) and "item" in api_res:
        raw_list = api_res["item"]
    elif isinstance(api_res, list):
        raw_list = api_res
    for v in raw_list:
        card = transformers.transform_video_card(v)
        if card:
            processed_videos.append(card)
    html = await render_template_with_theme("home.html", videos=processed_videos)
    if cache_ttl > 0:
        return Response(
            html,
            status=200,
            content_type="text/html",
            headers={"X-Cache": "STALE" if cache_hit == "STALE" else ("HIT" if cache_hit else "MISS")},
        )
    return html


@app.route("/vv/<zid>")
@app.route("/vv/<zid>/")
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("zone"))
async def zone_id_view(zid):
    pn = request.args.get("i") or 1
    info = await video_zone.get_zone_new_videos(zid, pn)
    return await render_template_with_theme("zone.html", info=info)


@app.route("/search")
@rate_limit(**RATE_LIMITS["search"])
async def search_view():
    q = request.args.get("q")
    i = request.args.get("i") or 1
    if not q:
        return await render_template_with_theme(
            "error.html",
            status="Search failed",
            desc="No search keyword provided.",
            suggest="Please enter a search keyword and try again.",
        ), 400

    # URL Jump logic
    b23_link = extract_b23_or_bili_url(q)
    b23_host = (urlparse(b23_link).hostname or "").lower() if b23_link else ""
    if b23_host == "b23.tv" or b23_host.endswith(".b23.tv"):
        res = await resolve_b23_target(q)
        if res.get("status") == "ok" and res.get("local_path"):
            return redirect(res["local_path"])

    # 1. Video (BV/av)
    m = re.search(r"(BV[a-zA-Z0-9]{10}|av\d+)", q, re.I)
    if m:
        return redirect(url_for("video_view", vid=m.group(0)))

    # 2. Article (cv/opus)
    m = re.search(r"cv(\d+)", q, re.I)
    if m:
        return redirect(url_for("read_view", cid=f"cv{m.group(1)}"))

    m = re.search(r"opus(\d+)", q, re.I)
    if m:
        return redirect(url_for("opus_view", cid=m.group(1)))

    m = re.search(r"bilibili\.com/read/mobile/(\d+)", q, re.I)
    if m:
        return redirect(url_for("read_view", cid=f"cv{m.group(1)}"))

    m = re.search(r"bilibili\.com/opus/(\d+)", q, re.I)
    if m:
        return redirect(url_for("opus_view", cid=m.group(1)))

    # 3. Live
    m = re.search(r"live\.bilibili\.com/(\d+)", q)
    if m:
        return redirect(url_for("live_room_view", room_id=m.group(1)))

    # 4. Space / Author
    m = re.search(r"space\.bilibili\.com/(\d+)", q)
    if m:
        return redirect(url_for("space_view", mid=m.group(1)))

    order_map = {
        "rank": search.OrderVideo.TOTALRANK,
        "click": search.OrderVideo.CLICK,
        "pubdate": search.OrderVideo.PUBDATE,
        "dm": search.OrderVideo.DM,
        "stow": search.OrderVideo.STOW,
        "scores": search.OrderVideo.SCORES,
        "attention": search.OrderArticle.ATTENTION,
        "fans": search.OrderUser.FANS,
        "level": search.OrderUser.LEVEL,
    }
    if request.args.get("t") == "article":
        search_type, tmpl = search.SearchObjectType.ARTICLE, "search_article.html"
    elif request.args.get("t") == "user":
        search_type, tmpl = search.SearchObjectType.USER, "search_user.html"
    elif request.args.get("t") == "live":
        search_type, tmpl = search.SearchObjectType.LIVE, "search.html"
    else:
        search_type, tmpl = search.SearchObjectType.VIDEO, "search.html"

    try:
        sinfo = await search.search_by_type(
            q, page=i, search_type=search_type, order_type=order_map.get(request.args.get("sort"))
        )
    except (NetworkException, ResponseCodeException) as exc:
        if not is_risk_error(exc) and getattr(exc, "code", None) != 412:
            raise
        sinfo = None
    # Bilibili's search endpoint is subject to risk control (HTTP 412 / v_voucher)
    # that can return `{'v_voucher': ...}` instead of a proper result dict. The
    # templates assume `page`/`numPages`/`numResults`/`result` exist, so normalise
    # any unexpected/empty payload to a safe shape to avoid a 500 error page.
    if not isinstance(sinfo, dict) or "result" not in sinfo or "page" not in sinfo:
        _try_page = sinfo.get("page") if isinstance(sinfo, dict) else None
        sinfo = {
            "result": {"live_room": []} if search_type == search.SearchObjectType.LIVE else [],
            "page": _try_page if isinstance(_try_page, int) else 0,
            "numPages": 0,
            "numResults": 0,
        }
    results = []
    if search_type == search.SearchObjectType.VIDEO:
        for item in sinfo.get("result", []):
            item_type = str(item.get("type") or "")
            if item_type in ("ketang", "pugv") or item_type.startswith("video_ad") or not item.get("bvid"):
                continue
            card = transformers.transform_video_card(item)
            if card:
                results.append(card)
    elif search_type == search.SearchObjectType.LIVE:
        for item in sinfo.get("result", {}).get("live_room", []):
            card = transformers.transform_live_card(item)
            if card:
                results.append(card)
    elif search_type == search.SearchObjectType.ARTICLE:
        for item in sinfo.get("result", []):
            card = transformers.transform_article_card(item)
            if card:
                results.append(card)
        art_ttl = cache_minutes("article_minutes") * 60
        if art_ttl > 0 and results:
            cards_with_id = [card for card in results if card.get("id")]
            if cards_with_id:
                cached_maps = await asyncio.gather(
                    *(cache_get(f"cv:opus:{card['id']}", art_ttl) for card in cards_with_id),
                    return_exceptions=True,
                )
                for card, cached_map in zip(cards_with_id, cached_maps, strict=True):
                    mapped = cached_map.get("opus_id") if isinstance(cached_map, dict) else None
                    if isinstance(mapped, str) and mapped.isdigit():
                        card["opus_id"] = mapped
    elif search_type == search.SearchObjectType.USER:
        for item in sinfo.get("result", []):
            card = transformers.transform_user_card(item)
            if card:
                results.append(card)
    else:
        results = sinfo.get("result", [])
    return await render_template_with_theme(tmpl, q=q, sinfo=sinfo, rs=results, sort=request.args.get("sort"))


@app.route("/history")
async def history_view():
    hist_id = getattr(g, "hist_id", None)
    if not hist_id or getattr(g, "set_hist_cookie", False):
        return await render_template_with_theme(
            "home.html", videos=[], title="Browsing history", message="You have no browsing history yet."
        )

    try:
        bvids = await appredis.lrange(f"miku_hist_{hist_id}", 0, -1)
    except Exception:
        bvids = []
    if not bvids:
        return await render_template_with_theme(
            "home.html", videos=[], title="Browsing history", message="You have no browsing history yet."
        )

    async def get_v_info(bvid):
        try:
            v = video.Video(bvid=bvid, credential=appcred)
            return await v.get_info()
        except Exception:
            return None

    raw_infos = await asyncio.gather(*[get_v_info(b) for b in bvids])
    videos = []
    for info in raw_infos:
        if info:
            card = transformers.transform_video_card(info)
            if card:
                videos.append(card)

    return await render_template_with_theme("home.html", videos=videos, title="Browsing history")


@app.route("/convert", methods=["GET", "POST"])
@rate_limit(**RATE_LIMITS["normal"])
@csrf_protect()
async def convert_view():
    url = request.args.get("url") or request.args.get("q") or ""
    result = None
    error = None

    if request.method == "POST":
        form = await request.form
        url = form.get("url", "").strip()
        if not url:
            error = "Please provide a Bilibili or b23.tv link"
        else:
            result = await resolve_b23_target(url)
            if result.get("status") == "error":
                error = result.get("error", "Failed to resolve URL")

    return await render_template_with_theme(
        "convert.html",
        url=url,
        result=result,
        error=error,
    )


@app.route("/api/convert", methods=["POST"])
@rate_limit(**RATE_LIMITS["normal"])
@csrf_protect()
async def api_convert():
    try:
        data = await request.get_json()
    except Exception:
        data = None

    if not isinstance(data, dict) or not str(data.get("url", "")).strip():
        return (
            jsonify(
                {
                    "status": "error",
                    "original": "",
                    "target_url": "",
                    "clean_url": "",
                    "local_path": "",
                    "type": "",
                    "id": "",
                    "error": "Missing or invalid 'url' parameter",
                }
            ),
            400,
        )

    url = str(data.get("url", "")).strip()
    result = await resolve_b23_target(url)
    if result.get("status") == "error":
        return jsonify(result), 400

    return jsonify(result), 200


__all__ = [
    "home_view",
    "zone_id_view",
    "search_view",
    "history_view",
    "convert_view",
    "api_convert",
    "static_licenses_view",
    "resolve_b23_target",
]
