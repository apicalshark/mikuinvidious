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
import datetime
import re
import sys

import orjson
import transformers
from api import (
    ResponseCodeException,
    article,
    audio,
    comment,
    homepage,
    live,
    live_area,
    opus,
    search,
    user,
    video,
    video_zone,
)
from extra import (
    article_to_any,
    article_to_html,
    av2bv,
    get_article_info,
    video_get_src_for_qn,
)
from quart import Response, g, redirect, request, url_for
from rate_limit import RATE_LIMITS, rate_limit
from shared import (
    Network,
    app,
    appconf,
    appcred,
    appredis,
    cache_get,
    cache_minutes,
    cache_set,
    render_template_with_theme,
    safe_json_loads,
)

_background_tasks = set()


@app.route("/live/chat/<int:room_id>")
@rate_limit(**RATE_LIMITS["strict"])
async def live_chat_sse(room_id):
    from api import Credential
    from api import live as b_live

    async def event_stream():
        queue = asyncio.Queue()
        cred = appcred if isinstance(appcred, Credential) else None
        stop_event = asyncio.Event()

        async def run_danmaku():
            while not stop_event.is_set():
                dm_client = None
                try:
                    dm_client = b_live.LiveDanmaku(room_id, credential=cred)

                    @dm_client.on("DANMU_MSG")
                    async def on_danmaku(event):
                        try:
                            info = event["data"]["info"]
                            await queue.put({"user": info[2][1], "text": info[1]})
                        except Exception:
                            pass

                    await dm_client.connect()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[LiveChat] Connection error for room {room_id}: {e}")
                finally:
                    if dm_client:
                        try:
                            await dm_client.disconnect()
                        except Exception:
                            pass

                if not stop_event.is_set():
                    await asyncio.sleep(5)
                    print(f"[LiveChat] Attempting reconnection for room {room_id}...")

        conn_task = asyncio.create_task(run_danmaku())

        async def heartbeat():
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(20)
                    await queue.put(": heartbeat")
            except asyncio.CancelledError:
                pass

        hb_task = asyncio.create_task(heartbeat())

        yield f"data: {orjson.dumps({'user': 'SYSTEM', 'text': 'Chat connected'}).decode('utf-8')}\n\n"

        try:
            while not stop_event.is_set():
                msg = await queue.get()
                if msg == ": heartbeat":
                    yield ": heartbeat\n\n"
                else:
                    yield f"data: {orjson.dumps(msg).decode('utf-8')}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            print(f"[LiveChat] Client disconnected from room {room_id}")
        except Exception as e:
            print(f"[LiveChat] Unexpected error in event_stream for room {room_id}: {e}")
        finally:
            print(f"[LiveChat] Cleaning up resources for room {room_id}")
            stop_event.set()
            hb_task.cancel()
            conn_task.cancel()

            async def cleanup():
                try:
                    await asyncio.gather(hb_task, conn_task, return_exceptions=True)
                except Exception as e:
                    print(f"[LiveChat] Error during task cancellation: {e}")

            await asyncio.shield(cleanup())

    return Response(
        event_stream(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
            "X-Accel-Buffering": "no",
        },
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
        api_res = await homepage.get_videos()
        # Only cache a healthy feed; an empty homepage is almost certainly a
        # transient upstream blip, not a real empty front page.
        if cache_ttl > 0 and isinstance(api_res, (dict, list)):
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
            headers={"X-Cache": "HIT" if cache_hit else "MISS"},
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
            "error.html", status="Search failed", desc="No search keyword provided.", suggest="Please enter a search keyword and try again."
        ), 400

    # URL Jump logic

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


def _space_data_key(mid, pn=1):
    # Page-1 payload (space:data:<mid>) is shared with /space/<mid>/json so
    # one Bilibili fetch serves both routes; deeper pages get their own
    # per-page key (space:data:<mid>:<pn>).
    return f"space:data:{mid}" if pn == 1 else f"space:data:{mid}:{pn}"


def _is_good_space_data(uinfo, uvids):
    """Only healthy payloads may be cached: valid profile + non-empty vlist.

    Transient upstream failures surface as exceptions (handled by callers) or
    as valid-looking but empty vlists; caching those would stick the channel
    on "0 videos" until expiry, so they are served live and never stored.
    """
    if not isinstance(uinfo, dict) or not (uinfo.get("name") or uinfo.get("face")):
        return False
    if not isinstance(uvids, dict):
        return False
    lst = uvids.get("list")
    vlist = lst.get("vlist", []) if isinstance(lst, dict) else []
    return isinstance(vlist, list) and len(vlist) > 0


def _max_space_ttl():
    """Redis expiry for the shared key: the longest interested route policy."""
    return max(max(cache_minutes("space_minutes"), cache_minutes("space_json_minutes")), 0) * 60


async def _read_space_data(mid, max_age_seconds, pn=1):
    """Return (uinfo, uvids) if the unified entry is fresh for this reader.

    Each route passes its own TTL as max_age_seconds, so divergent
    SPACE_CACHE_MINUTES / SPACE_JSON_CACHE_MINUTES policies are each
    strictly enforced even though both share one key. Entries written
    before fetched_at existed are treated as expired (self-migrating).
    """
    data = await cache_get(_space_data_key(mid, pn), max_age_seconds)
    if not isinstance(data, dict):
        return None, None
    uinfo, uvids = data.get("uinfo"), data.get("uvids")
    if not _is_good_space_data(uinfo, uvids):
        return None, None
    return uinfo, uvids


async def _write_space_data(mid, uinfo, uvids, pn=1, ttl=None):
    """Store a healthy payload; page-1 expiry covers the longest route policy."""
    if not _is_good_space_data(uinfo, uvids):
        return
    if ttl is None:
        ttl = _max_space_ttl()
    await cache_set(_space_data_key(mid, pn), {"uinfo": uinfo, "uvids": uvids}, ttl)


async def _fetch_space_data(mid, pn=1, ps=30):
    u = user.User(mid, credential=appcred)
    return await asyncio.gather(u.get_user_info(), u.get_videos(pn=pn, ps=ps))


@app.route("/space/<mid>")
@app.route("/space/<mid>/")
async def space_view(mid):
    try:
        pn = int(request.args.get("i") or 1)
    except (TypeError, ValueError):
        pn = 1
    pn = max(pn, 1)
    # Page-1 payload is shared with /space/<mid>/json; deeper pages get
    # their own per-page key. All pages honor SPACE_CACHE_MINUTES.
    cache_ttl = cache_minutes("space_minutes") * 60
    use_cache = cache_ttl > 0
    cache_hit = False
    uinfo = None
    uvids = {}
    if use_cache:
        uinfo, uvids = await _read_space_data(mid, cache_ttl, pn)
        cache_hit = uinfo is not None
    if not cache_hit:
        u = user.User(mid, credential=appcred)
        try:
            # ps=30 on the page-1 path so the payload is a superset the JSON
            # feed can also use; page-1 HTML is sliced back to 28 below.
            uinfo, uvids = await asyncio.gather(u.get_user_info(), u.get_videos(pn=pn, ps=30 if pn == 1 else 28))
        except Exception:
            # The user API is often risk-controlled / IP-blocked (412/-352) without the
            # WARP proxy; re-run the core profile fetch alone in case only a sibling
            # gather task failed. The video list is optional and falls back to empty.
            uvids = {}
            if not isinstance(uinfo, dict) or not uinfo:
                try:
                    uinfo = await u.get_user_info()
                except Exception:
                    uinfo = None
        if use_cache:
            # Page-1 key expiry covers the longest (space/json) policy so a
            # divergent JSON TTL isn't cut short; deeper pages use their own.
            await _write_space_data(mid, uinfo, uvids, pn, _max_space_ttl() if pn == 1 else cache_ttl)
    if not isinstance(uinfo, dict) or not uinfo:
        return await render_template_with_theme(
            "error.html",
            status="Space load failed",
            desc="Failed to fetch this user's profile. Please try again later.",
            suggest="Please check your network connection or proxy settings.",
        ), 500
    if not isinstance(uvids, dict):
        uvids = {}
    uvids.setdefault("list", {}).setdefault("vlist", [])
    uvids.setdefault("page", {"count": 0, "pn": 1, "ps": 28})
    # Coerce numeric page fields to int so the template's arithmetic/comparisons
    # (e.g. `pn > 1`) never hit str-vs-int errors.
    page = uvids.get("page") or {}
    try:
        page["pn"] = int(page.get("pn", 1))
    except (TypeError, ValueError):
        page["pn"] = 1
    try:
        page["ps"] = int(page.get("ps", 28))
    except (TypeError, ValueError):
        page["ps"] = 28
    try:
        page["count"] = int(page.get("count", 0))
    except (TypeError, ValueError):
        page["count"] = 0
    vlist = uvids.get("list", {}).get("vlist", [])
    if pn == 1 and isinstance(vlist, list) and len(vlist) > 28:
        # Unified payload carries ps=30 for the JSON feed; keep page-1 HTML
        # identical to the classic ps=28 view so pagination stays aligned.
        uvids["list"]["vlist"] = vlist = vlist[:28]
        if isinstance(page, dict):
            page["ps"] = 28
    # The fallback recArchivesByKeywords endpoint has no author/owner name; since
    # this is the user's own space, stamp it from the profile.
    uname = uinfo.get("name", "")
    for v in vlist:
        if not v.get("author") and uname:
            v["author"] = uname
    html = await render_template_with_theme("space.html", uinfo=uinfo, uvids=uvids)
    if use_cache:
        return Response(
            html,
            status=200,
            content_type="text/html",
            headers={"X-Cache": "HIT" if cache_hit else "MISS"},
        )
    return html


@app.route("/space/<mid>/json")
async def space_json_feed(mid):
    cache_ttl = cache_minutes("space_json_minutes") * 60
    cache_hit = False
    uinfo = uvids = None
    if cache_ttl > 0:
        # Served from the same unified payload as /space/<mid> (page 1):
        # whichever route misses first pays for the one upstream fetch.
        uinfo, uvids = await _read_space_data(mid, cache_ttl)
        cache_hit = uinfo is not None
    if not cache_hit:
        try:
            uinfo, uvids = await _fetch_space_data(mid, pn=1, ps=30)
        except Exception as e:
            return Response(
                orjson.dumps({"error": str(e)}),
                status=502,
                content_type="application/json",
            )
        if not isinstance(uinfo, dict) or not isinstance(uvids, dict):
            return Response(
                orjson.dumps({"error": "Unexpected response format from Bilibili API"}),
                status=502,
                content_type="application/json",
            )
        if cache_ttl > 0:
            await _write_space_data(mid, uinfo, uvids)

    site_url = appconf["site"]["site_url"]
    feed_url = f"{site_url}/space/{mid}/json"
    home_url = f"{site_url}/space/{mid}"
    face_url = uinfo.get("face", "")

    items = []
    for v in uvids.get("list", {}).get("vlist", []):
        bvid = v.get("bvid", "")
        title = v.get("title", "")
        items.append(
            {
                "id": bvid,
                "url": f"{site_url}/video/{bvid}",
                "external_url": f"https://www.bilibili.com/video/{bvid}",
                "title": title,
                "content_text": title,
                "date_published": datetime.datetime.fromtimestamp(
                    v.get("created", 0), tz=datetime.timezone.utc
                ).isoformat(),
                "image": v.get("pic", ""),
            }
        )

    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": f"{uinfo.get('name', 'Unknown')}",
        "home_page_url": home_url,
        "feed_url": feed_url,
        "favicon": face_url,
        "description": uinfo.get("sign", ""),
        "items": items,
    }

    raw = orjson.dumps(feed)
    # Payload caching already happened via _write_space_data above (healthy
    # payloads only); the feed itself is cheaply rebuilt from cached data.
    if cache_ttl > 0:
        return Response(
            raw,
            status=200,
            content_type="application/feed+json",
            headers={"X-Cache": "HIT" if cache_hit else "MISS"},
        )
    return Response(raw, status=200, content_type="application/feed+json")


@app.route("/author/<mid>")
@app.route("/author/<mid>/")
async def author_view(mid):
    try:
        pn = int(request.args.get("i") or 1)
    except (TypeError, ValueError):
        pn = 1
    pn = max(pn, 1)
    # Page-1 payload uses the base key; deeper pages get per-page keys.
    cache_ttl = cache_minutes("author_minutes") * 60
    use_cache = cache_ttl > 0
    cache_key = f"author:data:{mid}" if pn == 1 else f"author:data:{mid}:{pn}"
    cache_hit = False
    uinfo = uarticles = None
    if use_cache:
        data = await cache_get(cache_key, cache_ttl)
        if isinstance(data, dict):
            uinfo, uarticles = data.get("uinfo"), data.get("uarticles")
            cache_hit = (
                isinstance(uinfo, dict)
                and bool(uinfo)
                and isinstance(uarticles, dict)
            )
            if not cache_hit:
                uinfo = uarticles = None
    if not cache_hit:
        u = user.User(mid, credential=appcred)
        uinfo, uarticles = await asyncio.gather(u.get_user_info(), u.get_articles(pn=pn, ps=28))
        # Only cache a healthy payload; never stick a broken profile.
        if use_cache and isinstance(uinfo, dict) and uinfo and isinstance(uarticles, dict):
            await cache_set(cache_key, {"uinfo": uinfo, "uarticles": uarticles}, cache_ttl)
    html = await render_template_with_theme("author.html", uinfo=uinfo, uarts=uarticles)
    if use_cache:
        return Response(
            html,
            status=200,
            content_type="text/html",
            headers={"X-Cache": "HIT" if cache_hit else "MISS"},
        )
    return html


@app.route("/read/<cid>")
@app.route("/read/<cid>/")
@app.route("/read/mobile/<cid>")
@app.route("/read/mobile/<cid>/")
@app.route("/opus/<cid>")
@app.route("/opus/<cid>/")
async def read_view(cid):
    is_opus = "opus" in request.path or not cid.startswith("cv")
    url = (
        f"https://www.bilibili.com/opus/{cid.replace('opus', '')}"
        if is_opus
        else f"https://www.bilibili.com/read/{cid}"
    )
    cvid = cid.replace("cv", "").replace("opus", "")
    ua = "Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com) 8.76.0 os/android model/WTF mobi_app/android build/8760000 channel/not_found innerVer/8760010 osVer/15 network/2"

    # Optional pandoc export (any supported format).
    want_format = (
        request.args.get("format")
        if (
            appconf["render"]["use_pandoc"]
            and request.args.get("format") in appconf["render"]["article_allowed_formats"]
        )
        else None
    )

    cache_ttl = cache_minutes("article_minutes") * 60
    # File exports (?format=) bypass the cache; the HTML flavor is part of
    # the key so /read/cv.. and /opus/.. never share entries.
    use_cache = want_format is None and cache_ttl > 0
    cache_key = f"read:data:{cid}:{'opus' if is_opus else 'cv'}"
    if use_cache:
        data = await cache_get(cache_key, cache_ttl)
        if (
            isinstance(data, dict)
            and isinstance(data.get("arinfo"), dict)
            and data.get("arinfo")
            and isinstance(data.get("content"), str)
            and data.get("content")
            and "Failed to parse article content" not in data.get("content")
        ):
            html = await render_template_with_theme(
                "read.html",
                cid=cid,
                arinfo=data["arinfo"],
                article_content=data["content"],
                is_opus=is_opus,
            )
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})

    client = await Network.get_async_client()

    # The public read/opus page is intermittently served with the content module
    # stripped (anti-bot) from datacenter IPs, even though HTTP 200 with title.
    # Retry the scrape until the article body actually parses (or give up).
    text = None
    content = None
    last_status = 0
    for attempt in range(3):
        req = await client.send(
            client.build_request("GET", url, headers={"User-Agent": ua}),
            follow_redirects=True,
        )
        status, body = req.status_code, req.text
        await req.aclose()
        last_status = status
        if status != 200:
            await asyncio.sleep(0.4 * (attempt + 1))
            continue
        try:
            parsed = article_to_html(body)
        except Exception:
            parsed = "<p>Failed to parse article content.</p>"
        if want_format is not None or "Failed to parse article content" not in parsed:
            text, content = body, parsed
            break
        await asyncio.sleep(0.4 * (attempt + 1))

    if text is None:
        return await render_template_with_theme(
            "error.html",
            status="Article not found" if last_status == 404 else "Server error",
            desc="Backend server sent an invalid response",
            suggest="The article you requested most likely does not exist. Please check your request." if last_status == 404 else None,
        ), (404 if last_status == 404 else 502)

    if want_format is not None:
        return await article_to_any(text, want_format)

    try:
        arinfo = get_article_info(text, cid)
        try:
            if is_opus:
                o = opus.Opus(int(cvid), credential=appcred)
                api_info = await o.get_info()
                for module in api_info.get("item", {}).get("modules", []):
                    if module.get("module_stat"):
                        stat = module["module_stat"]
                        arinfo["stats"]["like"] = stat.get("like", {}).get("count", arinfo["stats"]["like"])
                        arinfo["stats"]["coin"] = stat.get("coin", {}).get("count", arinfo["stats"]["coin"])
                        arinfo["stats"]["favorite"] = stat.get("favorite", {}).get("count", arinfo["stats"]["favorite"])
                        arinfo["stats"]["share"] = stat.get("forward", {}).get("count", arinfo["stats"]["share"])
            else:
                ar = article.Article(int(cvid))
                api_info = await ar.get_info()
                if api_info.get("stats"):
                    arinfo["stats"].update(api_info["stats"])
                if api_info.get("title") and not arinfo["title"]:
                    arinfo["title"] = api_info["title"]
        except Exception:
            pass
        # Only cache fully parsed articles; stripped anti-bot bodies are
        # retried live next time instead of sticking.
        if (
            use_cache
            and isinstance(arinfo, dict)
            and arinfo
            and isinstance(content, str)
            and content
            and "Failed to parse article content" not in content
        ):
            await cache_set(cache_key, {"arinfo": arinfo, "content": content}, cache_ttl)
        html = await render_template_with_theme(
            "read.html", cid=cid, arinfo=arinfo, article_content=content, is_opus=is_opus
        )
        if use_cache:
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "MISS"})
        return html
    except Exception:
        import traceback

        traceback.print_exc()
        return await render_template_with_theme(
            "error.html",
            status="Article not found",
            desc="Article does not exist or parse error",
            suggest="The article you requested most likely does not exist. Please check your request.",
        ), 404


@app.route("/live")
async def live_list_view():
    page = request.args.get("i", 1)
    try:
        data = await live_area.get_list_by_area(area_id=9, page=page)
        rooms = []
        for item in data.get("list", []):
            card = transformers.transform_live_card(item)
            if card:
                rooms.append(card)
        return await render_template_with_theme("home.html", videos=rooms, title="Live")
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"[ERROR] Live list error: {e}")
        return await render_template_with_theme(
            "error.html", status="Live list load failed", desc="Failed to fetch the live list. Please try again later."
        ), 500


def _pick_live_url(flv_url, hls_url):
    """Server-side live format policy: FLV first, HLS master fallback.

    Reversed when ``LIVE_PREFER_HLS`` is set. One format per room — the
    player never switches, so there is nothing to negotiate client-side.
    """
    if appconf["live"]["prefer_hls"]:
        return hls_url or flv_url
    return flv_url or hls_url


def _live_fetchable_qualities(qn_list, default_qn):
    """Qualities worth resolving: all when logged in, default-only anonymously.

    Per-quality live ladders require login, so anonymous sessions only ever
    play the default entry — skip the extra (doomed, -400-risking) fetches.
    """
    if appcred and appcred.sessdata:
        return qn_list
    print("[Live] Anonymous session: default quality only")
    return [d for d in qn_list if d.get("qn") == default_qn] or qn_list[:1]


@app.route("/live/<room_id>")
async def live_room_view(room_id):
    try:
        room_id_int = int(room_id)
        room = live.LiveRoom(room_id_int, credential=appcred)

        # Get basic room info first (getInfoByRoom, fallback to getRoomBaseInfo
        # when WBI/risk-control rejects the primary — PipePipe 1e330b87).
        try:
            info_data = await room.get_room_info()
        except Exception:
            info_data = await room.get_room_base_info()

        def _assemble_flv(codec):
            """host + base_url + extra, AVC preferred (PipePipe pickLiveFlvUrl)."""
            try:
                base = codec.get("base_url") or ""
                infos = codec.get("url_info") or []
                if not base or not infos:
                    return ""
                m = infos[0] or {}
                return f"{m.get('host', '')}{base}{m.get('extra', '')}"
            except Exception:
                return ""

        def _extract_live_urls(play_data):
            """Return (flv_url, hls_master_url) from a v2 getRoomPlayInfo payload."""
            flv, flv_hevc, hls_master = None, None, None
            streams = []
            try:
                if isinstance(play_data, dict):
                    # Api.result unwraps to play_url.stream; raw API nests
                    # playurl_info.playurl.stream — accept both + bare stream.
                    node = play_data.get("play_url") or {}
                    if not node:
                        node = (play_data.get("playurl_info") or {}).get("playurl") or {}
                    streams = node.get("stream", []) or play_data.get("stream", [])
            except Exception:
                streams = []
            for s in streams or []:
                pname = s.get("protocol_name", "")
                for f in s.get("format", []) or []:
                    fname = f.get("format_name", "")
                    if pname == "http_hls" and fname == "fmp4" and not hls_master:
                        mu = f.get("master_url") or ""
                        if mu:
                            hls_master = mu
                    if pname == "http_stream" and fname == "flv":
                        for c in f.get("codec", []) or []:
                            # Newer payloads already carry a full url; legacy
                            # ones need host + base_url + extra assembly.
                            full = c.get("url") or _assemble_flv(c) or c.get("base_url") or ""
                            if not full:
                                continue
                            if c.get("codec_name") == "avc":
                                flv = full
                                break
                            if not flv_hevc:
                                flv_hevc = full
                        if flv:
                            break
                if flv and hls_master:
                    break
            return flv or flv_hevc, hls_master

        # Single DEFAULT request carries both ladders (PipePipe fetchLivePlaybackUrls).
        try:
            play_data = await room.get_room_play_info_v2()
        except Exception:
            # Prioritize FLV for live streams as requested
            try:
                play_data = await room.get_room_play_info_v2(
                    live_protocol=live.LiveProtocol.FLV, live_format=live.LiveFormat.FLV
                )
            except Exception:
                play_data = await room.get_room_play_info_v2(
                    live_protocol=live.LiveProtocol.HLS, live_format=live.LiveFormat.FMP4
                )

        info = transformers.transform_live_room(info_data)
        _qn_node = (play_data.get("play_url") or {}) if isinstance(play_data, dict) else {}
        if not _qn_node.get("g_qn_desc") and isinstance(play_data, dict):
            _qn_node = (play_data.get("playurl_info") or {}).get("playurl") or {}
        qn_list = _qn_node.get("g_qn_desc", []) or [{"qn": 0, "desc": "Default"}]

        # Reuse the initial (qn=10000) response for the default quality instead
        # of refetching it: Bilibili -400s rapid parallel getRoomPlayInfo calls
        # from one IP, so per-QN URLs are fetched sequentially below.
        _default_flv, _default_hls = _extract_live_urls(play_data)
        _default_url = _pick_live_url(_default_flv, _default_hls)
        _default_qn = live.ScreenResolution.ORIGINAL.value
        live_format = "hls" if _default_hls and _default_url == _default_hls else "flv"
        supported_src = []
        if _default_url:
            print(f"[Live] Default room {room_id} QN {_default_qn}: {_default_url[:50]}...")
            await appredis.setex(f"miku_live_{room_id}", 1800, _default_url)
            await appredis.setex(f"miku_live_{room_id}_{_default_qn}", 1800, _default_url)
            _default_desc = next(
                (d.get("desc", "Default") for d in qn_list if d.get("qn") == _default_qn), "Default"
            )
            supported_src.append(
                {"quality": _default_qn, "new_description": _default_desc, "url": _default_url}
            )

        async def get_and_cache_qn(qn_val, qn_name):
            try:
                # Wrap qn_val to satisfy bilibili-api's requirement for an Enum-like object with .value
                wrapped_qn = type("QN", (), {"value": qn_val})()
                # Single DEFAULT request carries FLV + HLS ladders; prefer FLV
                # (AVC-assembled), fall back to HLS master (PipePipe 79da4d21).
                try:
                    q_data = await room.get_room_play_info_v2(live_qn=wrapped_qn)
                except Exception:
                    q_data = await room.get_room_play_info_v2(
                        live_protocol=live.LiveProtocol.FLV,
                        live_format=live.LiveFormat.FLV,
                        live_qn=wrapped_qn,
                    )
                flv_url, hls_master = _extract_live_urls(q_data)
                url = _pick_live_url(flv_url, hls_master)

                if not url:
                    # Fallback to HLS-only request if DEFAULT gave nothing
                    q_data = await room.get_room_play_info_v2(
                        live_protocol=live.LiveProtocol.HLS, live_format=live.LiveFormat.FMP4, live_qn=wrapped_qn
                    )
                    _, hls_master = _extract_live_urls(q_data)
                    url = _pick_live_url("", hls_master)

                if url:
                    print(f"[Live] Cached room {room_id} QN {qn_val}: {url[:50]}...")
                    await appredis.setex(f"miku_live_{room_id}_{qn_val}", 1800, url)
                    return {"quality": qn_val, "new_description": qn_name, "url": url}
            except Exception as e:
                print(f"[Live] Error fetching QN {qn_val} for {room_id}: {e}")
            return None

        # Sequential, not parallel: Bilibili answers one getRoomPlayInfo fine
        # but -400s a burst of them (even qn=10000, which just succeeded).
        # Anonymous sessions only resolve the default quality (see helper).
        for d in _live_fetchable_qualities(qn_list, _default_qn):
            qn_val, qn_name = d.get("qn"), d.get("desc", "")
            if qn_val == _default_qn and _default_url:
                continue  # already resolved from the initial response
            try:
                await asyncio.sleep(0.4)
                r = await get_and_cache_qn(qn_val, qn_name)
            except Exception as e:
                print(f"[Live] Error fetching QN {qn_val} for {room_id}: {e}")
                r = None
            if r:
                supported_src.append(r)

        if supported_src:
            # Default entry is first; keep the default key in sync.
            await appredis.setex(f"miku_live_{room_id}", 1800, supported_src[0]["url"])
        else:
            # Final desperate fallback
            try:
                fb_info = await room.get_room_play_url()
                if "durl" in fb_info and fb_info["durl"]:
                    u = fb_info["durl"][0]["url"]
                    await appredis.setex(f"miku_live_{room_id}", 1800, u)
                    supported_src = [{"quality": "default", "new_description": "Default", "url": u}]
            except Exception:
                pass

        vinfo = {
            "title": info["title"],
            "desc": info["description"],
            "pic": info["pic"],
            "owner": {"name": info["uname"], "mid": info["uid"], "face": info["face"]},
            "stat": {"view": info["online"], "like": 0, "coin": 0, "favorite": 0, "share": 0},
            "pubdate": info["start_time"],
            "bvid": str(room_id),
            "tid": 0,
            "tname": info["area_name"],
        }
        return await render_template_with_theme(
            "video.html",
            vid=str(room_id),
            vinfo=vinfo,
            vcomments={"page": {"count": 0}, "replies": []},
            vrelated=[],
            keywords="",
            supported_src=supported_src,
            ato=False,
            idx=0,
            vset=[],
            is_live=True,
            live_format=live_format,
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"[ERROR] Live room error: {e}")
        return await render_template_with_theme(
            "error.html", status="Live load failed", desc="Failed to load the live room. Please check your network or try again later."
        ), 500


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
    if isinstance(results[0], Exception) or results[0] is None:
        err_msg = str(results[0]) if results[0] else "Bilibili returned empty data (possibly region-restricted)"
        return await render_template_with_theme(
            "error.html", status="Audio mode load failed", desc=err_msg, suggest="This content may be region-restricted or removed."
        ), 404

    def is_valid(res):
        return res is not None and not isinstance(res, Exception)

    vinfo = results[0]
    vtags = results[1] if is_valid(results[1]) else []
    vrelated = results[2] if not isinstance(results[2], Exception) else []
    vcomments = results[3] if not isinstance(results[3], Exception) else {"page": {"count": 0}, "replies": []}
    vset = results[4] if not isinstance(results[4], Exception) else [{"page": 1, "part": vid}]
    return await render_template_with_theme(
        "video_listen.html",
        vid=vid,
        vinfo=vinfo,
        vrelated=vrelated[:10],
        vcomments=vcomments,
        keywords=",".join(x.get("tag_name", "") for x in vtags),
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

        return await asyncio.wait_for(
            fetch_durl_supported_src(v, vid, idx, play_data=dash_data, ep_id=ep_id, cid=cid),
            timeout=20.0,
        )
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
        cached = await appredis.get(f"miku_dash_{vid}_{idx}")
        if cached:
            cached_data = safe_json_loads(cached)
            if isinstance(cached_data, dict) and cached_data.get("dash"):
                return cached_data
        try:
            from dash_proxy import video_get_dash_for_qn

            data = await asyncio.wait_for(video_get_dash_for_qn(v, idx, ep_id=ep_id, cid=pgc_cid), timeout=8.0)
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
            if isinstance(f, dict)
            and f.get("quality") is not None
            and str(f.get("quality")) in dash_tracks_by_quality
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
        subs = await asyncio.wait_for(v.get_subtitle_meta(page_index=idx), timeout=6.0)
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
        raw_result = await asyncio.wait_for(
            comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE), 4.0
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

    async def safe_api(coro, timeout=4.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return None

    raw = await safe_api(
        comment.get_comments(
            vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE, next_offset=next_offset
        ),
        4.0,
    )
    vcomments = (
        raw
        if raw and not isinstance(raw, Exception)
        else {"page": {"count": 0}, "replies": [], "next_offset": "", "is_end": True}
    )

    return await render_template_with_theme(
        "components/comment_items.html",
        vid=vid,
        vcomments=vcomments,
        is_live=False,
    )


@app.route("/api/component/comments/<vid>/<int:rpid>")
@rate_limit(**RATE_LIMITS["normal"])
async def api_component_sub_comments(vid, rpid):
    """Return rendered sub-comment HTML for a given parent comment."""
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r"^[A-Za-z0-9_-]{16,40}$", passed_nonce):
        g.csp_nonce = passed_nonce

    async def safe_api(coro, timeout=4.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return None

    page = request.args.get("page", 1, type=int)
    if page is None or page < 1:
        page = 1

    result = await safe_api(
        comment.get_sub_comments(vid, rpid, comment.CommentResourceType.VIDEO.value, page),
        4.0,
    )
    sub = (
        result
        if result and not isinstance(result, Exception)
        else {"page": {"count": 0, "num": page, "size": 20}, "replies": []}
    )

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
            await appredis.lrem(hist_key, 0, vid)
            await appredis.lpush(hist_key, vid)
            await appredis.ltrim(hist_key, 0, 49)
            await appredis.expire(hist_key, 3600 * 24 * 30)
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
                supported_src=[],
                ato=ato,
                idx=idx,
                vset=vset,
            )
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})

    # LIGHTWEIGHT FETCH ONLY
    async def safe_api(coro, timeout=4.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return None

    tasks = [
        safe_api(v.get_info(), 4.0),
        safe_api(v.get_tags(idx), 2.0),
        safe_api(v.get_related(), 5.0),
        safe_api(v.get_pages(), 4.0),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Check that core data was fetched successfully
    if isinstance(results[0], Exception) or results[0] is None:
        err_msg = str(results[0]) if results[0] else "Bilibili returned empty data (possibly region-restricted)"
        print(f"[Video] Error fetching info for {vid}: {err_msg}")
        # Show a friendly message for 404 / "啥都木有" (empty-result) responses
        if "啥都木有" in err_msg or "-404" in err_msg:
            return await render_template_with_theme(
                "error.html",
                status="Video load failed",
                desc="Bilibili returned empty result (-404)",
                suggest="This video may have been deleted, is under review, or is region-restricted.",
            ), 404
        return await render_template_with_theme(
            "error.html", status="Video load failed", desc=err_msg, suggest="Please try refreshing the page, or check the server network connection."
        ), 500

    def is_valid(res):
        return res is not None and not isinstance(res, Exception)

    vinfo = results[0]  # results[0] is guaranteed to be valid vinfo data at this point
    vtags = results[1] if is_valid(results[1]) else []
    vrelated = results[2] if is_valid(results[2]) else []
    vset = results[3] if is_valid(results[3]) else [{"page": 1, "part": vid}]

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

            data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=8.0)
            if has_valid_dash_tracks(data):
                await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(data))
            elif data and data.get("durl"):
                try:
                    await asyncio.wait_for(
                        fetch_durl_supported_src(v, vid, idx, play_data=data),
                        timeout=25.0,
                    )
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
        supported_src=supported_src,
        ato=ato,
        idx=idx,
        vset=vset,
    )
    if cache_ttl > 0:
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "MISS"})
    return html


@app.route("/audio/<auid>")
async def audio_view(auid):
    ato = request.args.get("ato") == "1"
    auid_int = int(auid[2:]) if auid.startswith("au") else int(auid)

    async def get_audio_url():
        if not await appredis.exists(f"mikuinv_{auid}_{0}_0"):
            try:
                asrc = await a.get_download_url()
                if "cdns" in asrc and asrc["cdns"]:
                    await appredis.setex(f"mikuinv_{auid}_{0}_0", 1800, asrc["cdns"][0])
                elif "url" in asrc:
                    await appredis.setex(f"mikuinv_{auid}_{0}_0", 1800, asrc["url"])
            except Exception:
                pass

    cache_ttl = cache_minutes("audio_minutes") * 60
    cache_key = f"audio:data:{auid}"
    cache_hit = False
    ainfo = None
    acomments = {"page": {"count": 0}, "replies": []}
    if cache_ttl > 0:
        data = await cache_get(cache_key, cache_ttl)
        if isinstance(data, dict) and isinstance(data.get("ainfo"), dict) and data.get("ainfo"):
            ainfo = data["ainfo"]
            cache_hit = True
    if cache_hit:
        # Info comes from cache; comments stay live so they never go stale.
        try:
            acomments = await comment.get_comments(
                auid_int, comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE
            )
        except Exception:
            pass
    else:
        a = audio.Audio(auid_int, credential=appcred)
        results = await asyncio.gather(
            a.get_info(),
            comment.get_comments(auid_int, comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE),
            get_audio_url(),
            return_exceptions=True,
        )
        ainfo = results[0] if not isinstance(results[0], Exception) else {}
        acomments = results[1] if not isinstance(results[1], Exception) else {"page": {"count": 0}, "replies": []}
        if cache_ttl > 0 and isinstance(ainfo, dict) and ainfo:
            await cache_set(cache_key, {"ainfo": ainfo}, cache_ttl)
    vinfo = {
        "title": ainfo.get("title", auid),
        "pic": ainfo.get("cover", ""),
        "desc": ainfo.get("intro", ""),
        "owner": {"name": ainfo.get("author", "Unknown"), "mid": ainfo.get("mid", 0), "face": ""},
        "stat": {
            "view": ainfo.get("statistic", {}).get("play", 0),
            "like": ainfo.get("statistic", {}).get("collect", 0),
            "coin": ainfo.get("statistic", {}).get("coin", 0),
            "favorite": ainfo.get("statistic", {}).get("collect", 0),
            "share": ainfo.get("statistic", {}).get("share", 0),
        },
        "pubdate": ainfo.get("passtime", 0),
        "bvid": auid,
        "tid": 0,
        "tname": "Audio",
    }
    html = await render_template_with_theme(
        "video_listen.html",
        vid=auid,
        vinfo=vinfo,
        vrelated=[],
        vcomments=acomments,
        keywords="",
        ato=ato,
        idx=0,
        vset=[{"page": 1, "part": auid}],
    )
    if cache_ttl > 0:
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT" if cache_hit else "MISS"})
    return html


@app.route("/audio_list/<amid>")
@app.route("/audio_list/<amid>:<idx>")
async def audio_list_view(amid, idx=0):
    idx, ato = int(idx), request.args.get("ato") == "1"
    amid_int = int(amid[2:]) if amid.startswith("am") else int(amid)
    cache_ttl = cache_minutes("audio_minutes") * 60
    cache_key = f"audiolist:data:{amid}:{idx}"
    cache_hit = False
    songs = ainfo = list_info = None
    acomments = {"page": {"count": 0}, "replies": []}
    if cache_ttl > 0:
        data = await cache_get(cache_key, cache_ttl)
        cached_songs = data.get("songs") if isinstance(data, dict) else None
        if (
            isinstance(data, dict)
            and isinstance(cached_songs, list)
            and cached_songs
            and all(isinstance(s, dict) and "id" in s for s in cached_songs)
            and isinstance(data.get("ainfo"), dict)
            and data.get("ainfo")
            and isinstance(data.get("list_info"), dict)
        ):
            songs, ainfo, list_info = cached_songs, data["ainfo"], data["list_info"]
            cache_hit = True
    if cache_hit:
        if idx >= len(songs):
            return await render_template_with_theme("error.html", status="Playlist empty", desc="No songs found"), 404
        current_song = songs[idx]
        # Track info comes from cache; comments stay live so they never go stale.
        try:
            acomments = await comment.get_comments(
                current_song["id"], comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE
            )
        except Exception:
            pass
        auid = f"au{current_song['id']}"
    else:
        al = audio.AudioList(amid_int, credential=appcred)
        songs_res = await al.get_song_list()
        songs = songs_res.get("data", [])
        if not songs or idx >= len(songs):
            return await render_template_with_theme("error.html", status="Playlist empty", desc="No songs found"), 404
        current_song = songs[idx]
        auid = f"au{current_song['id']}"
        a = audio.Audio(current_song["id"], credential=appcred)

        async def get_audio_url():
            if not await appredis.exists(f"mikuinv_{amid}_{idx}_0"):
                try:
                    asrc = await a.get_download_url()
                    if "cdns" in asrc and asrc["cdns"]:
                        await appredis.setex(f"mikuinv_{amid}_{idx}_0", 1800, asrc["cdns"][0])
                    elif "url" in asrc:
                        await appredis.setex(f"mikuinv_{amid}_{idx}_0", 1800, asrc["url"])
                except Exception:
                    pass

        results = await asyncio.gather(
            a.get_info(),
            al.get_info(),
            comment.get_comments(current_song["id"], comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE),
            get_audio_url(),
            return_exceptions=True,
        )
        ainfo, list_info = (
            results[0] if not isinstance(results[0], Exception) else {},
            results[1] if not isinstance(results[1], Exception) else {},
        )
        acomments = results[2] if not isinstance(results[2], Exception) else {"page": {"count": 0}, "replies": []}
        if cache_ttl > 0 and songs and isinstance(ainfo, dict) and ainfo:
            await cache_set(cache_key, {"songs": songs, "ainfo": ainfo, "list_info": list_info}, cache_ttl)
    vinfo = {
        "title": ainfo.get("title", auid),
        "pic": ainfo.get("cover", ""),
        "desc": ainfo.get("intro", ""),
        "owner": {"name": ainfo.get("author", "Unknown"), "mid": ainfo.get("mid", 0), "face": ""},
        "stat": {
            "view": ainfo.get("statistic", {}).get("play", 0),
            "like": ainfo.get("statistic", {}).get("collect", 0),
            "coin": ainfo.get("statistic", {}).get("coin", 0),
            "favorite": ainfo.get("statistic", {}).get("collect", 0),
            "share": ainfo.get("statistic", {}).get("share", 0),
        },
        "pubdate": ainfo.get("passtime", 0),
        "bvid": auid,
        "tid": 0,
        "tname": list_info.get("title", "Audio List"),
    }
    vset = [
        {
            "page": i + 1,
            "part": s.get("title", f"Song {i + 1}"),
            "duration": s.get("duration", 0),
            "first_frame": s.get("cover", ""),
        }
        for i, s in enumerate(songs)
        if isinstance(s, dict)
    ]
    html = await render_template_with_theme(
        "video_listen.html",
        vid=amid,
        vinfo=vinfo,
        vrelated=[],
        vcomments=acomments,
        keywords="",
        ato=ato,
        idx=idx,
        vset=vset,
    )
    if cache_ttl > 0:
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT" if cache_hit else "MISS"})
    return html


@app.route("/history")
async def history_view():
    hist_id = getattr(g, "hist_id", None)
    if not hist_id or getattr(g, "set_hist_cookie", False):
        return await render_template_with_theme("home.html", videos=[], title="Browsing history", message="You have no browsing history yet.")

    bvids = await appredis.lrange(f"miku_hist_{hist_id}", 0, -1)
    if not bvids:
        return await render_template_with_theme("home.html", videos=[], title="Browsing history", message="You have no browsing history yet.")

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
