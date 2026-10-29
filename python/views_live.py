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

import orjson
import transformers
from api import live, live_area
from quart import Response, request
from rate_limit import RATE_LIMITS, rate_limit
from shared import app, appconf, appcred, appredis, render_template_with_theme

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
        except asyncio.CancelledError, GeneratorExit:
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
            _default_desc = next((d.get("desc", "Default") for d in qn_list if d.get("qn") == _default_qn), "Default")
            supported_src.append({"quality": _default_qn, "new_description": _default_desc, "url": _default_url})

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
            "error.html",
            status="Live load failed",
            desc="Failed to load the live room. Please check your network or try again later.",
        ), 500
