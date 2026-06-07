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
import os
import re

import orjson
import transformers
from bilibili_api import article, audio, comment, homepage, live, live_area, opus, search, user, video, video_zone
from extra import (
    article_to_any,
    article_to_html,
    av2bv,
    get_article_info,
    video_get_src_for_qn,
)
from quart import Response, redirect, request, url_for, g
from shared import Network, app, appconf, appcred, appredis, render_template_with_theme, safe_json_loads
from rate_limit import rate_limit, RATE_LIMITS


@app.route("/live/chat/<int:room_id>")
@rate_limit(**RATE_LIMITS["strict"])
async def live_chat_sse(room_id):
    from bilibili_api import Credential
    from bilibili_api import live as b_live

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
    api_res = await homepage.get_videos()
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
    return await render_template_with_theme("home.html", videos=processed_videos)


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
            "error.html", status="无法搜索", desc="没有发送搜索关键字。", sg="请设置搜索关键字后重试。"
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

    sinfo = await search.search_by_type(
        q, page=i, search_type=search_type, order_type=order_map.get(request.args.get("sort"))
    )
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


@app.route("/space/<mid>")
@app.route("/space/<mid>/")
async def space_view(mid):
    u = user.User(mid, credential=appcred)
    uinfo, uvids = await asyncio.gather(u.get_user_info(), u.get_videos(pn=request.args.get("i") or 1, ps=28))
    return await render_template_with_theme("space.html", uinfo=uinfo, uvids=uvids)


@app.route("/author/<mid>")
@app.route("/author/<mid>/")
async def author_view(mid):
    u = user.User(mid, credential=appcred)
    uinfo, uarticles = await asyncio.gather(u.get_user_info(), u.get_articles(pn=request.args.get("i") or 1, ps=28))
    return await render_template_with_theme("author.html", uinfo=uinfo, uarts=uarticles)


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
    client = await Network.get_async_client()
    req = None
    try:
        ua = "Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com) 8.76.0 os/android model/WTF mobi_app/android build/8760000 channel/not_found innerVer/8760010 osVer/15 network/2"
        _req = client.build_request("GET", url, headers={"User-Agent": ua})
        req = await client.send(_req, follow_redirects=True)
        if req.status_code != 200:
            return await render_template_with_theme(
                "error.html",
                status="没有找到文章" if req.status_code == 404 else "服务器错误",
                desc="后端服务器发送了无效的回复",
                suggest="这很可能说明您访问的文章不存在，请检查您的请求。" if req.status_code == 404 else None,
            ), req.status_code

        if (
            appconf["render"]["use_pandoc"]
            and request.args.get("format") in appconf["render"]["article_allowed_formats"]
        ):
            return await article_to_any(req.text, request.args.get("format"))
        else:
            cvid = cid.replace("cv", "").replace("opus", "")
            try:
                arinfo = get_article_info(req.text, cid)
                try:
                    if is_opus:
                        o = opus.Opus(int(cvid), credential=appcred)
                        api_info = await o.get_info()
                        for module in api_info.get("item", {}).get("modules", []):
                            if module.get("module_stat"):
                                stat = module["module_stat"]
                                arinfo["stats"]["like"] = stat.get("like", {}).get("count", arinfo["stats"]["like"])
                                arinfo["stats"]["coin"] = stat.get("coin", {}).get("count", arinfo["stats"]["coin"])
                                arinfo["stats"]["favorite"] = stat.get("favorite", {}).get(
                                    "count", arinfo["stats"]["favorite"]
                                )
                                arinfo["stats"]["share"] = stat.get("forward", {}).get(
                                    "count", arinfo["stats"]["share"]
                                )
                    else:
                        ar = article.Article(int(cvid))
                        api_info = await ar.get_info()
                        if api_info.get("stats"):
                            arinfo["stats"].update(api_info["stats"])
                        if api_info.get("title") and not arinfo["title"]:
                            arinfo["title"] = api_info["title"]
                except Exception:
                    pass
                return await render_template_with_theme(
                    "read.html", cid=cid, arinfo=arinfo, article_content=article_to_html(req.text), is_opus=is_opus
                )
            except Exception:
                return await render_template_with_theme(
                    "error.html",
                    status="没有找到文章",
                    desc="文章不存在或解析错误",
                    sg="这很可能说明您访问的文章不存在，请检查您的请求。",
                ), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Read] Error fetching article {url}: {e}")
        return await render_template_with_theme(
            "error.html", status="网络错误", desc="无法获取文章内容，请检查网络连接或代理设置。", suggest="请检查您的网络连接或代理设置。"
        ), 500
    finally:
        if req:
            await req.aclose()


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
        return await render_template_with_theme("home.html", videos=rooms, title="直播")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[ERROR] Live list error: {e}")
        return await render_template_with_theme("error.html", status="直播列表加载失败", desc="无法获取直播列表，请稍后重试。"), 500


@app.route("/live/<room_id>")
async def live_room_view(room_id):
    try:
        room_id_int = int(room_id)
        room = live.LiveRoom(room_id_int, credential=appcred)

        # Get basic room info first
        info_data = await room.get_room_info()

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
        qn_list = play_data.get("play_url", {}).get("g_qn_desc", []) or [{"qn": 0, "desc": "默认"}]

        async def get_and_cache_qn(qn_val, qn_name):
            try:
                # Wrap qn_val to satisfy bilibili-api's requirement for an Enum-like object with .value
                wrapped_qn = type("QN", (), {"value": qn_val})()
                # Try FLV first
                q_data = await room.get_room_play_info_v2(
                    live_protocol=live.LiveProtocol.FLV, live_format=live.LiveFormat.FLV, live_qn=wrapped_qn
                )
                stream = q_data.get("play_url", {}).get("stream", [])
                url = None
                for s in stream:
                    for f in s.get("format", []):
                        for c in f.get("codec", []):
                            url = c.get("url") or c.get("base_url")
                            if url:
                                break
                        if url:
                            break
                    if url:
                        break

                if not url:
                    # Fallback to HLS if FLV fails
                    q_data = await room.get_room_play_info_v2(
                        live_protocol=live.LiveProtocol.HLS, live_format=live.LiveFormat.FMP4, live_qn=wrapped_qn
                    )
                    stream = q_data.get("play_url", {}).get("stream", [])
                    for s in stream:
                        for f in s.get("format", []):
                            for c in f.get("codec", []):
                                url = c.get("url") or c.get("base_url")
                                if url:
                                    break
                            if url:
                                break
                        if url:
                            break

                if url:
                    print(f"[Live] Cached room {room_id} QN {qn_val}: {url[:50]}...")
                    await appredis.setex(f"miku_live_{room_id}_{qn_val}", 1800, url)
                    return {"quality": qn_val, "new_description": qn_name, "url": url}
            except Exception as e:
                print(f"[Live] Error fetching QN {qn_val} for {room_id}: {e}")
            return None

        q_results = await asyncio.gather(*[get_and_cache_qn(d["qn"], d["desc"]) for d in qn_list])
        supported_src = [r for r in q_results if r]

        if supported_src:
            # Set default quality as well
            await appredis.setex(f"miku_live_{room_id}", 1800, supported_src[0]["url"])
        else:
            # Final desperate fallback
            try:
                fb_info = await room.get_room_play_url()
                if "durl" in fb_info and fb_info["durl"]:
                    u = fb_info["durl"][0]["url"]
                    await appredis.setex(f"miku_live_{room_id}", 1800, u)
                    supported_src = [{"quality": "default", "new_description": "默认", "url": u}]
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
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[ERROR] Live room error: {e}")
        return await render_template_with_theme("error.html", status="直播加载失败", desc="无法加载直播间，请检查网络或稍后重试。"), 500


@app.route("/video_listen/<vid>")
@app.route("/video_listen/<vid>:<idx>")
@app.route("/video_listen/<vid>/")
@app.route("/video_listen/<vid>:<idx>/")
async def video_listen_view(vid, idx=0):
    # Validate video ID format
    import re
    if not re.match(r'^(BV[a-zA-Z0-9]{10}|av\d+)$', vid):
        abort(400, description="Invalid video ID format")
    
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

    # 核心數據檢查
    if isinstance(results[0], Exception) or results[0] is None:
        err_msg = str(results[0]) if results[0] else "B站返回了空的數據 (可能受到地區限制)"
        return await render_template_with_theme(
            "error.html", status="音频模式加载失败", desc=err_msg, suggest="該內容可能受到地區限制或已被下架。"
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


@app.route("/api/component/player/<vid>/<int:idx>")
@rate_limit(**RATE_LIMITS["normal"])
async def api_component_player(vid, idx):
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r'^[A-Za-z0-9_-]{16,40}$', passed_nonce):
        g.csp_nonce = passed_nonce
    v = video.Video(bvid=vid, credential=appcred)
    ep_id = request.args.get("ep_id")
    if ep_id and ep_id.isdigit():
        ep_id = int(ep_id)
    else:
        ep_id = None

    async def get_durl_playurls():
        v_supported_src = []

        cached = await appredis.get(f"mikuinv_{vid}_{idx}")
        if cached:
            cached_src = safe_json_loads(cached)
            if isinstance(cached_src, list):
                return cached_src

        try:
            data = await asyncio.wait_for(video_get_src_for_qn(v, idx, ep_id=ep_id), timeout=4.0)
            if data and "durl" in data:
                url = data["durl"][0]["url"]
                qn = data.get("quality", 16)
                ext = (".flv" if ".flv" in url.lower() else ".mp4")
                await appredis.setex(f"mikuinv_{vid}_{idx}_{qn}", 1800, url)
                # Cache first backup URL if available (Akamai typically, works globally)
                backup_list = data["durl"][0].get("backup_url", [])
                if backup_list and backup_list[0] != url:
                    await appredis.setex(f"mikuinv_{vid}_{idx}_{qn}_bak", 1800, backup_list[0])

                support_formats = data.get("support_formats", [])
                if support_formats:
                    # Cache highest quality
                    first_qn = support_formats[0]["quality"]
                    cached_qns = {qn, first_qn}
                    if first_qn != qn:
                        try:
                            res_high = await asyncio.wait_for(
                                video_get_src_for_qn(v, idx, first_qn, ep_id=ep_id), timeout=4.0
                            )
                            if "durl" in res_high:
                                await appredis.setex(
                                    f"mikuinv_{vid}_{idx}_{first_qn}", 1800, res_high["durl"][0]["url"]
                                )
                                hb = res_high["durl"][0].get("backup_url", [])
                                if hb and hb[0] != res_high["durl"][0]["url"]:
                                    await appredis.setex(
                                        f"mikuinv_{vid}_{idx}_{first_qn}_bak", 1800, hb[0]
                                    )
                        except Exception:
                            pass
                    # Cache up to 2 additional fallback qualities (720p, 480p etc.)
                    for sf in support_formats[1:]:
                        sf_qn = sf["quality"]
                        if sf_qn in cached_qns:
                            continue
                        if len(cached_qns) >= 4:
                            break
                        try:
                            res_qn = await asyncio.wait_for(
                                video_get_src_for_qn(v, idx, sf_qn, ep_id=ep_id), timeout=4.0
                            )
                            if "durl" in res_qn:
                                await appredis.setex(
                                    f"mikuinv_{vid}_{idx}_{sf_qn}", 1800, res_qn["durl"][0]["url"]
                                )
                                qb = res_qn["durl"][0].get("backup_url", [])
                                if qb and qb[0] != res_qn["durl"][0]["url"]:
                                    await appredis.setex(
                                        f"mikuinv_{vid}_{idx}_{sf_qn}_bak", 1800, qb[0]
                                    )
                                cached_qns.add(sf_qn)
                        except Exception:
                            pass

                v_supported_src = [
                    {"quality": f["quality"], "new_description": f["new_description"], "ext": ext}
                    for f in support_formats
                ]
                await appredis.setex(f"mikuinv_{vid}_{idx}", 1800, orjson.dumps(v_supported_src))
        except Exception:
            pass

        return v_supported_src

    try:
        if ep_id:
            from bilibili_api.utils.network import Api

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
            else:
                vinfo = await v.get_info()
        else:
            vinfo = await v.get_info()
    except Exception:
        vinfo = {"pic": ""}

    supported_src = await get_durl_playurls()

    return await render_template_with_theme(
        "components/player_part.html",
        vid=vid,
        vinfo=vinfo,
        idx=idx,
        supported_src=supported_src,
        is_live=False,
    )


@app.route("/api/component/meta/<vid>/<int:idx>")
@rate_limit(**RATE_LIMITS["normal"])
async def api_component_meta(vid, idx):
    # Use passed CSP nonce from main page to avoid CSP mismatch
    passed_nonce = request.headers.get("X-CSP-Nonce")
    if passed_nonce and re.match(r'^[A-Za-z0-9_-]{16,40}$', passed_nonce):
        g.csp_nonce = passed_nonce
    async def safe_api(coro, timeout=4.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return None

    tasks = [
        safe_api(comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE), 4.0),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    vcomments = (
        results[0] if results[0] and not isinstance(results[0], Exception) else {"page": {"count": 0}, "replies": []}
    )

    return await render_template_with_theme("components/meta_part.html", vid=vid, vcomments=vcomments, is_live=False)


@app.route("/video/<vid>")
@app.route("/video/<vid>/")
@app.route("/video/<vid>:<idx>")
@app.route("/video/<vid>:<idx>/")
@rate_limit(**RATE_LIMITS["normal"])
async def video_view(vid, idx=0):
    # Validate video ID format
    import re
    if not re.match(r'^(BV[a-zA-Z0-9]{10}|av\d+)$', vid):
        abort(400, description="Invalid video ID format")
    
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

    # 檢查核心數據是否獲取成功
    if isinstance(results[0], Exception) or results[0] is None:
        err_msg = str(results[0]) if results[0] else "B站返回了空的數據 (可能受到地區限制)"
        print(f"[Video] Error fetching info for {vid}: {err_msg}")
        # 如果是 404 或 啥都木有，顯示友好提示
        if "啥都木有" in err_msg or "-404" in err_msg:
            return await render_template_with_theme(
                "error.html",
                status="无法加载视频",
                desc="B站返回：啥都木有 (-404)",
                suggest="該影片可能已被刪除、審核中，或是受到地區限制（僅限港澳台）。",
            ), 404
        return await render_template_with_theme(
            "error.html", status="视频加载出错", desc=err_msg, suggest="請嘗試刷新頁面，或檢查伺服器網路連接。"
        ), 500

    def is_valid(res):
        return res is not None and not isinstance(res, Exception)

    vinfo = results[0]  # 此時 results[0] 肯定是有效的 vinfo 資料
    vtags = results[1] if is_valid(results[1]) else []
    vrelated = results[2] if is_valid(results[2]) else []
    vset = results[3] if is_valid(results[3]) else [{"page": 1, "part": vid}]

    # Pre-cache durl URLs if proxy is enabled
    if appconf["proxy"]["use_proxy"]:

        async def precache_durl():
            try:
                await asyncio.wait_for(video_get_src_for_qn(v, idx), timeout=4.0)
            except Exception as e:
                print(f"[Video] Pre-cache durl failed for {vid}: {e}")

        asyncio.create_task(precache_durl())

    vcomments = {"page": {"count": 0}, "replies": []}
    supported_src = []

    return await render_template_with_theme(
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


@app.route("/audio/<auid>")
async def audio_view(auid):
    ato = request.args.get("ato") == "1"
    auid_int = int(auid[2:]) if auid.startswith("au") else int(auid)
    a = audio.Audio(auid_int, credential=appcred)

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

    results = await asyncio.gather(
        a.get_info(),
        comment.get_comments(auid_int, comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE),
        get_audio_url(),
        return_exceptions=True,
    )
    ainfo = results[0] if not isinstance(results[0], Exception) else {}
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
    acomments = results[1] if not isinstance(results[1], Exception) else {"page": {"count": 0}, "replies": []}
    return await render_template_with_theme(
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


@app.route("/audio_list/<amid>")
@app.route("/audio_list/<amid>:<idx>")
async def audio_list_view(amid, idx=0):
    idx, ato = int(idx), request.args.get("ato") == "1"
    amid_int = int(amid[2:]) if amid.startswith("am") else int(amid)
    al = audio.AudioList(amid_int, credential=appcred)
    songs_res = await al.get_song_list()
    songs = songs_res.get("data", [])
    if not songs or idx >= len(songs):
        return await render_template_with_theme("error.html", status="歌单为空", desc="没有找到歌曲"), 404
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
    acomments = results[2] if not isinstance(results[2], Exception) else {"page": {"count": 0}, "replies": []}
    vset = [
        {
            "page": i + 1,
            "part": s.get("title", f"Song {i + 1}"),
            "duration": s.get("duration", 0),
            "first_frame": s.get("cover", ""),
        }
        for i, s in enumerate(songs)
    ]
    return await render_template_with_theme(
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


@app.route("/history")
async def history_view():
    hist_id = getattr(g, "hist_id", None)
    if not hist_id or getattr(g, "set_hist_cookie", False):
        return await render_template_with_theme("home.html", videos=[], title="浏览历史", message="您还没有浏览历史。")

    bvids = await appredis.lrange(f"miku_hist_{hist_id}", 0, -1)
    if not bvids:
        return await render_template_with_theme("home.html", videos=[], title="浏览历史", message="您还没有浏览历史。")

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

    return await render_template_with_theme("home.html", videos=videos, title="浏览历史")
