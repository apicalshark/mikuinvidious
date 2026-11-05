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
import weakref

import orjson
import transformers
from api import live, live_area
from ip_slots import PerIpSlotLimiter
from quart import Response, request
from rate_limit import RATE_LIMITS, get_client_ip, rate_limit
from shared import app, appconf, appcred, appredis, cache_get, cache_set, render_template_with_theme

_background_tasks = set()

LIVE_CHAT_HANDSHAKE_DELAY = 1.0
LIVE_CHAT_IDLE_GRACE = 30.0
LIVE_CHAT_HEARTBEAT_INTERVAL = 20.0
LIVE_CHAT_QUEUE_MAXSIZE = 100
# Optional global room ceiling (None by default so a multi-IP botnet cannot
# starve legitimate users across distinct IPs; per-IP concurrency is enforced
# via ``live_chat_slots``).
LIVE_CHAT_MAX_ROOMS: int | None = None
LIVE_CHAT_MAX_PER_IP = 4
LIVE_ROOM_CACHE_TTL = 60
LIVE_CHAT_LOOKUP_TIMEOUT = 5.0

live_chat_slots = PerIpSlotLimiter(LIVE_CHAT_MAX_PER_IP)


async def _cache_live_quality(room_id, quality, url, default_url):
    """Keep the default URL alive for at least as long as each quality URL."""
    pipe = appredis.pipeline(transaction=True)
    pipe.setex(f"miku_live_{room_id}_{quality}", 1800, url)
    pipe.setex(f"miku_live_{room_id}", 1800, default_url)
    await pipe.execute()


class LiveChatCapacityError(RuntimeError):
    """Raised when LiveChatManager has reached its maximum concurrent room limit."""


class LiveChatRoom:
    """Multiplexes a single upstream Bilibili LiveDanmaku connection across all SSE clients in a room."""

    def __init__(
        self,
        room_id: int,
        manager: LiveChatManager,
        idle_grace: float = LIVE_CHAT_IDLE_GRACE,
        queue_maxsize: int = LIVE_CHAT_QUEUE_MAXSIZE,
    ):
        self.room_id = room_id
        self.manager = manager
        self.idle_grace = idle_grace
        self.queue_maxsize = queue_maxsize
        self.subscribers: set[asyncio.Queue] = set()
        self._stop_event = asyncio.Event()
        self._conn_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._conn_task is None or self._conn_task.done():
            self._stop_event.clear()
            self._conn_task = asyncio.create_task(self._run_upstream())

    def broadcast(self, msg: dict) -> None:
        for q in list(self.subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def add_subscriber(self) -> asyncio.Queue:
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
            self._idle_task = None
        q: asyncio.Queue = asyncio.Queue(maxsize=self.queue_maxsize)
        self.subscribers.add(q)
        self.start()
        return q

    def remove_subscriber(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)
        if not self.subscribers and not self._stop_event.is_set():
            if self._idle_task is None or self._idle_task.done():
                self._idle_task = asyncio.create_task(self._idle_watchdog())

    async def _idle_watchdog(self) -> None:
        try:
            await asyncio.sleep(self.idle_grace)
            if not self.subscribers:
                await self.shutdown()
        except asyncio.CancelledError:
            pass

    async def _run_upstream(self) -> None:
        from api import Credential
        from api import live as b_live

        cred = appcred if isinstance(appcred, Credential) else None
        while not self._stop_event.is_set():
            dm_client = None
            try:
                dm_client = b_live.LiveDanmaku(self.room_id, credential=cred)

                @dm_client.on("DANMU_MSG")
                async def on_danmaku(event):
                    try:
                        info = event["data"]["info"]
                        self.broadcast({"user": info[2][1], "text": info[1]})
                    except Exception:
                        pass

                await dm_client.connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[LiveChat] Connection error for room {self.room_id}: {e}")
            finally:
                if dm_client:
                    try:
                        await dm_client.disconnect()
                    except Exception:
                        pass

            if not self._stop_event.is_set():
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
                if not self._stop_event.is_set():
                    print(f"[LiveChat] Attempting reconnection for room {self.room_id}...")

    async def shutdown(self) -> None:
        self._stop_event.set()
        self.manager._remove_room(self.room_id, self)
        if self._idle_task is not None and not self._idle_task.done() and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        if self._conn_task is not None and not self._conn_task.done():
            self._conn_task.cancel()
            try:
                await asyncio.gather(self._conn_task, return_exceptions=True)
            except Exception:
                pass


async def _lookup_real_room_id(room_id: int) -> int | None:
    from api import Credential
    from api import live as b_live

    try:
        cred = appcred if isinstance(appcred, Credential) else None
        real_id = await b_live.LiveRoom(room_id, credential=cred).get_room_id()
        if isinstance(real_id, int) and real_id > 0:
            return real_id
    except Exception:
        pass
    return None


class LiveChatManager:
    """Registry of active LiveChatRoom instances keyed by canonical room_id."""

    def __init__(
        self,
        idle_grace: float = LIVE_CHAT_IDLE_GRACE,
        queue_maxsize: int = LIVE_CHAT_QUEUE_MAXSIZE,
        max_rooms: int | None = LIVE_CHAT_MAX_ROOMS,
        lookup_real_room_id=None,
    ):
        self.rooms: dict[int, LiveChatRoom] = {}
        self._canonical_ids: dict[int, int] = {}
        self.idle_grace = idle_grace
        self.queue_maxsize = queue_maxsize
        self.max_rooms = max_rooms
        self._lookup_real_room_id = lookup_real_room_id

    async def _resolve_room_id(self, room_id: int) -> int:
        room_id = int(room_id)
        cached = self._canonical_ids.get(room_id)
        if cached is not None:
            return cached
        existing = self.rooms.get(room_id)
        if existing is not None and not existing._stop_event.is_set():
            return room_id
        lookup = self._lookup_real_room_id or _lookup_real_room_id
        try:
            async with asyncio.timeout(LIVE_CHAT_LOOKUP_TIMEOUT):
                real_id = await lookup(room_id)
            if isinstance(real_id, int) and real_id > 0:
                self._canonical_ids[room_id] = real_id
                self._canonical_ids[real_id] = real_id
                return real_id
        except Exception:
            pass
        return room_id

    async def subscribe(self, room_id: int) -> asyncio.Queue:
        canonical_id = await self._resolve_room_id(room_id)
        room = self.rooms.get(canonical_id)
        if room is None or room._stop_event.is_set():
            if self.max_rooms is not None:
                active_rooms = sum(1 for r in self.rooms.values() if not r._stop_event.is_set())
                if active_rooms >= self.max_rooms:
                    raise LiveChatCapacityError("Too many active live chat rooms")
            room = LiveChatRoom(
                canonical_id,
                manager=self,
                idle_grace=self.idle_grace,
                queue_maxsize=self.queue_maxsize,
            )
            self.rooms[canonical_id] = room
        return room.add_subscriber()

    async def unsubscribe(self, room_id: int, queue: asyncio.Queue) -> None:
        canonical_id = self._canonical_ids.get(int(room_id), int(room_id))
        room = self.rooms.get(canonical_id) or self.rooms.get(int(room_id))
        if room is not None:
            room.remove_subscriber(queue)

    def _remove_room(self, room_id: int, room: LiveChatRoom) -> None:
        if self.rooms.get(room_id) is room:
            del self.rooms[room_id]
        stale_aliases = [alias for alias, cid in self._canonical_ids.items() if cid == room_id]
        for alias in stale_aliases:
            del self._canonical_ids[alias]

    async def shutdown_all(self) -> None:
        for room in list(self.rooms.values()):
            await room.shutdown()


live_chat_manager = LiveChatManager()


async def _live_chat_rate_key(req) -> str:
    """Per-IP bucket shared across all /live/chat/<room_id> rooms."""
    return f"{get_client_ip(req)}:live_chat"


@app.route("/live/chat/<int:room_id>")
@rate_limit(**RATE_LIMITS["strict"], key_func=_live_chat_rate_key)
async def live_chat_sse(room_id):
    slot = live_chat_slots.try_acquire(get_client_ip(request))
    if slot is None:
        return Response("Too Many Requests", status=429, headers={"Retry-After": "1"})

    async def event_stream():
        queue = None
        try:
            yield f"data: {orjson.dumps({'user': 'SYSTEM', 'text': 'Chat connected'}).decode('utf-8')}\n\n"

            # Layer 1: Handshake gate — drop instant scanner/probe disconnects
            # before allocating room state or opening an upstream Bilibili WebSocket.
            if LIVE_CHAT_HANDSHAKE_DELAY > 0:
                await asyncio.sleep(LIVE_CHAT_HANDSHAKE_DELAY)

            # Layer 2: Subscribe to shared room multiplexer.
            try:
                queue = await live_chat_manager.subscribe(room_id)
            except LiveChatCapacityError:
                yield f"data: {orjson.dumps({'user': 'SYSTEM', 'text': 'Chat capacity reached, please try again later'}).decode('utf-8')}\n\n"
                return

            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=LIVE_CHAT_HEARTBEAT_INTERVAL)
                    yield f"data: {orjson.dumps(msg).decode('utf-8')}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError, GeneratorExit:
            raise
        except Exception as e:
            print(f"[LiveChat] Unexpected error in event_stream for room {room_id}: {e}")
        finally:
            slot.release()
            if queue is not None:
                await asyncio.shield(live_chat_manager.unsubscribe(room_id, queue))

    gen = event_stream()
    weakref.finalize(gen, slot.release)
    return Response(
        gen,
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/live")
@rate_limit(**RATE_LIMITS["normal"])
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


@app.route("/live/<int:room_id>")
@rate_limit(**RATE_LIMITS["normal"])
async def live_room_view(room_id):
    try:
        cache_key = f"live:room:{room_id}"
        if LIVE_ROOM_CACHE_TTL > 0:
            cached = await cache_get(cache_key, LIVE_ROOM_CACHE_TTL)
            if (
                isinstance(cached, dict)
                and isinstance(cached.get("vinfo"), dict)
                and isinstance(cached.get("supported_src"), list)
                and cached.get("supported_src")
            ):
                html = await render_template_with_theme(
                    "video.html",
                    vid=str(room_id),
                    vinfo=cached["vinfo"],
                    vcomments={"page": {"count": 0}, "replies": []},
                    vrelated=[],
                    keywords="",
                    supported_src=cached["supported_src"],
                    ato=False,
                    idx=0,
                    vset=[],
                    is_live=True,
                    live_format=cached.get("live_format", "flv"),
                )
                return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})

        room = live.LiveRoom(room_id, credential=appcred)

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
            await _cache_live_quality(room_id, _default_qn, _default_url, _default_url)
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
                    default_url = supported_src[0]["url"] if supported_src else url
                    await _cache_live_quality(room_id, qn_val, url, default_url)
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
        if LIVE_ROOM_CACHE_TTL > 0 and supported_src:
            await cache_set(
                cache_key,
                {"vinfo": vinfo, "supported_src": supported_src, "live_format": live_format},
                LIVE_ROOM_CACHE_TTL,
            )
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
