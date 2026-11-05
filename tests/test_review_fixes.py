import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


class RateKeys(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_routes_share_family_budgets_and_isolate_clients(self):
        import rate_limit
        from app import app

        limiter = MagicMock()
        limiter.is_allowed = AsyncMock(return_value=(False, {"remaining": 0, "reset": 1, "limit": 10}))
        cases = [
            (["/proxy/download/BV1xx411c7mD/0/80", "/proxy/download/BV1xx411c7mD/1/64"], 10),
            (["/res/danmaku/BV1xx411c7mD", "/res/danmaku/BV1xx411c7mD:2"], 60),
            (["/video_listen/BV1xx411c7mD", "/video_listen/BV1xx411c7mD:2/"], 60),
            (["/read/cv1", "/opus/2"], 60),
            (["/space/1", "/space/2/"], 60),
            (["/space/1/json", "/space/2/json"], 60),
            (["/author/1", "/author/2/"], 60),
            (["/audio/au1", "/audio/au2"], 60),
            (["/audio_list/am1", "/audio_list/am2:1"], 60),
            (["/vv/1", "/vv/2/"], 60),
            (["/video/dash/BV1xx411c7mD/0/manifest.mpd", "/video/dash/BV1xx411c7mD/1/manifest.mpd"], 60),
        ]
        with (
            patch.dict(rate_limit.appconf["rate_limit"], {"enabled": True}),
            patch.object(rate_limit, "get_rate_limiter", return_value=limiter),
            patch.object(rate_limit, "_peer_is_trusted_proxy", return_value=True),
        ):
            client = app.test_client()
            keys = []
            for paths, limit in cases:
                limiter.is_allowed.reset_mock()
                for path in paths:
                    response = await client.get(path, headers={"X-Real-IP": "203.0.113.1"})
                    self.assertEqual(response.status_code, 429, path)
                calls = limiter.is_allowed.await_args_list
                self.assertEqual(calls[0].args, calls[1].args, paths)
                self.assertEqual(calls[0].args[1:], (limit, 60))
                keys.append(calls[0].args[0])
                await client.get(paths[0], headers={"X-Real-IP": "203.0.113.2"})
                self.assertNotEqual(calls[0].args[0], limiter.is_allowed.await_args.args[0])
            self.assertEqual(len(keys), len(set(keys)))


class ManifestRefresh(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_retry_and_singleflight(self):
        import dash_mpd as m
        from app import app

        m._manifest_fresh_at.clear()
        m._manifest_failed_at.clear()
        m._manifest_refreshing.clear()
        m._manifest_new_refreshing.clear()
        now = [100.0]
        clock = SimpleNamespace(monotonic=lambda: now[0])
        refresh = AsyncMock(side_effect=[None, {"ok": True}, {"ok": True}])

        async def call(vid):
            async with app.test_request_context(f"/video/dash/{vid}/0/manifest.mpd?fresh=1"):
                return await m.video_dash_manifest_view(vid, 0)

        try:
            with (
                patch.object(m, "time", clock),
                patch.object(m, "_MANIFEST_FRESH_MAX", 1),
                patch.object(m, "_manifest_next_sweep", 0.0),
                patch.object(m, "_refresh_dash_data", refresh),
                patch.object(m, "_load_dash_data", AsyncMock(return_value=None)),
                patch.object(m, "generate_vod_mpd", return_value="mpd"),
            ):
                a, b = "BV1xx411c7mD", "BV1xx411c7xx"
                await call(a)
                self.assertNotIn(f"{a}_0", m._manifest_fresh_at)
                await call(a)
                await call(b)
                await call(b)
                self.assertEqual(refresh.await_count, 1)
                self.assertEqual(len(m._manifest_failed_at), 1)
                now[0] += 6
                await call(a)
                self.assertEqual(refresh.await_count, 2)
                self.assertIn(f"{a}_0", m._manifest_fresh_at)
                await call(b)
                self.assertEqual(refresh.await_count, 2)
                now[0] += 31
                await call(b)
                self.assertEqual(refresh.await_count, 3)
                self.assertEqual(len(m._manifest_fresh_at), 1)
        finally:
            m._manifest_fresh_at.clear()
            m._manifest_failed_at.clear()
            m._manifest_refreshing.clear()
            m._manifest_new_refreshing.clear()

    async def test_concurrent_new_keys_reserve_capacity(self):
        import dash_mpd as m
        from app import app

        gate, started = asyncio.Event(), asyncio.Event()

        async def refresh(*args):
            started.set()
            await gate.wait()
            return {"ok": True}

        async def call(vid):
            async with app.test_request_context("/?fresh=1"):
                return await m.video_dash_manifest_view(vid, 0)

        try:
            with (
                patch.object(m, "_MANIFEST_FRESH_MAX", 1),
                patch.object(m, "_manifest_next_sweep", 0.0),
                patch.object(m, "_refresh_dash_data", AsyncMock(side_effect=refresh)) as fetch,
                patch.object(m, "_load_dash_data", AsyncMock(return_value=None)),
                patch.object(m, "generate_vod_mpd", return_value="mpd"),
            ):
                task = asyncio.create_task(call("BV1xx411c7mD"))
                await started.wait()
                await call("BV1xx411c7xx")
                await call("BV1xx411c7mD")
                self.assertEqual(fetch.await_count, 1)
                gate.set()
                await task
                self.assertEqual(len(m._manifest_fresh_at), 1)
        finally:
            gate.set()
            m._manifest_fresh_at.clear()
            m._manifest_failed_at.clear()
            m._manifest_refreshing.clear()
            m._manifest_new_refreshing.clear()


class BoundedScan(unittest.IsolatedAsyncioTestCase):
    async def test_limits_empty_scan_rounds_and_finds_urls(self):
        import proxy

        scan = AsyncMock(side_effect=[(n, []) for n in range(1, 9)])
        get = AsyncMock()
        with patch.object(proxy.appredis, "scan", scan), patch.object(proxy.appredis, "get", get):
            self.assertIsNone(await proxy._first_live_quality_url("123"))
        self.assertEqual(scan.await_count, 8)
        self.assertEqual(scan.await_args.kwargs, {"cursor": 7, "match": "miku_live_123_*", "count": 100})
        get.assert_not_awaited()
        scan = AsyncMock(side_effect=[(2, []), (0, [b"key"])])
        with (
            patch.object(proxy.appredis, "scan", scan),
            patch.object(proxy.appredis, "get", AsyncMock(return_value="url")),
        ):
            self.assertEqual(await proxy._first_live_quality_url("123"), "url")
            self.assertEqual(scan.await_count, 2)


class DownloadCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_active_connection_closed_on_task_cancellation(self):
        import dash_download_track as d

        started = asyncio.Event()
        conn = SimpleNamespace(close=AsyncMock())

        async def body(*args):
            started.set()
            await asyncio.Event().wait()
            yield b""

        headers = SimpleNamespace(status_code=206, headers={"content-range": "bytes 0-9/10", "content-length": "10"})
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(d, "_failover_dash_conn", AsyncMock(return_value=(conn, "https://example.com/a", headers))),
            patch.object(d, "_yield_dash_body", body),
        ):
            task = asyncio.create_task(
                d._download_track_segmented(["https://example.com/a"], {}, "", tmp + "/track", 10, "test")
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            conn.close.assert_awaited_once()


class CacheWrites(unittest.IsolatedAsyncioTestCase):
    async def test_danmaku_only_caches_xml_like_body(self):
        import res
        from app import app

        get_xml = AsyncMock(side_effect=["  upstream error", "  <i></i>"])
        cache_write = AsyncMock()
        with (
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", cache_write),
            patch.object(res.video.Video, "get_danmaku_xml", get_xml),
        ):
            client = app.test_client()
            await client.get("/res/danmaku/BV1xx411c7mD")
            cache_write.assert_not_awaited()
            await client.get("/res/danmaku/BV1xx411c7mD")
            cache_write.assert_awaited_once_with("miku_danmaku_xml_BV1xx411c7mD_0", res.DANMAKU_CACHE_TTL, "  <i></i>")

    async def test_nyaa_caches_only_present_results(self):
        import views_bangumi as b

        bg = MagicMock()
        bg.get_meta = AsyncMock(return_value={"media": {"title": "Anime"}})
        torrent = SimpleNamespace(title="[CHS] Anime", magnet_url="magnet", seeders=1, leechers=0, size="1G")
        search = AsyncMock(return_value=[])
        cache = AsyncMock()
        with (
            patch.object(b.bangumi, "Bangumi", return_value=bg),
            patch.object(b, "search_nyaa", search),
            patch.object(b.shared, "render_template_with_theme", AsyncMock(return_value="sidebar")),
            patch.object(b.shared, "cache_set", cache),
        ):
            _, cacheable = await b._build_nyaa_payload(1)
            self.assertFalse(cacheable)
            cache.assert_not_awaited()
            search.return_value = [torrent]
            _, cacheable = await b._build_nyaa_payload(1)
            self.assertTrue(cacheable)
            self.assertEqual(cache.await_args.args[2], b.NYAA_CACHE_TTL)

    async def test_listen_does_not_cache_fallback_pages(self):
        import views_video as v
        from app import app

        inst = MagicMock()
        inst.get_info = AsyncMock(return_value={"bvid": "BV1xx411c7mD", "title": "Video"})
        inst.get_tags = AsyncMock(return_value=[])
        inst.get_related = AsyncMock(return_value=[])
        inst.get_pages = AsyncMock(side_effect=[RuntimeError("offline"), [{"page": 1, "part": "real"}]])
        cache = AsyncMock()
        with (
            patch.object(v.video, "Video", return_value=inst),
            patch.object(v, "cache_minutes", return_value=15),
            patch.object(v, "cache_get", AsyncMock(return_value=None)),
            patch.object(v, "cache_set", cache),
            patch.object(v.appredis, "exists", AsyncMock(return_value=True)),
            patch.object(v.comment, "get_comments", AsyncMock(return_value={})),
            patch.object(v, "render_template_with_theme", AsyncMock(return_value="ok")),
        ):
            client = app.test_client()
            response = await client.get("/video_listen/BV1xx411c7mD")
            self.assertEqual(response.status_code, 200)
            cache.assert_not_awaited()
            await client.get("/video_listen/BV1xx411c7mD")
            self.assertEqual(cache.await_args.args[1]["vset"], [{"page": 1, "part": "real"}])


class AdditionalBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_scans_are_throttled(self):
        import dash_mpd as m
        from app import app

        class CountingDict(dict):
            scans = 0

            def items(self):
                self.scans += 1
                return super().items()

        fresh = CountingDict({"BV1xx411c7mD_0": 100.0})
        with (
            patch.object(m, "_manifest_fresh_at", fresh),
            patch.object(m, "_manifest_failed_at", {}),
            patch.object(m, "_manifest_refreshing", set()),
            patch.object(m, "_manifest_new_refreshing", set()),
            patch.object(m, "_manifest_next_sweep", 0.0),
            patch.object(m, "_MANIFEST_FRESH_MAX", 1),
            patch.object(m, "time", SimpleNamespace(monotonic=lambda: 101.0)),
            patch.object(m, "_load_dash_data", AsyncMock(return_value=None)),
            patch.object(m, "_refresh_dash_data", AsyncMock()) as refresh,
        ):
            for _ in range(10):
                async with app.test_request_context("/?fresh=1"):
                    await m.video_dash_manifest_view("BV1xx411c7xx", 0)
            self.assertEqual(fresh.scans, 1)
            refresh.assert_not_awaited()

    async def test_event_cancellation_closes_active_connection(self):
        import dash_download_track as d

        event = asyncio.Event()
        conn = SimpleNamespace(close=AsyncMock())

        async def body(*args):
            event.set()
            yield b"a"

        headers = SimpleNamespace(status_code=206, headers={"content-range": "bytes 0-9/10", "content-length": "10"})
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(d, "_failover_dash_conn", AsyncMock(return_value=(conn, "https://example.com/a", headers))),
            patch.object(d, "_yield_dash_body", body),
        ):
            result = await d._download_track_segmented(
                ["https://example.com/a"], {}, "", tmp + "/track", 10, "test", cancel_event=event
            )
            self.assertEqual(result, d._DOWNLOAD_CANCELLED)
            conn.close.assert_awaited_once()

    async def test_keepalive_reuse_succeeds_and_closes_on_cancellation(self):
        import dash_download_track as d

        started = asyncio.Event()

        async def stalled(*args):
            started.set()
            await asyncio.Event().wait()

        conn = SimpleNamespace(close=AsyncMock(), next_request=AsyncMock(side_effect=stalled))
        first = SimpleNamespace(status_code=206, headers={"content-range": "bytes 0-0/2", "content-length": "1"})
        second = SimpleNamespace(status_code=206, headers={"content-range": "bytes 1-1/2", "content-length": "1"})

        async def body(*args):
            yield b"a"

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(d, "_DOWNLOAD_SEGMENT_SIZE", 1),
            patch.object(d, "_pace_delay", return_value=0),
            patch.object(
                d, "_failover_dash_conn", AsyncMock(return_value=(conn, "https://example.com/a", first))
            ) as fetch,
            patch.object(d, "_yield_dash_body", body),
        ):
            conn.next_request.side_effect = None
            conn.next_request.return_value = second
            result = await d._download_track_segmented(["https://example.com/a"], {}, "", tmp + "/ok", 2, "test")
            self.assertEqual(result, 2)
            self.assertEqual(fetch.await_count, 1)
            conn.close.assert_awaited_once()
            conn.close.reset_mock()
            conn.next_request.side_effect = stalled
            task = asyncio.create_task(
                d._download_track_segmented(["https://example.com/a"], {}, "", tmp + "/cancel", 2, "test")
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            conn.close.assert_awaited_once()
