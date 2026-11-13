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
"""Unit tests for DASH streaming, MPD manifests, resume failover, and cancellation."""

import asyncio
import gc
import os
import tempfile
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import miku.dash.common as dash_common
import miku.dash.download_job as dash_download_job
import miku.dash.download_track as dash_download_track
import miku.dash.fetch as dash_fetch
import miku.dash.mpd as dash_mpd
import miku.dash.stream as dash_stream
import miku.views as views
import miku.views.video as views_video
from miku.app import app


class ManifestRefresh(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_retry_and_singleflight(self):
        dash_mpd._manifest_fresh_at.clear()
        dash_mpd._manifest_failed_at.clear()
        dash_mpd._manifest_refreshing.clear()
        dash_mpd._manifest_new_refreshing.clear()
        now = [100.0]
        clock = SimpleNamespace(monotonic=lambda: now[0])
        refresh = AsyncMock(side_effect=[None, {"ok": True}, {"ok": True}])

        async def call(vid):
            async with app.test_request_context(f"/video/dash/{vid}/0/manifest.mpd?fresh=1"):
                return await dash_mpd.video_dash_manifest_view(vid, 0)

        try:
            with (
                patch.object(dash_mpd, "time", clock),
                patch.object(dash_mpd, "_MANIFEST_FRESH_MAX", 1),
                patch.object(dash_mpd, "_manifest_next_sweep", 0.0),
                patch.object(dash_mpd, "_refresh_dash_data", refresh),
                patch.object(dash_mpd, "_load_dash_data", AsyncMock(return_value=None)),
                patch.object(dash_mpd, "generate_vod_mpd", return_value="mpd"),
            ):
                a, b = "BV1xx411c7mD", "BV1xx411c7xx"
                await call(a)
                self.assertNotIn(f"{a}_0", dash_mpd._manifest_fresh_at)
                await call(a)
                await call(b)
                await call(b)
                self.assertEqual(refresh.await_count, 1)
                self.assertEqual(len(dash_mpd._manifest_failed_at), 1)
                now[0] += 6
                await call(a)
                self.assertEqual(refresh.await_count, 2)
                self.assertIn(f"{a}_0", dash_mpd._manifest_fresh_at)
                await call(b)
                self.assertEqual(refresh.await_count, 2)
                now[0] += 31
                await call(b)
                self.assertEqual(refresh.await_count, 3)
                self.assertEqual(len(dash_mpd._manifest_fresh_at), 1)
        finally:
            dash_mpd._manifest_fresh_at.clear()
            dash_mpd._manifest_failed_at.clear()
            dash_mpd._manifest_refreshing.clear()
            dash_mpd._manifest_new_refreshing.clear()

    async def test_concurrent_new_keys_reserve_capacity(self):
        gate, started = asyncio.Event(), asyncio.Event()

        async def refresh(*args):
            started.set()
            await gate.wait()
            return {"ok": True}

        async def call(vid):
            async with app.test_request_context("/?fresh=1"):
                return await dash_mpd.video_dash_manifest_view(vid, 0)

        try:
            with (
                patch.object(dash_mpd, "_MANIFEST_FRESH_MAX", 1),
                patch.object(dash_mpd, "_manifest_next_sweep", 0.0),
                patch.object(dash_mpd, "_refresh_dash_data", AsyncMock(side_effect=refresh)) as fetch,
                patch.object(dash_mpd, "_load_dash_data", AsyncMock(return_value=None)),
                patch.object(dash_mpd, "generate_vod_mpd", return_value="mpd"),
            ):
                task = asyncio.create_task(call("BV1xx411c7mD"))
                await started.wait()
                await call("BV1xx411c7xx")
                await call("BV1xx411c7mD")
                self.assertEqual(fetch.await_count, 1)
                gate.set()
                await task
                self.assertEqual(len(dash_mpd._manifest_fresh_at), 1)
        finally:
            gate.set()
            dash_mpd._manifest_fresh_at.clear()
            dash_mpd._manifest_failed_at.clear()
            dash_mpd._manifest_refreshing.clear()
            dash_mpd._manifest_new_refreshing.clear()


class DownloadCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_active_connection_closed_on_task_cancellation(self):
        started = asyncio.Event()
        conn = SimpleNamespace(close=AsyncMock())

        async def body(*args):
            started.set()
            await asyncio.Event().wait()
            yield b""

        headers = SimpleNamespace(status_code=206, headers={"content-range": "bytes 0-9/10", "content-length": "10"})
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(
                dash_download_track, "_failover_dash_conn", AsyncMock(return_value=(conn, "https://example.com/a", headers))
            ),
            patch.object(dash_download_track, "_yield_dash_body", body),
        ):
            task = asyncio.create_task(
                dash_download_track._download_track_segmented(
                    ["https://example.com/a"], {}, "", tmp + "/track", 10, "test"
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            conn.close.assert_awaited_once()


class AdditionalBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_scans_are_throttled(self):
        class CountingDict(dict):
            scans = 0

            def items(self):
                self.scans += 1
                return super().items()

        fresh = CountingDict({"BV1xx411c7mD_0": 100.0})
        with (
            patch.object(dash_mpd, "_manifest_fresh_at", fresh),
            patch.object(dash_mpd, "_manifest_failed_at", {}),
            patch.object(dash_mpd, "_manifest_refreshing", set()),
            patch.object(dash_mpd, "_manifest_new_refreshing", set()),
            patch.object(dash_mpd, "_manifest_next_sweep", 0.0),
            patch.object(dash_mpd, "_MANIFEST_FRESH_MAX", 1),
            patch.object(dash_mpd, "time", SimpleNamespace(monotonic=lambda: 101.0)),
            patch.object(dash_mpd, "_load_dash_data", AsyncMock(return_value=None)),
            patch.object(dash_mpd, "_refresh_dash_data", AsyncMock()) as refresh,
        ):
            for _ in range(10):
                async with app.test_request_context("/?fresh=1"):
                    await dash_mpd.video_dash_manifest_view("BV1xx411c7xx", 0)
            self.assertEqual(fresh.scans, 1)
            refresh.assert_not_awaited()

    async def test_event_cancellation_closes_active_connection(self):
        event = asyncio.Event()
        conn = SimpleNamespace(close=AsyncMock())

        async def body(*args):
            event.set()
            yield b"a"

        headers = SimpleNamespace(status_code=206, headers={"content-range": "bytes 0-9/10", "content-length": "10"})
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(
                dash_download_track, "_failover_dash_conn", AsyncMock(return_value=(conn, "https://example.com/a", headers))
            ),
            patch.object(dash_download_track, "_yield_dash_body", body),
        ):
            result = await dash_download_track._download_track_segmented(
                ["https://example.com/a"], {}, "", tmp + "/track", 10, "test", cancel_event=event
            )
            self.assertEqual(result, dash_download_track._DOWNLOAD_CANCELLED)
            conn.close.assert_awaited_once()

    async def test_keepalive_reuse_succeeds_and_closes_on_cancellation(self):
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
            patch.object(dash_download_track, "_DOWNLOAD_SEGMENT_SIZE", 1),
            patch.object(dash_download_track, "_pace_delay", return_value=0),
            patch.object(
                dash_download_track, "_failover_dash_conn", AsyncMock(return_value=(conn, "https://example.com/a", first))
            ) as fetch,
            patch.object(dash_download_track, "_yield_dash_body", body),
        ):
            conn.next_request.side_effect = None
            conn.next_request.return_value = second
            result = await dash_download_track._download_track_segmented(
                ["https://example.com/a"], {}, "", tmp + "/ok", 2, "test"
            )
            self.assertEqual(result, 2)
            self.assertEqual(fetch.await_count, 1)
            conn.close.assert_awaited_once()
            conn.close.reset_mock()
            conn.next_request.side_effect = stalled
            task = asyncio.create_task(
                dash_download_track._download_track_segmented(
                    ["https://example.com/a"], {}, "", tmp + "/cancel", 2, "test"
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            conn.close.assert_awaited_once()


class DashProxyLogRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resumed_conn_skip_longer_than_idle_timeout_does_not_false_stall(self):
        class FakeRawConn:
            async def iter_chunks(self):
                for _ in range(30):
                    await asyncio.sleep(0.02)
                    yield b"x" * 10240
                await asyncio.sleep(0.02)
                yield b"HELLO_WORLD"

            async def close(self):
                pass

        resumed = dash_stream._ResumedDashConn(FakeRawConn(), skip=30 * 10240 + 6, limit=5)
        with (
            patch.object(dash_stream, "DASH_BODY_IDLE_TIMEOUT", 0.5),
            patch.object(dash_stream, "DASH_BODY_GRACE", 0.04),
            patch.object(dash_stream, "DASH_BODY_FLOOR_KBPS", 100),
        ):
            chunks = [c async for c in dash_stream._yield_dash_body(resumed, "test", floor_watchdog=True)]
        self.assertEqual(b"".join(chunks), b"WORLD")

    async def test_resumed_conn_tarpit_during_skip_still_trips_floor_watchdog(self):
        class SlowRawConn:
            async def iter_chunks(self):
                for _ in range(5):
                    await asyncio.sleep(0.02)
                    yield b"x" * 10

        resumed = dash_stream._ResumedDashConn(SlowRawConn(), skip=1000, limit=10)
        with (
            patch.object(dash_stream, "DASH_BODY_IDLE_TIMEOUT", 0.5),
            patch.object(dash_stream, "DASH_BODY_GRACE", 0.03),
            patch.object(dash_stream, "DASH_BODY_FLOOR_KBPS", 100),
        ):
            with self.assertRaises(dash_stream._SlowDashBody):
                _ = [c async for c in dash_stream._yield_dash_body(resumed, "test", floor_watchdog=True)]

    async def test_failover_same_mirror_retry_when_pending_first_416s(self):
        dash_common._mirror_range_shy.clear()
        dash_common._mirror_health.clear()

        akam_url = "https://upos-hz-mirrorakam.akamaized.net/track.m4s"
        cosov_url = "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s"

        class FakeConn:
            async def close(self):
                pass

        async def fake_attempt(url, headers, proxy_url, timeout=None):
            if "mirrorcosov" in url:
                return FakeConn(), SimpleNamespace(
                    status_code=416,
                    headers={"content-range": "bytes */7372863"},
                )
            return FakeConn(), SimpleNamespace(
                status_code=206,
                headers={
                    "content-range": "bytes 430980-999999/7372863",
                    "content-length": str(999999 - 430980 + 1),
                },
            )

        pending = [cosov_url]
        with patch.object(dash_stream, "_fetch_dash_attempt", side_effect=fake_attempt):
            conn, used, resp = await dash_stream._failover_dash_conn(
                pending,
                {"range": "bytes=136915-999999"},
                "",
                "BV151ac6VEQX:0 audio/30232/0",
                "bytes=136915-999999",
                294065,
                863085,
                retry_url=akam_url,
                retry_served=294065,
                retry_budget=2,
            )
        self.assertEqual(used, akam_url)
        self.assertEqual(resp.status_code, 206)
        self.assertTrue(dash_common._mirror_range_shy_at("upos-sz-mirrorcosov.bilivideo.com", 430980))

    async def test_fetch_dash_track_timeout_includes_host_in_error(self):
        dash_common._mirror_health.clear()
        url = "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s"
        with patch.object(dash_stream, "_fetch_dash_attempt", AsyncMock(side_effect=TimeoutError())):
            with self.assertRaises(dash_stream._DashUpstreamError) as ctx:
                await dash_stream._fetch_dash_track([url], {}, "", "BV1B5Jc6nEYW:0 video/80/12")
        self.assertIn("upos-sz-mirrorcosov.bilivideo.com stalled", str(ctx.exception))

    async def test_method_not_allowed_returns_405_instead_of_500(self):
        client = app.test_client()
        resp = await client.post("/preferences")
        self.assertEqual(resp.status_code, 405)

    async def test_cancelled_video_view_leaves_no_unawaited_coroutines(self):
        async def cancel_gather(*coros, **kwargs):
            for c in coros:
                c.close()
            raise asyncio.CancelledError()

        with (
            patch.object(views_video, "cache_minutes", return_value=0),
            patch("miku.views.video.asyncio.gather", side_effect=cancel_gather),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always", RuntimeWarning)
            async with views.app.test_request_context("/video/BV1xx411c7xx"):
                with self.assertRaises(asyncio.CancelledError):
                    await views_video.video_view("BV1xx411c7xx", 0)
            gc.collect()
        unawaited = [w for w in caught if "was never awaited" in str(w.message)]
        self.assertEqual(unawaited, [])

    async def test_video_view_gracefully_handles_cancelled_related_and_tags(self):
        sample_vinfo = {
            "bvid": "BV1xx411c7xx",
            "title": "Test Video",
            "pic": "https://i0.hdslb.com/test.jpg",
            "owner": {"name": "Test Owner", "mid": 123},
            "stat": {"view": 100, "danmaku": 10, "reply": 5, "like": 20},
            "cid": 12345,
            "duration": 120,
            "pubdate": 1600000000,
            "desc": "test desc",
        }

        fake_results = [
            sample_vinfo,
            asyncio.CancelledError(),
            asyncio.CancelledError(),
            [{"page": 1, "part": "BV1xx411c7xx"}],
        ]

        async def fake_gather(*coros, **kwargs):
            for c in coros:
                c.close()
            return fake_results

        with (
            patch.object(views_video, "cache_minutes", return_value=0),
            patch("miku.views.video.asyncio.gather", side_effect=fake_gather),
            patch.object(
                views_video, "render_template_with_theme", new=AsyncMock(return_value="<html>OK</html>")
            ) as mock_render,
        ):
            async with views.app.test_request_context("/video/BV1xx411c7xx"):
                resp = await views_video.video_view("BV1xx411c7xx", 0)
                self.assertEqual(resp, "<html>OK</html>")
                _, kwargs = mock_render.call_args
                self.assertIsInstance(kwargs["vrelated"], list)
                self.assertEqual(kwargs["vrelated"], [])
                self.assertIsInstance(kwargs["vtags"], list)
                self.assertEqual(kwargs["vtags"], [])


class DashDownloadResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_failover_salvage_is_not_wrapped_twice(self):
        dash_common._mirror_health.clear()
        dash_common._mirror_range_shy.clear()

        mirror_a = "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s"
        mirror_b = "https://upos-hz-mirrorakam.akamaized.net/track.m4s"
        body = bytes(range(100))
        headers_206 = {
            "content-range": "bytes 0-99/100",
            "content-length": "100",
        }

        class CutConn:
            async def iter_chunks(self):
                yield body[:40]
                raise OSError("connection reset")

            async def close(self):
                pass

        class FullConn:
            async def iter_chunks(self):
                yield body

            async def close(self):
                pass

        async def fake_attempt(url, headers, proxy_url, timeout=None):
            conn = CutConn() if "mirrorcosov" in url else FullConn()
            return conn, SimpleNamespace(status_code=206, headers=dict(headers_206))

        fd, dest = tempfile.mkstemp()
        os.close(fd)
        try:
            with (
                patch.object(
                    dash_download_track,
                    "_segment_mirror_order",
                    side_effect=lambda urls, idx: list(urls),
                ),
                patch.object(dash_stream, "_fetch_dash_attempt", side_effect=fake_attempt),
                patch.object(dash_download_track, "_DOWNLOAD_SEGMENT_SIZE", 100),
                patch.object(dash_download_track, "_DOWNLOAD_WRITE_BATCH", 1),
            ):
                async with asyncio.timeout(5):
                    written = await dash_download_track._download_track_segmented(
                        [mirror_a, mirror_b], {}, "", dest, len(body), "test track"
                    )
            self.assertEqual(written, len(body))
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), body)
        finally:
            os.unlink(dest)


class DashSingleflightCancellationTests(unittest.IsolatedAsyncioTestCase):
    vid = "BV1CancelledDash"

    async def asyncSetUp(self):
        dash_fetch._dash_inflight.clear()
        self.addCleanup(dash_fetch._dash_inflight.clear)
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock(return_value=True)
        redis.delete = AsyncMock(return_value=1)
        patcher = patch.object(dash_fetch, "appredis", redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.key = f"miku_dash_{self.vid}_0"

    async def test_cancelled_owner_settles_joiners_and_clears_singleflight(self):
        started = asyncio.Event()

        async def never_returns(vid, idx, key):
            started.set()
            await asyncio.sleep(3600)

        with patch.object(dash_fetch, "_fetch_and_cache_dash_data", never_returns):
            owner = asyncio.create_task(dash_fetch._load_dash_data(self.vid, 0))
            await started.wait()
            joiner = asyncio.create_task(dash_fetch._load_dash_data(self.vid, 0))
            await asyncio.sleep(0)
            self.assertIn(self.key, dash_fetch._dash_inflight)

            owner.cancel()
            self.assertIsNone(await asyncio.wait_for(joiner, 5))
            with self.assertRaises(asyncio.CancelledError):
                await owner
        self.assertNotIn(self.key, dash_fetch._dash_inflight)

        refetch = AsyncMock(return_value={"dash": {"video": []}})
        with patch.object(dash_fetch, "_fetch_and_cache_dash_data", refetch):
            self.assertEqual(
                await asyncio.wait_for(dash_fetch._load_dash_data(self.vid, 0), 5),
                {"dash": {"video": []}},
            )
        refetch.assert_awaited_once()

    async def test_cancelled_joiner_does_not_cancel_shared_fetch(self):
        started = asyncio.Event()
        release = asyncio.Event()
        payload = {"dash": {"video": [{"id": 80}]}}

        async def gated(vid, idx, key):
            started.set()
            await release.wait()
            return payload

        with patch.object(dash_fetch, "_fetch_and_cache_dash_data", gated):
            owner = asyncio.create_task(dash_fetch._load_dash_data(self.vid, 0))
            await started.wait()
            leaver = asyncio.create_task(dash_fetch._load_dash_data(self.vid, 0))
            stayer = asyncio.create_task(dash_fetch._load_dash_data(self.vid, 0))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            leaver.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await leaver

            release.set()
            self.assertEqual(await asyncio.wait_for(stayer, 5), payload)
            self.assertEqual(await asyncio.wait_for(owner, 5), payload)


class DashAudioOptimizationTests(unittest.IsolatedAsyncioTestCase):
    def test_dash_candidate_urls_prioritizes_direct_cdn_over_akamai_and_mcdn(self):
        from miku.dash.stream import _dash_candidate_urls

        track = {
            "base_url": "https://upos-hz-mirrorakam.akamaized.net/track.m4s",
            "backup_url": [
                "https://d1.mcdn.bilivideo.cn/track.m4s",
                "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s",
            ],
        }
        candidates = _dash_candidate_urls(track)
        self.assertEqual(len(candidates), 3)
        self.assertIn("mirrorcosov.bilivideo.com", candidates[0])
        self.assertIn("mirrorakam.akamaized.net", candidates[1])
        self.assertIn("mcdn.bilivideo.cn", candidates[2])

    async def test_peek_content_length_sends_range_zero_and_parses_content_range(self):
        sent_headers = {}

        async def fake_open(url, headers, proxy_url):
            sent_headers.update(headers)
            return (
                AsyncMock(),
                SimpleNamespace(
                    status_code=206,
                    headers={"content-range": "bytes 0-0/4389592", "content-length": "1"},
                ),
            )

        with patch.object(dash_download_job, "_open_cdn_track", side_effect=fake_open):
            size = await dash_download_job._peek_content_length(
                "https://upos-sz-mirrorcosov.bilivideo.com/audio.m4s", {}, ""
            )
            self.assertEqual(size, 4389592)
            self.assertEqual(sent_headers.get("range"), "bytes=0-0")


if __name__ == "__main__":
    unittest.main()
