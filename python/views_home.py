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

import transformers
from api import ResponseCodeException, homepage, search, video, video_zone
from b23_resolver import extract_b23_or_bili_url, resolve_b23_target
from quart import Response, g, jsonify, redirect, request, url_for
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
)


@app.route("/licenses")
async def static_licenses_view():
    return await render_template_with_theme("licenses.html")


@app.route("/")
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
        try:
            api_res = await homepage.get_videos()
        except Exception:
            # Upstream blip: serve the last good feed (stale) instead of
            # 500ing the front page; re-raise only when nothing survived.
            if cache_ttl > 0:
                stale_data, is_stale = await cache_get_stale("home:data", cache_ttl)
                if is_stale and isinstance(stale_data, dict) and isinstance(stale_data.get("payload"), (dict, list)):
                    api_res = stale_data["payload"]
                    cache_hit = "STALE"
            if cache_hit != "STALE":
                raise
        # Only cache a healthy feed; an empty homepage is almost certainly a
        # transient upstream blip, not a real empty front page. Stale-served
        # payloads are never re-cached (that would refresh fetched_at).
        if cache_hit != "STALE" and cache_ttl > 0 and isinstance(api_res, (dict, list)):
            raw_probe = api_res.get("item") if isinstance(api_res, dict) else api_res
            if isinstance(raw_probe, list) and raw_probe:
                await cache_set("home:data", {"payload": api_res}, cache_ttl)
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
    if b23_link and "b23.tv" in b23_link:
        res = await resolve_b23_target(q)
        if res.get("status") == "ok" and res.get("local_path"):
            return redirect(res["local_path"])

    # 1. Video (BV/av)
    m = re.search(r"(BV[a-zA-Z0-9]{10}|av\d+)", q, re.I)
    if m:
        return redirect(url_for("video_view", vid=m.group(0)))

    # 2. Article (cv/opus)
    m = re.search(r"(cv\d+|opus\d+)", q, re.I)
    if m:
        return redirect(url_for("read_view", cid=m.group(0)))

    m = re.search(r"bilibili\.com/opus/(\d+)", q, re.I)
    if m:
        return redirect(url_for("read_view", cid=f"opus{m.group(1)}"))

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
    except ResponseCodeException as exc:
        if exc.code != 412:
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
            if item.get("type") in ["ketang", "pugv"]:
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

    bvids = await appredis.lrange(f"miku_hist_{hist_id}", 0, -1)
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
