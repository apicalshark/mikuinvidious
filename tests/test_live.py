# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import json
import struct
import unittest
from unittest.mock import AsyncMock, patch

import brotli
from miku.api.live import LiveDanmaku


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
        from miku.app import app

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

    async def test_video_listen_invalid_idx_400(self):
        resp = await self.client.get("/video_listen/BV1xx411c7mD:notanumber")
        self.assertEqual(resp.status_code, 400)
        resp_neg = await self.client.get("/video_listen/BV1xx411c7mD:-1")
        self.assertEqual(resp_neg.status_code, 400)

    async def test_audio_invalid_id_400(self):
        resp_toolong = await self.client.get("/audio/au12345678901234567890123")
        self.assertEqual(resp_toolong.status_code, 400)
        resp_alpha = await self.client.get("/audio/invalid")
        self.assertEqual(resp_alpha.status_code, 400)
        resp_unicode = await self.client.get("/audio/²")
        self.assertEqual(resp_unicode.status_code, 400)
        resp_unicode_au = await self.client.get("/audio/au²")
        self.assertEqual(resp_unicode_au.status_code, 400)

    async def test_audio_list_invalid_id_400(self):
        resp_toolong = await self.client.get("/audio_list/am12345678901234567890123")
        self.assertEqual(resp_toolong.status_code, 400)
        resp_alpha = await self.client.get("/audio_list/invalid")
        self.assertEqual(resp_alpha.status_code, 400)
        resp_badidx = await self.client.get("/audio_list/am123:notanumber")
        self.assertEqual(resp_badidx.status_code, 400)
        resp_unicode = await self.client.get("/audio_list/²")
        self.assertEqual(resp_unicode.status_code, 400)
        resp_unicode_am = await self.client.get("/audio_list/am²")
        self.assertEqual(resp_unicode_am.status_code, 400)


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
        from unittest.mock import AsyncMock, patch

        import miku.views.live as views_live

        _FakeLiveDanmaku.instances.clear()
        views_live.live_chat_slots.reset()
        self._lookup_patch = patch("miku.views.live._lookup_real_room_id", new=AsyncMock(return_value=None))
        self._lookup_patch.start()

    async def asyncTearDown(self):
        import miku.views.live as views_live

        self._lookup_patch.stop()
        views_live.live_chat_slots.reset()

    async def test_probe_disconnect_before_handshake_does_not_connect_upstream(self):
        import asyncio
        from unittest.mock import patch

        import miku.views.live as views_live
        from miku.app import app

        with (
            patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku),
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
                    # Advance generator past the greeting yield into the handshake sleep
                    next_task = asyncio.create_task(anext(it))
                    await asyncio.sleep(0.05)
                    next_task.cancel()
                    await asyncio.gather(next_task, return_exceptions=True)
                    # Exiting `async with` closes the SSE generator before the 0.3s handshake gate finishes
                await asyncio.sleep(0.05)

            self.assertEqual(len(_FakeLiveDanmaku.instances), 0)
            self.assertNotIn(4366430, views_live.live_chat_manager.rooms)

    async def test_sse_stream_delivers_messages_and_heartbeats_after_handshake(self):
        from unittest.mock import patch

        import miku.views.live as views_live
        from miku.app import app

        with (
            patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku),
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

        import miku.views.live as views_live

        manager = views_live.LiveChatManager(idle_grace=0.5)
        with patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku):
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

        import miku.views.live as views_live

        manager = views_live.LiveChatManager(idle_grace=0.5)
        with patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku):
            # Simulate 10 rapid connect/disconnect cycles from a scanner or flaky client
            for _ in range(10):
                q = await manager.subscribe(4366430)
                await asyncio.sleep(0.01)
                await manager.unsubscribe(4366430, q)

            # Only 1 upstream connection was ever created, and it was NOT disconnected
            self.assertEqual(len(_FakeLiveDanmaku.instances), 1)
            self.assertFalse(_FakeLiveDanmaku.instances[0].disconnected)

            await manager.shutdown_all()

    async def test_reconnect_before_idle_deadline_restarts_grace_period(self):
        import asyncio
        from unittest.mock import patch

        import miku.views.live as views_live

        manager = views_live.LiveChatManager(idle_grace=0.12)
        with patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku):
            q1 = await manager.subscribe(4366430)
            await asyncio.sleep(0.01)
            await manager.unsubscribe(4366430, q1)

            await asyncio.sleep(0.07)

            q2 = await manager.subscribe(4366430)
            await manager.unsubscribe(4366430, q2)

            await asyncio.sleep(0.07)
            self.assertEqual(len(_FakeLiveDanmaku.instances), 1)
            self.assertFalse(_FakeLiveDanmaku.instances[0].disconnected)
            self.assertIn(4366430, manager.rooms)

            await asyncio.sleep(0.09)
            self.assertTrue(_FakeLiveDanmaku.instances[0].disconnected)
            self.assertNotIn(4366430, manager.rooms)

            await manager.shutdown_all()

    async def test_canonical_room_id_aliases_share_single_multiplexer(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        import miku.views.live as views_live

        manager = views_live.LiveChatManager(idle_grace=0.5)
        with (
            patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku),
            patch("miku.views.live._lookup_real_room_id", new=AsyncMock(return_value=21144080)),
        ):
            # Subscribe via short display ID 100 and canonical ID 21144080
            q_short = await manager.subscribe(100)
            q_canon = await manager.subscribe(21144080)
            await asyncio.sleep(0.02)

            self.assertEqual(len(_FakeLiveDanmaku.instances), 1)
            self.assertIn(21144080, manager.rooms)
            self.assertNotIn(100, manager.rooms)

            await _FakeLiveDanmaku.instances[0].emit_danmu("Bob", "Shared alias")
            self.assertEqual(
                await asyncio.wait_for(q_short.get(), timeout=1.0), {"user": "Bob", "text": "Shared alias"}
            )
            self.assertEqual(
                await asyncio.wait_for(q_canon.get(), timeout=1.0), {"user": "Bob", "text": "Shared alias"}
            )

            # Unsubscribing via short alias removes from canonical room
            await manager.unsubscribe(100, q_short)
            self.assertEqual(len(manager.rooms[21144080].subscribers), 1)

            await manager.shutdown_all()

    async def test_canonical_lookup_timeout_falls_back_to_requested_id(self):
        import asyncio
        from unittest.mock import patch

        import miku.views.live as views_live

        async def slow_lookup(rid):
            await asyncio.sleep(10.0)
            return 99999

        manager = views_live.LiveChatManager(idle_grace=0.5)
        with (
            patch.object(views_live, "LIVE_CHAT_LOOKUP_TIMEOUT", 0.05),
            patch("miku.views.live._lookup_real_room_id", slow_lookup),
        ):
            resolved = await manager._resolve_room_id(12345)
            self.assertEqual(resolved, 12345)

    async def test_manager_admission_limit_caps_distinct_rooms_including_idle_grace(self):
        import asyncio
        from unittest.mock import patch

        import miku.views.live as views_live

        manager = views_live.LiveChatManager(idle_grace=0.5, max_rooms=2)
        with patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku):
            q1 = await manager.subscribe(1001)
            q2 = await manager.subscribe(1002)
            await asyncio.sleep(0.02)

            # Unsubscribe from 1001 so it is retained only in idle_grace
            await manager.unsubscribe(1001, q1)

            # Rotating to a 3rd room_id while 1001 is still in idle_grace is rejected
            with self.assertRaises(views_live.LiveChatCapacityError):
                await manager.subscribe(1003)

            # Subscribing to an existing room (active 1002 or idle-grace 1001) still succeeds
            q2_extra = await manager.subscribe(1002)
            q1_rejoin = await manager.subscribe(1001)
            self.assertIsNotNone(q2_extra)
            self.assertIsNotNone(q1_rejoin)

            await manager.unsubscribe(1002, q2)
            await manager.unsubscribe(1002, q2_extra)
            await manager.unsubscribe(1001, q1_rejoin)
            await manager.shutdown_all()

    async def test_idle_grace_expiry_disconnects_upstream_and_removes_room(self):
        import asyncio
        from unittest.mock import patch

        import miku.views.live as views_live

        manager = views_live.LiveChatManager(idle_grace=0.05)
        with patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku):
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

        import miku.views.live as views_live

        manager = views_live.LiveChatManager(idle_grace=0.5, queue_maxsize=3)
        with patch("miku.api.live.LiveDanmaku", _FakeLiveDanmaku):
            q = await manager.subscribe(4366430)
            room = manager.rooms[4366430]

            for i in range(5):
                room.broadcast({"user": "u", "text": f"msg-{i}"})

            self.assertEqual(q.qsize(), 3)
            items = [q.get_nowait()["text"] for _ in range(3)]
            self.assertEqual(items, ["msg-2", "msg-3", "msg-4"])

            await manager.shutdown_all()


class TestEndpointAntiAbuseHardening(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from miku.app import app

        self.client = app.test_client()

    async def test_mux_tracks_kills_ffmpeg_on_cancelled_error(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        import miku.dash.download_track as dash_download_track

        hang_event = asyncio.Event()

        class _FakeProc:
            returncode = None
            killed = False
            waited = False

            async def communicate(self):
                await hang_event.wait()
                return b"", b""

            def kill(self):
                self.killed = True
                self.returncode = -9
                hang_event.set()

            async def wait(self):
                self.waited = True
                return self.returncode

        fake_proc = _FakeProc()

        with (
            patch("miku.dash.download_track.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)),
        ):
            task = asyncio.create_task(dash_download_track._mux_tracks("/tmp/v.m4s", "/tmp/a.m4s", "/tmp/o.mp4"))
            await asyncio.sleep(0.03)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            self.assertTrue(fake_proc.killed, "ffmpeg subprocess must be killed when _mux_tracks is cancelled")
            self.assertTrue(fake_proc.waited, "ffmpeg subprocess must be awaited after kill")

    async def test_live_room_view_serves_from_cache_on_repeat_visit(self):
        from unittest.mock import AsyncMock, patch

        cached_payload = {
            "vinfo": {
                "title": "Cached Live Room",
                "desc": "Desc",
                "pic": "",
                "owner": {"name": "Streamer", "mid": 123, "face": ""},
                "stat": {"view": 100, "like": 0, "coin": 0, "favorite": 0, "share": 0},
                "pubdate": 0,
                "bvid": "4366430",
                "tid": 0,
                "tname": "Game",
            },
            "supported_src": [{"quality": 10000, "new_description": "Original", "url": "https://example.com/live.flv"}],
            "live_format": "flv",
        }
        mock_room_info = AsyncMock()
        with (
            patch("miku.views.live.cache_get", new=AsyncMock(return_value=cached_payload)),
            patch("miku.api.live.LiveRoom.get_room_info", mock_room_info),
            patch("miku.views.live.render_template_with_theme", new=AsyncMock(return_value="<html>Cached Live</html>")),
        ):
            resp = await self.client.get("/live/4366430")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("X-Cache"), "HIT")
            mock_room_info.assert_not_awaited()

    async def test_live_room_view_renders_when_redis_pipeline_fails(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        import miku.views.live as views_live

        fake_info_data = {
            "room_info": {
                "title": "Uncached Live Room",
                "description": "Desc",
                "cover": "",
                "area_name": "Game",
                "online": 50,
                "live_start_time": 0,
            },
            "anchor_info": {
                "base_info": {"uname": "Streamer", "face": ""},
            },
        }
        fake_play_data = {
            "play_url": {
                "stream": [
                    {
                        "protocol_name": "http_stream",
                        "format": [
                            {
                                "format_name": "flv",
                                "codec": [
                                    {
                                        "codec_name": "avc",
                                        "url": "https://example.com/live.flv",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "g_qn_desc": [{"qn": 10000, "desc": "Original"}],
            }
        }
        mock_pipe = MagicMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis pipeline failed"))

        with (
            patch("miku.views.live.cache_get", new=AsyncMock(return_value=None)),
            patch("miku.api.live.LiveRoom.get_room_info", new=AsyncMock(return_value=fake_info_data)),
            patch("miku.api.live.LiveRoom.get_room_play_info_v2", new=AsyncMock(return_value=fake_play_data)),
            patch.object(views_live.appredis, "pipeline", return_value=mock_pipe),
            patch.object(
                views_live.appredis, "setex", new=AsyncMock(side_effect=ConnectionError("Redis setex failed"))
            ),
            patch(
                "miku.views.live.render_template_with_theme", new=AsyncMock(return_value="<html>Live Rendered</html>")
            ),
        ):
            resp = await self.client.get("/live/4366430")
            self.assertEqual(resp.status_code, 200)
            text = await resp.get_data(as_text=True)
            self.assertEqual(text, "<html>Live Rendered</html>")

    async def test_concurrent_live_room_cache_misses_are_coalesced(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        import miku.views.live as views_live

        fake_info_data = {
            "room_info": {
                "title": "Concurrent Live Room",
                "description": "Desc",
                "cover": "",
                "area_name": "Game",
                "online": 50,
                "live_start_time": 0,
            },
            "anchor_info": {
                "base_info": {"uname": "Streamer", "face": ""},
            },
        }
        fake_play_data = {
            "play_url": {
                "stream": [
                    {
                        "protocol_name": "http_stream",
                        "format": [
                            {
                                "format_name": "flv",
                                "codec": [
                                    {
                                        "codec_name": "avc",
                                        "url": "https://example.com/live.flv",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "g_qn_desc": [{"qn": 10000, "desc": "Original"}],
            }
        }

        async def slow_get_room_info():
            await asyncio.sleep(0.05)
            return fake_info_data

        mock_get_room_info = AsyncMock(side_effect=slow_get_room_info)

        with (
            patch("miku.views.live.cache_get", new=AsyncMock(return_value=None)),
            patch("miku.api.live.LiveRoom.get_room_info", mock_get_room_info),
            patch("miku.api.live.LiveRoom.get_room_play_info_v2", new=AsyncMock(return_value=fake_play_data)),
            patch.object(views_live.appredis, "pipeline", return_value=MagicMock(execute=AsyncMock())),
            patch.object(views_live.appredis, "setex", new=AsyncMock()),
            patch("miku.views.live.cache_set", new=AsyncMock()),
            patch(
                "miku.views.live.render_template_with_theme", new=AsyncMock(return_value="<html>Live Coalesced</html>")
            ),
        ):
            # Fire 3 simultaneous requests for the same room
            responses = await asyncio.gather(
                self.client.get("/live/4366430"),
                self.client.get("/live/4366430"),
                self.client.get("/live/4366430"),
            )
            for resp in responses:
                self.assertEqual(resp.status_code, 200)
                text = await resp.get_data(as_text=True)
                self.assertEqual(text, "<html>Live Coalesced</html>")

            # Upstream get_room_info was called only once despite 3 concurrent requests
            self.assertEqual(mock_get_room_info.await_count, 1)
            # In-flight map is completely cleared after completion
            self.assertEqual(views_live._live_room_inflight, {})

    async def test_concurrent_live_room_cache_misses_cleared_on_failure(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        import miku.views.live as views_live

        async def failing_get_room_info():
            await asyncio.sleep(0.02)
            raise RuntimeError("Upstream room lookup failed")

        mock_get_room_info = AsyncMock(side_effect=failing_get_room_info)

        with (
            patch("miku.views.live.cache_get", new=AsyncMock(return_value=None)),
            patch("miku.api.live.LiveRoom.get_room_info", mock_get_room_info),
            patch(
                "miku.api.live.LiveRoom.get_room_base_info", new=AsyncMock(side_effect=RuntimeError("Fallback failed"))
            ),
            patch("miku.views.live.render_template_with_theme", new=AsyncMock(return_value="<html>Error</html>")),
        ):
            responses = await asyncio.gather(
                self.client.get("/live/4366430"),
                self.client.get("/live/4366430"),
            )
            for resp in responses:
                self.assertEqual(resp.status_code, 500)

            # In-flight map is cleared even on failure
            self.assertEqual(views_live._live_room_inflight, {})

    async def test_video_listen_view_reuses_cached_video_data(self):
        from unittest.mock import AsyncMock, patch

        import miku.views.video as views_video

        cached_payload = {
            "vinfo": {"bvid": "BV1xx411c7mD", "title": "Cached Audio Video"},
            "vtags": [{"tag_name": "music"}],
            "vrelated": [{"bvid": "BV1yy411c7mD", "title": "Related"}],
            "vset": [{"page": 1, "part": "Part 1"}],
        }
        mock_get_info = AsyncMock()
        with (
            patch("miku.views.video.cache_get", new=AsyncMock(return_value=cached_payload)),
            patch("miku.api.video.Video.get_info", mock_get_info),
            patch("miku.api.comment.get_comments", new=AsyncMock(return_value={"page": {"count": 0}, "replies": []})),
            patch.object(views_video.appredis, "exists", new=AsyncMock(return_value=1)),
            patch("miku.views.video.render_template_with_theme", new=AsyncMock(return_value="<html>Listen</html>")),
        ):
            resp = await self.client.get("/video_listen/BV1xx411c7mD")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("X-Cache"), "HIT")
            mock_get_info.assert_not_awaited()

    async def test_rate_limits_enforced_across_hardened_endpoints(self):
        from unittest.mock import AsyncMock, patch

        import miku.core.rate_limit as rate_limit
        from miku.core.url_signing import sign_danmaku_url
        from miku.state import appconf

        fake_limiter = AsyncMock()
        fake_limiter.is_allowed = AsyncMock(
            return_value=(False, {"remaining": 0, "reset": 9999999999, "limit": 10, "retry_after": 30})
        )

        with (
            patch.dict(appconf["rate_limit"], {"enabled": True}),
            patch.object(rate_limit, "get_rate_limiter", return_value=fake_limiter),
        ):
            for path in (
                "/live/4366430",
                "/live",
                sign_danmaku_url("BV1xx411c7mD", 0),
                "/video_listen/BV1xx411c7mD",
                "/space/12345",
                "/space/12345/json",
                "/author/12345",
                "/audio/au12345",
                "/vv/1",
                "/proxy/download/BV1xx411c7mD/0/80",
            ):
                with self.subTest(path=path):
                    r = await self.client.get(path)
                    self.assertEqual(r.status_code, 429, f"Expected 429 on rate-limited {path}")


class TestLiveStreamLoop(unittest.IsolatedAsyncioTestCase):
    async def test_early_return_runs_finally_cleanup(self):
        import httpx
        from miku.media.live_manager import LiveStream

        stream = LiveStream("https://d1--cn-gotcha01.bilivideo.com/live/1001.flv", {}, {})
        stream.is_running = True
        q: asyncio.Queue = asyncio.Queue()
        stream.clients["c1"] = q

        # Mock HTTP 404 responses so _stream_loop hits retry_count >= max_retries and returns early
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch("miku.media.live_manager.Network.get_async_client", AsyncMock(return_value=client)),
                patch("miku.media.live_manager.asyncio.sleep", AsyncMock(return_value=None)),
            ):
                await stream._stream_loop()
        finally:
            await client.aclose()

        self.assertFalse(stream.is_running)
        self.assertTrue(stream.header_ready.is_set())
        self.assertEqual(len(stream.clients), 0)
        self.assertIsNone(q.get_nowait())

    async def test_redirect_cookies_follow_origin_boundaries(self):
        from miku.media.live_manager import LiveStream

        for target in ("https://other.bilivideo.com/x", "http://a.bilivideo.com/x", "https://a.bilivideo.com:444/x"):
            with self.subTest(target=target):
                stream = LiveStream("https://a.bilivideo.com/start", {"cookie": "extra=1"}, {"SESSDATA": "test"})
                stream.is_running = True
                stream.clients["test"] = asyncio.Queue()
                seen = []
                locations = iter(("https://a.bilivideo.com:443/same", target, "https://a.bilivideo.com/back", "/end"))

                class Response:
                    status_code = 302

                    def __init__(self, location):
                        self.headers = {"location": location}

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *args):
                        pass

                class Client:
                    def stream(self, method, url, headers, seen=seen, locations=locations, **kwargs):
                        seen.append(dict(headers))
                        return Response(next(locations))

                with (
                    patch("miku.media.live_manager.Network.get_async_client", AsyncMock(return_value=Client())),
                    patch("miku.media.proxy.is_safe_proxy_url", AsyncMock(return_value=True)),
                ):
                    await stream._stream_loop()
                self.assertIn("Cookie", seen[0])
                self.assertIn("Cookie", seen[1])
                for headers in seen[2:]:
                    self.assertFalse(any(k.lower() == "cookie" for k in headers))

    async def test_redirect_blocks_unsafe_hop_and_caps_hops(self):
        import miku.media.proxy as media_proxy
        from miku.media.live_manager import MAX_LIVE_REDIRECTS, LiveStream

        # 1. Unsafe redirect target -> status 403 and stream stops
        stream1 = LiveStream("https://d1--cn-gotcha01.bilivideo.com/live/1001.flv", {}, {})
        stream1.is_running = True
        q1: asyncio.Queue = asyncio.Queue()
        stream1.clients["c1"] = q1

        seen_follow_redirects = []

        class _FakeStreamResp:
            def __init__(self, status_code, headers):
                self.status_code = status_code
                self.headers = headers

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class _FakeClient:
            def __init__(self, location):
                self.location = location

            def stream(self, method, url, headers=None, follow_redirects=True):
                seen_follow_redirects.append(follow_redirects)
                return _FakeStreamResp(302, {"location": self.location})

        unsafe_checks = []

        async def fake_is_safe(url):
            unsafe_checks.append(url)
            return False

        with (
            patch(
                "miku.media.live_manager.Network.get_async_client",
                AsyncMock(return_value=_FakeClient("http://127.0.0.1/internal")),
            ),
            patch.object(media_proxy, "is_safe_proxy_url", side_effect=fake_is_safe),
        ):
            await stream1._stream_loop()

        self.assertEqual(seen_follow_redirects, [False])
        self.assertEqual(unsafe_checks, ["http://127.0.0.1/internal"])
        self.assertEqual(stream1.status_code, 403)
        self.assertFalse(stream1.is_running)
        self.assertTrue(stream1.header_ready.is_set())
        self.assertIsNone(q1.get_nowait())

        # 2. Safe redirect chain exceeding MAX_LIVE_REDIRECTS -> status 502
        stream2 = LiveStream("https://d1--cn-gotcha01.bilivideo.com/live/1002.flv", {}, {})
        stream2.is_running = True
        q2: asyncio.Queue = asyncio.Queue()
        stream2.clients["c2"] = q2
        hop_count = 0

        class _LoopRedirectClient:
            def stream(self, method, url, headers=None, follow_redirects=True):
                nonlocal hop_count
                hop_count += 1
                return _FakeStreamResp(
                    302, {"location": f"https://d1--cn-gotcha01.bilivideo.com/live/hop{hop_count}.flv"}
                )

        with (
            patch(
                "miku.media.live_manager.Network.get_async_client",
                AsyncMock(return_value=_LoopRedirectClient()),
            ),
            patch.object(media_proxy, "is_safe_proxy_url", AsyncMock(return_value=True)),
        ):
            await stream2._stream_loop()

        self.assertEqual(hop_count, MAX_LIVE_REDIRECTS + 1)
        self.assertEqual(stream2.status_code, 502)
        self.assertFalse(stream2.is_running)
        self.assertEqual(len(stream2.clients), 0)
        self.assertIsNone(q2.get_nowait())


class TestLiveHlsProxy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from miku.app import app

        self.client = app.test_client()

    def test_is_live_hls_url_classification(self):
        from miku.media.proxy import _is_live_hls_url

        self.assertTrue(_is_live_hls_url("https://d1--ov-gotcha207.bilivideo.com/live-bvc/123/index.m3u8?sign=abc"))
        self.assertTrue(
            _is_live_hls_url("https://api.live.bilibili.com/xlive/play-interface/v1/index/getMasterUrl?room_id=1")
        )
        self.assertTrue(_is_live_hls_url("https://d1--ov-gotcha207.bilivideo.com/live-bvc/123/master?expires=123"))
        self.assertFalse(_is_live_hls_url("https://d1--ov-gotcha207.bilivideo.com/live-bvc/123/live_123.flv?sign=abc"))
        self.assertFalse(_is_live_hls_url("https://d1--ov-gotcha207.bilivideo.com/live-bvc/master/live_123.flv"))

    async def test_live_hls_master_url_bypasses_flv_live_manager(self):
        import miku.media.proxy as media_proxy
        from miku.core.url_signing import sign_live_hls_url
        from miku.media.stream import CdnResponse

        cdn_playlist_url = "https://d1--ov-gotcha207.bilivideo.com/a/index.m3u8"
        manifest = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n{cdn_playlist_url}\n".encode()
        expected = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n{sign_live_hls_url(cdn_playlist_url)}\n".encode()

        class _FakeConn:
            async def iter_chunks(self):
                yield manifest

            async def close(self):
                pass

        resp_headers = CdnResponse()
        resp_headers.status_code = 200
        resp_headers.headers = {
            "content-type": "application/vnd.apple.mpegurl",
            "cache-control": "max-age=300",
        }

        master_url = "https://api.live.bilibili.com/xlive/play-interface/v1/index/getMasterUrl?cid=22747736"
        live_sub_mock = AsyncMock()

        with (
            patch.object(media_proxy.appredis, "get", new=AsyncMock(return_value=master_url.encode())),
            patch.object(media_proxy, "is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch.object(media_proxy.TicketManager, "get_ticket", new=AsyncMock(return_value="t")),
            patch.object(media_proxy.live_manager, "subscribe", live_sub_mock),
            patch.object(
                media_proxy,
                "_establish_cdn_connection",
                new=AsyncMock(return_value=(_FakeConn(), resp_headers)),
            ),
        ):
            resp = await self.client.get("/proxy/live/22747736_10000")
            body = await resp.get_data()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body, expected)
        self.assertEqual(resp.headers.get("Content-Type"), "application/vnd.apple.mpegurl")
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertIsNone(resp.headers.get("X-Miku-Proxy"))
        live_sub_mock.assert_not_awaited()

    async def test_live_hls_master_redirect_targets_are_validated(self):
        import miku.media.proxy as media_proxy
        from miku.media.stream import CdnResponse

        master_url = "https://api.live.bilibili.com/xlive/play-interface/v1/index/getMasterUrl?cid=22747736"

        class _FakePlaylistConn:
            def __init__(self):
                self.close = AsyncMock()

            async def iter_chunks(self):
                yield b"#EXTM3U\n#EXTINF:1.0,\nseg1.m4s\n"

        cases = [
            ("https://d1.bilivideo.com/index.m3u8", True, 200),
            ("/index.m3u8", True, 200),
            ("https://127.0.0.1/index.m3u8", False, 403),
            ("https://evil.example/index.m3u8", False, 403),
            ("https://[malformed", False, 403),
            (None, False, 502),
        ]
        for status in (301, 302, 303, 307, 308):
            for location, safe, expected_status in cases:
                with self.subTest(status=status, location=location):
                    conn = AsyncMock()
                    upstream = CdnResponse()
                    upstream.status_code = status
                    upstream.headers = {"location": location} if location is not None else {}

                    final_conn = _FakePlaylistConn()
                    final_headers = CdnResponse()
                    final_headers.status_code = 200
                    final_headers.headers = {"content-type": "application/vnd.apple.mpegurl"}

                    guard = AsyncMock(side_effect=[True, safe, safe])
                    with (
                        patch.object(media_proxy.appredis, "get", new=AsyncMock(return_value=master_url.encode())),
                        patch.object(media_proxy, "is_safe_proxy_url", guard),
                        patch.object(media_proxy.TicketManager, "get_ticket", new=AsyncMock(return_value="t")),
                        patch.object(media_proxy.live_manager, "subscribe", new=AsyncMock()) as subscribe,
                        patch.object(
                            media_proxy,
                            "_establish_cdn_connection",
                            new=AsyncMock(return_value=(conn, upstream)),
                        ),
                        patch.object(
                            media_proxy,
                            "_cdn_handshake",
                            new=AsyncMock(return_value=(final_conn, final_headers)),
                        ),
                    ):
                        resp = await self.client.get("/proxy/live/22747736_10000")
                        body = await resp.get_data(as_text=True)
                    self.assertEqual(resp.status_code, expected_status)
                    self.assertIsNone(resp.headers.get("Location"))
                    if safe:
                        target = (
                            location if location.startswith("https://") else "https://api.live.bilibili.com/index.m3u8"
                        )
                        self.assertIn(target, [call.args[0] for call in guard.await_args_list])
                        self.assertIn("/proxy/live_hls?sig=", body)
                        final_conn.close.assert_awaited_once()
                    conn.close.assert_awaited_once()
                    subscribe.assert_not_awaited()


class TestLiveRoomBaseInfo(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_fake_api(payload):
        from unittest.mock import AsyncMock

        class _FakeApi:
            def __init__(self, **_kwargs):
                pass

            def update_params(self, **_params):
                return self

            @property
            def result(self):
                return AsyncMock(return_value=payload)()

        return _FakeApi

    async def test_short_room_id_falls_back_to_sole_by_room_ids_entry(self):
        from unittest.mock import patch

        import miku.api.live as api_live

        expected = {"room_id": 5440, "short_id": 6, "title": "Live Short Room"}

        with patch.object(api_live, "Api", self._make_fake_api({"by_room_ids": {"5440": expected}})):
            room = await api_live.LiveRoom(6).get_room_base_info()

        self.assertEqual(room, expected)

    async def test_ambiguous_or_empty_by_room_ids_raises(self):
        from unittest.mock import patch

        import miku.api.live as api_live
        from miku.api.exceptions import ResponseCodeException

        for payload in (
            {"by_room_ids": {}},
            {"by_room_ids": {"1001": {"room_id": 1001}, "1002": {"room_id": 1002}}},
        ):
            with self.subTest(payload=payload), patch.object(api_live, "Api", self._make_fake_api(payload)):
                with self.assertRaises(ResponseCodeException):
                    await api_live.LiveRoom(6).get_room_base_info()


if __name__ == "__main__":
    unittest.main()
