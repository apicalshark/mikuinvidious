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
Bilibili live module.

Minimal drop-in for ``bilibili_api.live`` + ``bilibili_api.live_area`` covering
LiveRoom (get_room_info, get_room_play_info_v2, get_room_play_url),
LiveDanmaku (SSE chat via WebSocket), and live_area.get_list_by_area.
"""

import asyncio
import json
import logging
import struct
from enum import Enum

import brotli
from aiohttp import WSMsgType

from .client import HEADERS, Api
from .credential import Credential

__all__ = [
    "LiveRoom",
    "LiveDanmaku",
    "ScreenResolution",
    "LiveProtocol",
    "LiveFormat",
    "LiveCodec",
]


class ScreenResolution(Enum):
    FOUR_K = 20000
    ORIGINAL = 10000
    BLU_RAY_DOLBY = 401
    BLU_RAY = 400
    ULTRA_HD = 250
    HD = 150
    FLUENCY = 80


class LiveProtocol(Enum):
    FLV = 0
    HLS = 1
    DEFAULT = "0,1"


class LiveFormat(Enum):
    FLV = 0
    TS = 1
    FMP4 = 2
    DEFAULT = "0,1,2"


class LiveCodec(Enum):
    AVC = 0
    HEVC = 1
    DEFAULT = "0,1"


class LiveRoom:
    def __init__(self, room_display_id, credential=None):
        self.room_display_id = room_display_id
        self.credential = credential if credential is not None else Credential()
        self._real_id = None
        self._ruid = None

    async def get_room_play_info(self) -> dict:
        params = {"room_id": self.room_display_id}
        api = {
            "url": "https://api.live.bilibili.com/xlive/web-room/v1/index/getRoomPlayInfo",
            "method": "GET",
            "verify": False,
        }
        resp = await Api(**api, credential=self.credential).update_params(**params).result
        self._ruid = resp.get("uid")
        self._real_id = resp.get("room_id")
        return resp

    async def get_room_id(self) -> int:
        if self._real_id is None:
            await self.get_room_play_info()
        return self._real_id

    async def get_danmu_info(self) -> dict:
        params = {"id": await self.get_room_id(), "type": 0, "web_location": "444.8"}
        api = {
            "url": "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo",
            "method": "GET",
            "verify": False,
            "wbi": True,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_room_info(self) -> dict:
        params = {"room_id": self.room_display_id}
        api = {
            "url": "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_room_play_url(self, screen_resolution=ScreenResolution.ORIGINAL) -> dict:
        if isinstance(screen_resolution, ScreenResolution):
            qn = screen_resolution.value
        else:
            qn = screen_resolution
        params = {
            "cid": self.room_display_id,
            "platform": "web",
            "qn": qn,
            "https_url_req": "1",
            "ptype": "16",
        }
        api = {
            "url": "https://api.live.bilibili.com/xlive/web-room/v1/playUrl/playUrl",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_room_play_info_v2(
        self,
        live_protocol=LiveProtocol.DEFAULT,
        live_format=LiveFormat.DEFAULT,
        live_codec=LiveCodec.DEFAULT,
        live_qn=ScreenResolution.ORIGINAL,
    ) -> dict:
        def _v(e, default):
            return e.value if isinstance(e, Enum) else (default if e is None else e)

        params = {
            "room_id": self.room_display_id,
            "platform": "web",
            "ptype": "16",
            "protocol": _v(live_protocol, LiveProtocol.DEFAULT.value),
            "format": _v(live_format, LiveFormat.DEFAULT.value),
            "codec": _v(live_codec, LiveCodec.DEFAULT.value),
            "qn": _v(live_qn, ScreenResolution.ORIGINAL.value),
        }
        api = {
            "url": "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result


def _base_headers():
    return dict(HEADERS)


class LiveDanmaku:
    """WebSocket realtime danmaku for a live room.

    Exposes the subset of the bilibili_api.LiveDanmaku interface used by
    MikuInvidious: ``@client.on("DANMU_MSG")``, ``await client.connect()`` and
    ``await client.disconnect()``. Handlers receive ``event`` with the same
    shape as bilibili_api (``event["data"]["info"]``).
    """

    PROTOCOL_VERSION_RAW_JSON = 0
    PROTOCOL_VERSION_HEARTBEAT = 1
    PROTOCOL_VERSION_BROTLI_JSON = 3

    DATAPACK_TYPE_HEARTBEAT = 2
    DATAPACK_TYPE_HEARTBEAT_RESPONSE = 3
    DATAPACK_TYPE_NOTICE = 5
    DATAPACK_TYPE_VERIFY = 7
    DATAPACK_TYPE_VERIFY_SUCCESS_RESPONSE = 8

    def __init__(self, room_display_id, debug=False, credential=None, max_retry=5, retry_after=1.0, **kwargs):
        self.room_display_id = room_display_id
        self.credential = credential if credential is not None else Credential()
        self.max_retry = max_retry
        self.retry_after = retry_after
        self._handlers = {}
        self._tasks = []
        self._ws = None
        self._session = None
        self._connector = None
        self._heartbeat_task = None
        self._status = 0
        self._real_id = None
        self.err_reason = ""
        self.logger = logging.getLogger(f"LiveDanmaku_{room_display_id}")

    def on(self, event_name: str):
        name = event_name.upper()
        if name not in self._handlers:
            self._handlers[name] = []

        def decorator(func):
            self._handlers[name].append(func)
            return func

        return decorator

    def dispatch(self, name: str, *args, **kwargs):
        name = name.upper()
        for handler in self._handlers.get(name, []):
            result = handler(*args, **kwargs)
            if asyncio.iscoroutine(result):
                task = asyncio.create_task(result)
                self._tasks.append(task)

    async def connect(self) -> None:
        import aiohttp

        room = LiveRoom(self.room_display_id, credential=self.credential)
        self._real_id = await room.get_room_id()
        conf = await room.get_danmu_info()
        token = conf["token"]
        hosts = [h for h in conf["host_list"] if h.get("wss_port")]

        head = _base_headers()
        buvid = self.credential.buvid3 if self.credential.has_buvid3() else ""
        verify = json.dumps(
            {
                "uid": getattr(self.credential, "dedeuserid", 0) or 0,
                "roomid": self._real_id,
                "protover": 3,
                "platform": "web",
                "type": 2,
                "buvid": buvid,
                "key": token,
            },
            separators=(",", ":"),
        ).encode()

        ok = False
        try:
            for host in hosts:
                uri = f"wss://{host['host']}:{host['wss_port']}/sub"
                try:
                    self._connector = aiohttp.TCPConnector(limit=10)
                    self._session = aiohttp.ClientSession(
                        connector=self._connector,
                        headers=head,
                    )
                    self._ws = await self._session.ws_connect(uri)
                    await self._ws.send_bytes(
                        self._pack(verify, self.PROTOCOL_VERSION_HEARTBEAT, self.DATAPACK_TYPE_VERIFY)
                    )
                    self._status = 2
                    self._heartbeat_task = asyncio.create_task(self._heartbeat())
                    await self._recv_loop()
                    ok = True
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.err_reason = str(e)
                    self.logger.warning(f"live danmaku connect failed: {uri}: {e}")
                    await self._cleanup()
        finally:
            # recv_loop exits when the connection closes; setup failures and
            # cancellation must stop the heartbeat and release all resources.
            await self._cleanup()

        if not ok and self._ws is None:
            self.err_reason = self.err_reason or "无法连接直播弹幕服务器"

    async def _recv_loop(self) -> None:
        while True:
            msg = await self._ws.receive()
            if msg.type in (
                WSMsgType.CLOSE,
                WSMsgType.CLOSING,
                WSMsgType.CLOSED,
                WSMsgType.ERROR,
            ):
                break
            if msg.type == WSMsgType.BINARY:
                data = msg.data
                if isinstance(data, (bytes, bytearray)):
                    await self._handle_data(bytes(data))

    async def _heartbeat(self) -> None:
        HEARTBEAT = self._pack(
            b"[object Object]", self.PROTOCOL_VERSION_HEARTBEAT, self.DATAPACK_TYPE_HEARTBEAT
        )
        while True:
            try:
                await self._ws.send_bytes(HEARTBEAT)
            except Exception:
                break
            await asyncio.sleep(30)

    async def _handle_data(self, data: bytes) -> None:
        infos = self._unpack(data)
        for info in infos:
            if info["datapack_type"] == self.DATAPACK_TYPE_NOTICE and "cmd" in info["data"]:
                cmd = info["data"]["cmd"]
                if "DANMU_MSG" in cmd:
                    event_name = "DANMU_MSG"
                    info["data"]["cmd"] = "DANMU_MSG"
                else:
                    event_name = cmd
                self.dispatch(event_name, {"room_display_id": self.room_display_id, "room_real_id": self._real_id, "type": event_name, "data": info["data"]})
                self.dispatch("ALL", info)

    @staticmethod
    def _pack(data: bytes, protocol_version: int, datapack_type: int) -> bytes:
        body = bytearray()
        body += struct.pack(">H", 16)
        body += struct.pack(">H", protocol_version)
        body += struct.pack(">I", datapack_type)
        body += struct.pack(">I", 1)
        body += data
        body = struct.pack(">I", len(body) + 4) + body
        return bytes(body)

    @staticmethod
    def _iter_packets(content: bytes):
        offset = 0
        while offset + 16 <= len(content):
            inner = struct.unpack(">IHHII", content[offset : offset + 16])
            length = inner[0]
            if length <= 16 or offset + length > len(content):
                return
            yield inner, content[offset + 16 : offset + length]
            offset += length

    def _unpack(self, data: bytes) -> list:
        ret = []
        if len(data) < 16:
            return ret
        header = struct.unpack(">IHHII", data[:16])
        content = data
        if header[2] == self.PROTOCOL_VERSION_BROTLI_JSON:
            try:
                content = brotli.decompress(data[16:])
            except Exception:
                content = data[16:]

        if (
            header[2] == self.PROTOCOL_VERSION_HEARTBEAT
            and header[3] == self.DATAPACK_TYPE_HEARTBEAT_RESPONSE
        ):
            view = struct.unpack(">I", data[16:20])[0] if len(data) >= 20 else 0
            ret.append({"protocol_version": header[2], "datapack_type": header[3], "data": {"view": view}})
            return ret

        for inner, chunk in self._iter_packets(content):
            datapack_type = inner[3]
            recv = {"protocol_version": inner[2], "datapack_type": datapack_type, "data": None}
            if datapack_type == self.DATAPACK_TYPE_HEARTBEAT_RESPONSE:
                if len(chunk) < 4:
                    break
                recv["data"] = {"view": struct.unpack(">I", chunk[:4])[0]}
            elif datapack_type == self.DATAPACK_TYPE_VERIFY_SUCCESS_RESPONSE:
                recv["data"] = json.loads(chunk.decode("utf-8", errors="ignore"))
            else:
                try:
                    recv["data"] = json.loads(chunk.decode("utf-8", errors="ignore"))
                except Exception:
                    recv["data"] = None
            ret.append(recv)
        return ret

    async def disconnect(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        await self._cleanup()

    async def _cleanup(self) -> None:
        heartbeat_task = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        session = self._session
        self._session = None
        if session:
            try:
                await session.close()
            except Exception:
                pass
        connector = self._connector
        self._connector = None
        if connector:
            try:
                await connector.close()
            except Exception:
                pass
        self._ws = None
        self._status = 0
