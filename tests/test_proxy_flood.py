"""Per-IP anti-flood hardening for the media proxies and scrape-amplifying APIs.

Principle under test: isolation is strictly per client IP. There is no global
ceiling, so a botnet spread over many IPs can only exhaust its own allowance,
never the allowance of unrelated users.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

IP_A = "203.0.113.9"
IP_B = "198.51.100.7"

_trust_patch = None


def setUpModule():
    # Quart test contexts carry no usable peer address; trust the test's
    # X-Real-IP header so per-IP behaviour can be exercised with two IPs.
    global _trust_patch
    import rate_limit

    _trust_patch = patch.object(rate_limit, "_peer_is_trusted_proxy", return_value=True)
    _trust_patch.start()


def tearDownModule():
    _trust_patch.stop()


class TestPerIpSlotLimiter(unittest.IsolatedAsyncioTestCase):
    def test_caps_per_ip_and_isolates_ips(self):
        from ip_slots import PerIpSlotLimiter

        lim = PerIpSlotLimiter(max_per_ip=2)
        a1 = lim.try_acquire(IP_A)
        a2 = lim.try_acquire(IP_A)
        self.assertIsNotNone(a1)
        self.assertIsNotNone(a2)
        self.assertIsNone(lim.try_acquire(IP_A))
        self.assertIsNotNone(lim.try_acquire(IP_B))

        a1.release()
        a1.release()  # idempotent: must not free a second slot
        self.assertEqual(lim.active(IP_A), 1)
        self.assertIsNotNone(lim.try_acquire(IP_A))
        self.assertIsNone(lim.try_acquire(IP_A))

    def test_no_global_ceiling(self):
        from ip_slots import PerIpSlotLimiter

        lim = PerIpSlotLimiter(max_per_ip=1)
        slots = [lim.try_acquire(f"10.{i // 256}.{i % 256}.1") for i in range(5000)]
        self.assertTrue(all(s is not None for s in slots))
        for s in slots:
            s.release()
        self.assertEqual(lim.active_by_ip, {})

    def test_dash_limiter_has_no_global_ceiling(self):
        import dash_stream

        lim = dash_stream._DashConcurrencyLimiter(max_per_ip=1)
        self.assertFalse(hasattr(lim, "max_global"))
        slots = [lim.try_acquire(f"10.0.{i // 256}.{i % 256}") for i in range(500)]
        self.assertTrue(all(s is not None for s in slots))

    async def test_live_chat_has_no_default_global_ceiling_and_caps_per_ip(self):
        import views_live
        from app import app

        views_live.live_chat_slots.reset()
        try:
            self.assertIsNone(views_live.live_chat_manager.max_rooms)
            with patch.object(views_live.live_chat_slots, "max_per_ip", 2):
                async with app.test_request_context("/live/chat/1001", headers={"X-Real-IP": IP_A}):
                    r1 = await views_live.live_chat_sse(1001)
                    self.assertEqual(r1.status_code, 200)
                async with app.test_request_context("/live/chat/1002", headers={"X-Real-IP": IP_A}):
                    r2 = await views_live.live_chat_sse(1002)
                    self.assertEqual(r2.status_code, 200)
                async with app.test_request_context("/live/chat/1003", headers={"X-Real-IP": IP_A}):
                    r3 = await views_live.live_chat_sse(1003)
                    self.assertEqual(r3.status_code, 429)
                # Another IP is unaffected
                async with app.test_request_context("/live/chat/1004", headers={"X-Real-IP": IP_B}):
                    r_other = await views_live.live_chat_sse(1004)
                    self.assertEqual(r_other.status_code, 200)
                for r in (r1, r2, r_other):
                    async with r.response as b:
                        it = aiter(b)
                        _ = await anext(it)
                self.assertEqual(views_live.live_chat_slots.active_by_ip, {})
        finally:
            views_live.live_chat_slots.reset()


class _GatedConn:
    """Fake CdnConnection whose body blocks until ``gate`` is set."""

    def __init__(self, gate: asyncio.Event, body: bytes = b"x" * 10):
        self.gate = gate
        self.body = body
        self.closed = False

    async def iter_chunks(self):
        await self.gate.wait()
        yield self.body

    async def close(self):
        self.closed = True


def _resp_headers(status=206):
    rh = MagicMock()
    rh.status_code = status
    rh.headers = {"content-type": "video/mp4", "content-length": "10", "content-range": "bytes 0-9/100"}
    return rh


class TestProgressiveProxyFlood(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import proxy

        proxy.direct_stream_slots.reset()
        proxy.live_stream_slots.reset()

    def tearDown(self):
        import proxy

        proxy.direct_stream_slots.reset()
        proxy.live_stream_slots.reset()

    async def test_rejects_multi_range_before_touching_redis(self):
        import proxy
        from app import app

        get_mock = AsyncMock(return_value=b"https://upos-sz-mirrorcos.bilivideo.com/x.mp4")
        for rng in ("bytes=0-10,20-30", "bytes=0-1,0-1,0-1,0-1", "bytes=abc", "items=0-10", "bytes=-"):
            with patch.object(proxy.appredis, "get", get_mock):
                async with app.test_request_context(
                    "/proxy/video/BV1xx411c7xx_0_80.mp4", headers={"Range": rng, "X-Real-IP": IP_A}
                ):
                    resp = await proxy.proxy_main("video/BV1xx411c7xx_0_80.mp4")
            self.assertEqual(resp.status_code, 416, rng)
        get_mock.assert_not_awaited()

    async def test_accepts_open_ended_and_suffix_ranges(self):
        """Native <video> opens with ``bytes=0-`` and resumes with ``bytes=N-``."""
        import proxy

        for rng in ("bytes=0-", "bytes=12345-", "bytes=0-99", "bytes=-500", ""):
            self.assertTrue(proxy._is_acceptable_progressive_range(rng), rng)

    async def test_rejects_malformed_video_path(self):
        import proxy
        from app import app

        get_mock = AsyncMock(return_value=None)
        for sub in ("video/..%2f_0_80", "video/BV1xx411c7xx_x_80", "video/BV1xx411c7xx_0_*", "video/a_b"):
            with patch.object(proxy.appredis, "get", get_mock):
                async with app.test_request_context(f"/proxy/{sub}", headers={"X-Real-IP": IP_A}):
                    resp = await proxy.proxy_main(sub)
            self.assertEqual(resp.status_code, 400, sub)
        get_mock.assert_not_awaited()

    async def test_per_ip_concurrent_stream_cap(self):
        import proxy
        from app import app

        gate = asyncio.Event()
        conns = []

        async def fake_establish(url, headers, proxy_url):
            c = _GatedConn(gate)
            conns.append(c)
            return c, _resp_headers()

        async def call(ip):
            async with app.test_request_context(
                "/proxy/video/BV1xx411c7xx_0_80.mp4", headers={"Range": "bytes=0-", "X-Real-IP": ip}
            ):
                return await proxy.proxy_main("video/BV1xx411c7xx_0_80.mp4")

        with (
            patch.object(
                proxy.appredis, "get", AsyncMock(return_value=b"https://upos-sz-mirrorcos.bilivideo.com/x.mp4")
            ),
            patch.object(proxy, "is_safe_proxy_url", AsyncMock(return_value=True)),
            patch.object(proxy.TicketManager, "get_ticket", AsyncMock(return_value=None)),
            patch.object(proxy, "_establish_cdn_connection", fake_establish),
            patch.object(proxy.direct_stream_slots, "max_per_ip", 2),
        ):
            r1 = await call(IP_A)
            r2 = await call(IP_A)
            r3 = await call(IP_A)
            self.assertEqual(r1.status_code, 206)
            self.assertEqual(r2.status_code, 206)
            self.assertEqual(r3.status_code, 429)
            self.assertEqual(r3.headers.get("Retry-After"), "1")
            self.assertEqual(len(conns), 2, "rejected request must not open an upstream socket")

            r_other = await call(IP_B)
            self.assertEqual(r_other.status_code, 206)

            gate.set()
            async with r1.response as body:
                data = b"".join([c async for c in aiter(body)])
            self.assertEqual(data, b"x" * 10)
            self.assertEqual(proxy.direct_stream_slots.active(IP_A), 1)

            r4 = await call(IP_A)
            self.assertEqual(r4.status_code, 206)
            for r in (r2, r_other, r4):
                async with r.response as b:
                    _ = [c async for c in aiter(b)]
        self.assertEqual(proxy.direct_stream_slots.active_by_ip, {})
        self.assertTrue(all(c.closed for c in conns))

    async def test_upstream_failure_releases_slot(self):
        import proxy
        from app import app

        with (
            patch.object(
                proxy.appredis, "get", AsyncMock(return_value=b"https://upos-sz-mirrorcos.bilivideo.com/x.mp4")
            ),
            patch.object(proxy, "is_safe_proxy_url", AsyncMock(return_value=True)),
            patch.object(proxy.TicketManager, "get_ticket", AsyncMock(return_value=None)),
            patch.object(proxy, "_establish_cdn_connection", AsyncMock(return_value=(None, None))),
            patch.object(proxy, "_try_backup_url", AsyncMock(return_value=(None, None))),
        ):
            async with app.test_request_context("/proxy/video/BV1xx411c7xx_0_80.mp4", headers={"X-Real-IP": IP_A}):
                resp = await proxy.proxy_main("video/BV1xx411c7xx_0_80.mp4")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(proxy.direct_stream_slots.active_by_ip, {})


class _FakeLiveStream:
    def __init__(self):
        self.status_code = 200
        self.header_ready = asyncio.Event()
        self.header_ready.set()
        self.removed = []

    def remove_client(self, client_id, reason=""):
        self.removed.append(client_id)


class TestLiveProxyFlood(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import proxy

        proxy.direct_stream_slots.reset()
        proxy.live_stream_slots.reset()

    def tearDown(self):
        import proxy

        proxy.direct_stream_slots.reset()
        proxy.live_stream_slots.reset()

    async def test_rejects_glob_room_ids_without_keys_scan(self):
        """User input used to reach ``appredis.keys(f"miku_live_{room_id}_*")``."""
        import proxy
        from app import app

        keys_mock = AsyncMock(return_value=[])
        get_mock = AsyncMock(return_value=None)
        for sub in ("live/*", "live/*_default", "live/1?_*", "live/[0-9]", "live/123_abc"):
            with patch.object(proxy.appredis, "keys", keys_mock), patch.object(proxy.appredis, "get", get_mock):
                async with app.test_request_context(f"/proxy/{sub}", headers={"X-Real-IP": IP_A}):
                    resp = await proxy.proxy_main(sub)
            self.assertEqual(resp.status_code, 400, sub)
        keys_mock.assert_not_awaited()
        get_mock.assert_not_awaited()

    async def test_default_quality_fallback_never_uses_keys(self):
        import proxy
        from app import app

        async def fake_scan(cursor=0, match=None, count=None):
            self.assertEqual(match, "miku_live_12345_*")
            return 0, [b"miku_live_12345_10000"]

        keys_mock = AsyncMock(return_value=[])
        get_mock = AsyncMock(side_effect=[None, None])
        with (
            patch.object(proxy.appredis, "keys", keys_mock),
            patch.object(proxy.appredis, "get", get_mock),
            patch.object(proxy.appredis, "scan", fake_scan),
        ):
            async with app.test_request_context("/proxy/live/12345", headers={"X-Real-IP": IP_A}):
                resp = await proxy.proxy_main("live/12345")
        keys_mock.assert_not_awaited()
        self.assertEqual([c.args[0] for c in get_mock.await_args_list], ["miku_live_12345", b"miku_live_12345_10000"])
        self.assertEqual(resp.status_code, 404)

    async def test_per_ip_live_subscription_cap(self):
        import proxy
        from app import app

        streams = []

        async def fake_subscribe(url, headers, cookies, client_id):
            s = _FakeLiveStream()
            streams.append(s)
            q = asyncio.Queue()
            q.put_nowait(b"FLV")
            q.put_nowait(None)
            return s, q

        sub_mock = AsyncMock(side_effect=fake_subscribe)

        async def call(ip, room):
            async with app.test_request_context(f"/proxy/live/{room}_10000", headers={"X-Real-IP": ip}):
                return await proxy.proxy_main(f"live/{room}_10000")

        with (
            patch.object(
                proxy.appredis, "get", AsyncMock(return_value=b"https://d1--cn-gotcha.bilivideo.com/live.flv")
            ),
            patch.object(proxy, "is_safe_proxy_url", AsyncMock(return_value=True)),
            patch.object(proxy.TicketManager, "get_ticket", AsyncMock(return_value=None)),
            patch.object(proxy.live_manager, "subscribe", sub_mock),
            patch.object(proxy.live_stream_slots, "max_per_ip", 2),
        ):
            r1 = await call(IP_A, 1)
            r2 = await call(IP_A, 2)
            r3 = await call(IP_A, 3)
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r3.status_code, 429)
            self.assertEqual(sub_mock.await_count, 2, "rejected request must not start an upstream stream")

            r_other = await call(IP_B, 3)
            self.assertEqual(r_other.status_code, 200)

            async with r1.response as body:
                data = b"".join([c async for c in aiter(body)])
            self.assertEqual(data, b"FLV")
            self.assertEqual(proxy.live_stream_slots.active(IP_A), 1)
            for r in (r2, r_other):
                async with r.response as b:
                    _ = [c async for c in aiter(b)]
        self.assertEqual(proxy.live_stream_slots.active_by_ip, {})
        self.assertTrue(all(s.removed for s in streams))

    async def test_failed_subscribe_releases_slot(self):
        import proxy
        from app import app

        with (
            patch.object(
                proxy.appredis, "get", AsyncMock(return_value=b"https://d1--cn-gotcha.bilivideo.com/live.flv")
            ),
            patch.object(proxy, "is_safe_proxy_url", AsyncMock(return_value=True)),
            patch.object(proxy.TicketManager, "get_ticket", AsyncMock(return_value=None)),
            patch.object(proxy.live_manager, "subscribe", AsyncMock(return_value=(_FakeLiveStream(), None))),
        ):
            async with app.test_request_context("/proxy/live/1_10000", headers={"X-Real-IP": IP_A}):
                resp = await proxy.proxy_main("live/1_10000")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(proxy.live_stream_slots.active_by_ip, {})


class _RecordingLimiter:
    def __init__(self):
        self.keys = []

    async def is_allowed(self, key, limit, window):
        self.keys.append(key)
        return True, {"remaining": limit, "reset": 0, "limit": limit}


class TestRateLimitBucketSplitting(unittest.IsolatedAsyncioTestCase):
    """Path-keyed buckets give a crawler a fresh allowance per vid/quality."""

    async def _keys_for(self, coro_factory, path):
        import rate_limit
        from app import app

        rec = _RecordingLimiter()
        with (
            patch.object(rate_limit, "get_rate_limiter", return_value=rec),
            patch.dict(rate_limit.appconf["rate_limit"], {"enabled": True}),
        ):
            async with app.test_request_context(path, headers={"X-Real-IP": IP_A}):
                await coro_factory()
        return rec.keys

    async def test_proxy_main_has_coarse_per_ip_bucket(self):
        import proxy

        keys = await self._keys_for(lambda: proxy.proxy_main("teapot/x"), "/proxy/teapot/x")
        self.assertIn(f"{IP_A}:proxy", keys)

    async def test_component_routes_share_coarse_per_ip_bucket(self):
        import views_video

        keys = await self._keys_for(
            lambda: views_video.api_component_player("not-a-vid", 0), "/api/component/player/not-a-vid/0"
        )
        self.assertIn(f"{IP_A}:component", keys)
        keys = await self._keys_for(
            lambda: views_video.api_component_meta("not-a-vid", 0), "/api/component/meta/not-a-vid/0"
        )
        self.assertIn(f"{IP_A}:component", keys)


class TestComponentValidation(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_vid_rejected_before_upstream(self):
        import views_video
        from app import app

        bad = "BV1'; DROP--"
        video_ctor = MagicMock()
        comments = AsyncMock()
        with (
            patch.object(views_video.video, "Video", video_ctor),
            patch.object(views_video.comment, "get_comments", comments),
            patch.object(views_video.comment, "get_sub_comments", comments),
        ):
            for fn, args, path in (
                (views_video.api_component_player, (bad, 0), "/api/component/player/x/0"),
                (views_video.api_component_meta, (bad, 0), "/api/component/meta/x/0"),
                (views_video.api_component_meta_more, (bad, 0), "/api/component/comments/x/0/more"),
                (views_video.api_component_sub_comments, (bad, 1), "/api/component/comments/x/1"),
            ):
                async with app.test_request_context(path, headers={"X-Real-IP": IP_A}):
                    resp = await fn(*args)
                status = resp[1] if isinstance(resp, tuple) else resp.status_code
                self.assertEqual(status, 400, fn.__name__)
        video_ctor.assert_not_called()
        comments.assert_not_awaited()


class TestNyaaApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import views_bangumi

        views_bangumi._nyaa_inflight.clear()

    async def test_cache_hit_skips_upstream(self):
        import views_bangumi
        from app import app

        cached = {"sidebar_html": "<p>x</p>", "ep_torrents": {"1": []}}
        search = AsyncMock(return_value=[])
        with (
            patch.dict(views_bangumi.shared.appconf["site"], {"nyaa_bangumi": True}),
            patch.object(views_bangumi.shared, "cache_get", AsyncMock(return_value=cached)),
            patch.object(views_bangumi, "search_nyaa", search),
            patch.object(views_bangumi.bangumi, "Bangumi", MagicMock()) as bg,
        ):
            async with app.test_request_context("/bangumi/api/nyaa/1", headers={"X-Real-IP": IP_A}):
                resp = await views_bangumi.bangumi_nyaa_api(1)
                body = await resp.get_json()
        self.assertEqual(body["sidebar_html"], "<p>x</p>")
        search.assert_not_awaited()
        bg.assert_not_called()

    async def test_concurrent_misses_singleflight_and_cache(self):
        import views_bangumi
        from app import app

        gate = asyncio.Event()

        async def slow_meta():
            await gate.wait()
            return {"media": {"title": "Test Anime"}}

        bg_inst = MagicMock()
        bg_inst.get_meta = AsyncMock(side_effect=slow_meta)
        search = AsyncMock(return_value=[])
        cache_set = AsyncMock()

        async def call():
            async with app.test_request_context("/bangumi/api/nyaa/7", headers={"X-Real-IP": IP_A}):
                resp = await views_bangumi.bangumi_nyaa_api(7)
                return await resp.get_json()

        with (
            patch.dict(views_bangumi.shared.appconf["site"], {"nyaa_bangumi": True}),
            patch.object(views_bangumi.shared, "cache_get", AsyncMock(return_value=None)),
            patch.object(views_bangumi.shared, "cache_set", cache_set),
            patch.object(views_bangumi.shared, "render_template_with_theme", AsyncMock(return_value="<p/>")),
            patch.object(views_bangumi, "search_nyaa", search),
            patch.object(views_bangumi.bangumi, "Bangumi", MagicMock(return_value=bg_inst)),
        ):
            tasks = [asyncio.create_task(call()) for _ in range(5)]
            await asyncio.sleep(0.05)
            gate.set()
            results = await asyncio.gather(*tasks)
        self.assertEqual(bg_inst.get_meta.await_count, 1)
        self.assertTrue(all(r["sidebar_html"] == "<p/>" for r in results))
        cache_set.assert_not_awaited()

    async def test_cache_and_singleflight_are_scoped_to_theme_and_locale(self):
        import views_bangumi
        from app import app
        from quart import request

        gate = asyncio.Event()
        store = {}
        torrent = MagicMock(title="[CHS] Anime", magnet_url="magnet", seeders=1, leechers=0, size="1G")
        bg_inst = MagicMock()

        async def meta():
            await gate.wait()
            return {"media": {"title": "Anime"}}

        async def cache_get(key, ttl):
            return store.get(key)

        async def cache_set(key, payload, ttl):
            store[key] = payload

        async def render(*args, **kwargs):
            return f"{views_bangumi.shared.detect_theme()}:{views_bangumi.shared.detect_locale()}"

        async def call(theme, lang):
            async with app.test_request_context(f"/bangumi/api/nyaa/7?theme={theme}&lang={lang}"):
                return await (await views_bangumi.bangumi_nyaa_api(7)).get_json()

        bg_inst.get_meta = AsyncMock(side_effect=meta)
        variants = [("modern", "en"), ("modern", "en"), ("modern", "ja"), ("other", "en")]
        with (
            patch.dict(views_bangumi.shared.appconf["site"], {"nyaa_bangumi": True}),
            patch.object(views_bangumi.shared, "detect_theme", side_effect=lambda: request.args["theme"]),
            patch.object(views_bangumi.shared, "cache_get", side_effect=cache_get),
            patch.object(views_bangumi.shared, "cache_set", side_effect=cache_set),
            patch.object(views_bangumi.shared, "render_template_with_theme", side_effect=render),
            patch.object(views_bangumi, "search_nyaa", AsyncMock(return_value=[torrent])),
            patch.object(views_bangumi.bangumi, "Bangumi", return_value=bg_inst),
        ):
            tasks = [asyncio.create_task(call(*variant)) for variant in variants]
            await asyncio.sleep(0.05)
            gate.set()
            results = await asyncio.gather(*tasks)
            self.assertEqual(bg_inst.get_meta.await_count, 3)
            self.assertEqual(
                [result["sidebar_html"] for result in results], [f"{theme}:{lang}" for theme, lang in variants]
            )
            self.assertEqual(set(store), {f"nyaa:v2:7:{theme}:{lang}" for theme, lang in variants})
            for theme, lang in variants:
                self.assertEqual((await call(theme, lang))["sidebar_html"], f"{theme}:{lang}")
            self.assertEqual(bg_inst.get_meta.await_count, 3)

    async def test_upstream_timeout_returns_empty_not_500(self):
        import views_bangumi
        from app import app

        bg_inst = MagicMock()
        bg_inst.get_meta = AsyncMock(side_effect=TimeoutError())
        with (
            patch.dict(views_bangumi.shared.appconf["site"], {"nyaa_bangumi": True}),
            patch.object(views_bangumi.shared, "cache_get", AsyncMock(return_value=None)),
            patch.object(views_bangumi.bangumi, "Bangumi", MagicMock(return_value=bg_inst)),
        ):
            async with app.test_request_context("/bangumi/api/nyaa/8", headers={"X-Real-IP": IP_A}):
                resp = await views_bangumi.bangumi_nyaa_api(8)
                body = await resp.get_json()
        self.assertEqual(body, {"sidebar_html": "", "ep_torrents": {}})


if __name__ == "__main__":
    unittest.main()
