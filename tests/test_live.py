import json
import struct
import unittest

import brotli
from api.live import LiveDanmaku


class TestLive(unittest.TestCase):
    def setUp(self):
        self.ld = LiveDanmaku(123456)

    def test_unpack_heartbeat_response(self):
        # Header: len=20, hlen=16, protover=1, type=3, seq=1; view=12345
        header = struct.pack(">IHHII", 20, 16, 1, 3, 1)
        body = struct.pack(">I", 12345)
        raw = header + body

        unpacked = self.ld._unpack(raw)
        self.assertEqual(len(unpacked), 1)
        self.assertEqual(unpacked[0]["protocol_version"], 1)
        self.assertEqual(unpacked[0]["datapack_type"], 3)
        self.assertEqual(unpacked[0]["data"], {"view": 12345})

    def test_unpack_verify_success_response(self):
        payload = json.dumps({"code": 0, "msg": "ok"}).encode("utf-8")
        packet = struct.pack(">IHHII", 16 + len(payload), 16, 0, 8, 1) + payload

        unpacked = self.ld._unpack(packet)
        self.assertEqual(len(unpacked), 1)
        self.assertEqual(unpacked[0]["protocol_version"], 0)
        self.assertEqual(unpacked[0]["datapack_type"], 8)
        self.assertEqual(unpacked[0]["data"], {"code": 0, "msg": "ok"})

    def test_unpack_brotli_notice_packets(self):
        notice1 = json.dumps({"cmd": "DANMU_MSG", "info": ["hello"]}).encode("utf-8")
        pkt1 = struct.pack(">IHHII", 16 + len(notice1), 16, 0, 5, 1) + notice1
        notice2 = json.dumps({"cmd": "DANMU_MSG", "info": ["world"]}).encode("utf-8")
        pkt2 = struct.pack(">IHHII", 16 + len(notice2), 16, 0, 5, 1) + notice2

        compressed = brotli.compress(pkt1 + pkt2)
        outer = struct.pack(">IHHII", 16 + len(compressed), 16, 3, 5, 1) + compressed

        unpacked = self.ld._unpack(outer)
        self.assertEqual(len(unpacked), 2)
        self.assertEqual(unpacked[0]["data"]["cmd"], "DANMU_MSG")
        self.assertEqual(unpacked[0]["data"]["info"], ["hello"])
        self.assertEqual(unpacked[1]["data"]["cmd"], "DANMU_MSG")
        self.assertEqual(unpacked[1]["data"]["info"], ["world"])

    def test_unpack_truncated_or_corrupt(self):
        # Too short (< 16 bytes)
        self.assertEqual(self.ld._unpack(b"short"), [])
        # Corrupt brotli data
        bad_brotli = struct.pack(">IHHII", 30, 16, 3, 5, 1) + b"not_brotli_data!"
        # Should not raise exception
        res = self.ld._unpack(bad_brotli)
        self.assertIsInstance(res, list)


class TestLiveRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app import app

        self.client = app.test_client()

    async def test_live_room_non_numeric_404(self):
        resp = await self.client.get("/live/.env")
        self.assertEqual(resp.status_code, 404)

    async def test_live_room_arbitrary_string_404(self):
        resp = await self.client.get("/live/config.php")
        self.assertEqual(resp.status_code, 404)

    async def test_live_room_negative_int_404(self):
        resp = await self.client.get("/live/-1")
        self.assertEqual(resp.status_code, 404)


class _FakeLiveDanmaku:
    instances = []

    def __init__(self, room_display_id, credential=None, **kwargs):
        self.room_display_id = room_display_id
        self.credential = credential
        self.handlers = {}
        self.connected = False
        self.disconnected = False
        self._stop = None
        _FakeLiveDanmaku.instances.append(self)

    def on(self, event_name: str):
        def decorator(func):
            self.handlers[event_name.upper()] = func
            return func

        return decorator

    async def connect(self):
        import asyncio

        self.connected = True
        self._stop = asyncio.Event()
        await self._stop.wait()

    async def disconnect(self):
        self.disconnected = True
        if self._stop is not None:
            self._stop.set()

    async def emit_danmu(self, user: str, text: str):
        handler = self.handlers.get("DANMU_MSG")
        if handler:
            await handler({"data": {"info": [[], text, [0, user]]}})


class TestLiveChatMultiplexer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _FakeLiveDanmaku.instances.clear()

    async def test_probe_disconnect_before_handshake_does_not_connect_upstream(self):
        import asyncio
        from unittest.mock import patch

        import views_live
        from app import app

        with (
            patch("api.live.LiveDanmaku", _FakeLiveDanmaku),
            patch.object(views_live, "LIVE_CHAT_HANDSHAKE_DELAY", 0.3),
        ):
            async with app.test_request_context("/live/chat/4366430"):
                resp = await views_live.live_chat_sse(4366430)
                async with resp.response as body:
                    it = aiter(body)
                    first_chunk = await anext(it)
                    if isinstance(first_chunk, bytes):
                        first_chunk = first_chunk.decode("utf-8")
                    self.assertIn("Chat connected", first_chunk)
                    # Exiting `async with` abruptly closes the SSE generator before the 0.3s handshake gate finishes
                await asyncio.sleep(0.05)

            self.assertEqual(len(_FakeLiveDanmaku.instances), 0)
            self.assertNotIn(4366430, views_live.live_chat_manager.rooms)

    async def test_sse_stream_delivers_messages_and_heartbeats_after_handshake(self):
        from unittest.mock import patch

        import views_live
        from app import app

        with (
            patch("api.live.LiveDanmaku", _FakeLiveDanmaku),
            patch.object(views_live, "LIVE_CHAT_HANDSHAKE_DELAY", 0.02),
            patch.object(views_live, "LIVE_CHAT_HEARTBEAT_INTERVAL", 0.05),
        ):
            async with app.test_request_context("/live/chat/4366430"):
                resp = await views_live.live_chat_sse(4366430)
                async with resp.response as body:
                    it = aiter(body)
                    greeting = await anext(it)
                    if isinstance(greeting, bytes):
                        greeting = greeting.decode("utf-8")
                    self.assertIn("Chat connected", greeting)

                    # Wait for heartbeat (no danmaku sent yet)
                    hb = await anext(it)
                    if isinstance(hb, bytes):
                        hb = hb.decode("utf-8")
                    self.assertEqual(hb, ": heartbeat\n\n")

                    # Now emit a danmaku message and read it from the SSE stream
                    self.assertEqual(len(_FakeLiveDanmaku.instances), 1)
                    await _FakeLiveDanmaku.instances[0].emit_danmu("Tester", "Works!")
                    msg_chunk = await anext(it)
                    if isinstance(msg_chunk, bytes):
                        msg_chunk = msg_chunk.decode("utf-8")
                    self.assertIn("Tester", msg_chunk)
                    self.assertIn("Works!", msg_chunk)

            await views_live.live_chat_manager.shutdown_all()

    async def test_multiple_clients_share_single_upstream_danmaku_and_receive_broadcast(self):
        import asyncio
        from unittest.mock import patch

        import views_live

        manager = views_live.LiveChatManager(idle_grace=0.5)
        with patch("api.live.LiveDanmaku", _FakeLiveDanmaku):
            q1 = await manager.subscribe(4366430)
            q2 = await manager.subscribe(4366430)
            await asyncio.sleep(0.05)

            self.assertEqual(len(_FakeLiveDanmaku.instances), 1)
            dm = _FakeLiveDanmaku.instances[0]
            self.assertTrue(dm.connected)

            await dm.emit_danmu("Miku", "Hello everyone")
            msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
            msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)

            self.assertEqual(msg1, {"user": "Miku", "text": "Hello everyone"})
            self.assertEqual(msg2, {"user": "Miku", "text": "Hello everyone"})

            await manager.shutdown_all()

    async def test_client_disconnect_within_grace_keeps_upstream_alive(self):
        import asyncio
        from unittest.mock import patch

        import views_live

        manager = views_live.LiveChatManager(idle_grace=0.5)
        with patch("api.live.LiveDanmaku", _FakeLiveDanmaku):
            # Simulate 10 rapid connect/disconnect cycles from a scanner or flaky client
            for _ in range(10):
                q = await manager.subscribe(4366430)
                await asyncio.sleep(0.01)
                await manager.unsubscribe(4366430, q)

            # Only 1 upstream connection was ever created, and it was NOT disconnected
            self.assertEqual(len(_FakeLiveDanmaku.instances), 1)
            self.assertFalse(_FakeLiveDanmaku.instances[0].disconnected)

            await manager.shutdown_all()

    async def test_idle_grace_expiry_disconnects_upstream_and_removes_room(self):
        import asyncio
        from unittest.mock import patch

        import views_live

        manager = views_live.LiveChatManager(idle_grace=0.05)
        with patch("api.live.LiveDanmaku", _FakeLiveDanmaku):
            q = await manager.subscribe(4366430)
            await asyncio.sleep(0.02)
            self.assertEqual(len(_FakeLiveDanmaku.instances), 1)

            await manager.unsubscribe(4366430, q)
            # Wait for the 0.05s idle grace period to expire
            await asyncio.sleep(0.12)

            self.assertTrue(_FakeLiveDanmaku.instances[0].disconnected)
            self.assertNotIn(4366430, manager.rooms)

    async def test_bounded_subscriber_queue_drops_oldest_on_overflow(self):
        from unittest.mock import patch

        import views_live

        manager = views_live.LiveChatManager(idle_grace=0.5, queue_maxsize=3)
        with patch("api.live.LiveDanmaku", _FakeLiveDanmaku):
            q = await manager.subscribe(4366430)
            room = manager.rooms[4366430]

            for i in range(5):
                room.broadcast({"user": "u", "text": f"msg-{i}"})

            self.assertEqual(q.qsize(), 3)
            items = [q.get_nowait()["text"] for _ in range(3)]
            self.assertEqual(items, ["msg-2", "msg-3", "msg-4"])

            await manager.shutdown_all()


if __name__ == "__main__":
    unittest.main()
