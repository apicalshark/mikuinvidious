"""Run with PYTHONPATH=python .venv/bin/python -m unittest discover -s tests."""

import unittest
from unittest.mock import AsyncMock, patch

import views
from api import user
from api.exceptions import ResponseCodeException
from dash_fetch import _select_durl_results


def durl_result(actual, requested, description):
    return {"quality": actual, "_requested_qn": requested, "new_description": description, "ext": ".mp4"}


class DurlSelectionTests(unittest.TestCase):
    def test_mixed_results_keep_distinct_qualities_and_prefer_exact(self):
        for exact_first in (True, False):
            with self.subTest(exact_first=exact_first):
                exact = durl_result(64, 64, "720p exact")
                duplicate = durl_result(64, 80, "1080p requested")
                results = [exact, duplicate] if exact_first else [duplicate, exact]
                results.extend([durl_result(32, 16, "360p requested"), None])
                picked = _select_durl_results(results, {
                    "support_formats": [{"quality": "32", "display_desc": "480p"}]
                })
                self.assertEqual([r["quality"] for r in picked], [64, 32])
                self.assertIs(picked[0], exact)
                self.assertEqual([r["new_description"] for r in picked], ["720p exact", "480p"])
                self.assertTrue(all("_requested_qn" not in r for r in picked))

    def test_all_mismatches_keep_single_best_with_malformed_formats(self):
        picked = _select_durl_results([
            durl_result(32, 16, "360p"), durl_result(64, 80, "1080p")
        ], {"support_formats": [
            None, "bad", 42, [], {}, {"quality": "invalid", "new_description": "bad"},
            {"quality": "64", "new_description": "720p", "display_desc": "alternate"},
        ]})
        self.assertEqual(picked, [{"quality": 64, "new_description": "720p", "ext": ".mp4"}])

    def test_missing_description_preserves_existing_fallback(self):
        self.assertEqual(_select_durl_results([durl_result(32, 80, "existing")], None), [
            {"quality": 32, "new_description": "existing", "ext": ".mp4"}
        ])

    def test_empty_results(self):
        self.assertEqual(_select_durl_results([None], None), [])


class SpaceFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_fetch_empty_states_and_recovery(self):
        primary_empty = {"list": {"vlist": []}}
        fallback_empty = {"archives": [], "page": {"total": 0}}
        fallback_full = {"archives": [{"bvid": "BVtest", "title": "Video"}]}
        cases = [
            ("genuinely empty", [primary_empty, fallback_empty], False, False, 0),
            ("network failure", [TimeoutError(), OSError(), TimeoutError()], True, False, 0),
            ("fallback failure", [primary_empty, OSError(), OSError()], True, False, 0),
            ("primary failure", [TimeoutError(), fallback_empty], True, False, 0),
            ("recovered content", [TimeoutError(), fallback_full], False, False, 1),
            ("retry recovered content", [primary_empty, OSError(), fallback_full], False, False, 1),
            ("risk control", [ResponseCodeException(-352, "risk"), fallback_empty], False, True, 0),
        ]
        for name, responses, load_failed, degraded, count in cases:
            with self.subTest(name=name):
                fetch = AsyncMock(side_effect=responses)

                class FakeApi:
                    def __init__(self, *args, **kwargs):
                        pass

                    def update_params(self, **kwargs):
                        return self

                    @property
                    def result(self):
                        return fetch()

                render = AsyncMock(return_value="space page")
                import views_space
                with (
                    patch.object(user, "Api", FakeApi),
                    patch.object(user.User, "get_user_info", AsyncMock(return_value={"name": "Test"})),
                    patch.object(views_space, "cache_minutes", return_value=0),
                    patch.object(views_space, "render_template_with_theme", render),
                ):
                    async with views.app.test_request_context("/space/123"):
                        response = await views_space.space_view("123")
                self.assertEqual(response.status_code, 200)
                context = render.await_args.kwargs
                self.assertEqual(context["load_failed"], load_failed)
                self.assertEqual(context["degraded"], degraded)
                self.assertEqual(len(context["uvids"]["list"]["vlist"]), count)
                self.assertEqual(response.headers.get("X-Degraded"), "risk-control" if degraded else None)
                self.assertEqual(fetch.await_count, len(responses))


class CommentPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_meta_more_returns_503_on_upstream_failure(self):
        import views_video

        with (
            patch("views_video.comment.get_comments", AsyncMock(side_effect=TimeoutError("upstream timeout"))),
            patch("views_video.render_template_with_theme", AsyncMock(return_value="rendered_error_comments")),
        ):
            async with views.app.test_request_context("/api/component/comments/BV1xx411c7xx/0/more?next=offset1"):
                result = await views_video.api_component_meta_more("BV1xx411c7xx", 0)
                # When failed, returns tuple (body, 503)
                self.assertIsInstance(result, tuple)
                self.assertEqual(result[1], 503)
                self.assertEqual(result[0], "rendered_error_comments")

    async def test_meta_more_returns_200_on_success(self):
        import views_video

        mock_comments = {
            "page": {"count": 1},
            "replies": [{"rpid": 123}],
            "next_offset": "offset2",
            "is_end": False,
        }
        with (
            patch("views_video.comment.get_comments", AsyncMock(return_value=mock_comments)),
            patch("views_video.render_template_with_theme", AsyncMock(return_value="rendered_comments")),
        ):
            async with views.app.test_request_context("/api/component/comments/BV1xx411c7xx/0/more?next=offset1"):
                result = await views_video.api_component_meta_more("BV1xx411c7xx", 0)
                # When successful, returns body directly
                self.assertEqual(result, "rendered_comments")


class DashProxyLogRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resumed_conn_skip_longer_than_idle_timeout_does_not_false_stall(self):
        import asyncio
        import dash_stream

        class FakeRawConn:
            async def iter_chunks(self):
                # 6 chunks * 0.02s = 0.12s total (> patched idle timeout 0.05s and grace 0.04s)
                for _ in range(5):
                    await asyncio.sleep(0.02)
                    yield b"x" * 10240
                await asyncio.sleep(0.02)
                yield b"HELLO_WORLD"

            async def close(self):
                pass

        resumed = dash_stream._ResumedDashConn(FakeRawConn(), skip=5 * 10240 + 6, limit=5)
        with (
            patch.object(dash_stream, "DASH_BODY_IDLE_TIMEOUT", 0.05),
            patch.object(dash_stream, "DASH_BODY_GRACE", 0.04),
            patch.object(dash_stream, "DASH_BODY_FLOOR_KBPS", 100),
        ):
            chunks = [c async for c in dash_stream._yield_dash_body(resumed, "test", floor_watchdog=True)]
        self.assertEqual(b"".join(chunks), b"WORLD")

    async def test_resumed_conn_tarpit_during_skip_still_trips_floor_watchdog(self):
        import asyncio
        import dash_stream

        class SlowRawConn:
            async def iter_chunks(self):
                for _ in range(5):
                    await asyncio.sleep(0.02)
                    yield b"x" * 10  # < 1 KB/s

        resumed = dash_stream._ResumedDashConn(SlowRawConn(), skip=1000, limit=10)
        with (
            patch.object(dash_stream, "DASH_BODY_IDLE_TIMEOUT", 0.5),
            patch.object(dash_stream, "DASH_BODY_GRACE", 0.03),
            patch.object(dash_stream, "DASH_BODY_FLOOR_KBPS", 100),
        ):
            with self.assertRaises(dash_stream._SlowDashBody):
                _ = [c async for c in dash_stream._yield_dash_body(resumed, "test", floor_watchdog=True)]

    async def test_failover_same_mirror_retry_when_pending_first_416s(self):
        from types import SimpleNamespace
        import dash_common
        import dash_stream

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
        import dash_common
        import dash_stream

        dash_common._mirror_health.clear()
        url = "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s"
        with patch.object(dash_stream, "_fetch_dash_attempt", AsyncMock(side_effect=TimeoutError())):
            with self.assertRaises(dash_stream._DashUpstreamError) as ctx:
                await dash_stream._fetch_dash_track([url], {}, "", "BV1B5Jc6nEYW:0 video/80/12")
        self.assertIn("upos-sz-mirrorcosov.bilivideo.com stalled", str(ctx.exception))

    async def test_method_not_allowed_returns_405_instead_of_500(self):
        from app import app

        client = app.test_client()
        resp = await client.post("/preferences")
        self.assertEqual(resp.status_code, 405)

    async def test_cancelled_video_view_leaves_no_unawaited_coroutines(self):
        import asyncio
        import gc
        import warnings
        import views_video

        async def cancel_gather(*coros, **kwargs):
            for c in coros:
                c.close()
            raise asyncio.CancelledError()

        with (
            patch.object(views_video, "cache_minutes", return_value=0),
            patch("views_video.asyncio.gather", side_effect=cancel_gather),
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
        import asyncio
        import views_video

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
            patch("views_video.asyncio.gather", side_effect=fake_gather),
            patch.object(views_video, "render_template_with_theme", new=AsyncMock(return_value="<html>OK</html>")) as mock_render,
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
        """An overlapping-206 salvage must not be re-wrapped by the caller.

        ``_failover_dash_conn`` already returns a byte-contiguous
        ``_ResumedDashConn``; validating the same headers again used to nest a
        second wrapper, and ``_yield_dash_body`` unwraps one level only — the
        prefix was skipped twice, corrupting the appended bytes (and looping
        forever on the short body).
        """
        import asyncio
        import os
        import tempfile
        from types import SimpleNamespace

        import dash_common
        import dash_download_track
        import dash_stream

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
                # One chunk: any double skip is visible in the written bytes.
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
                # The double-wrap bug also never finished (wrong byte
                # accounting, unbounded resume), so bound it.
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
    """A cancelled dash fetch must never strand other callers on a shared future."""

    vid = "BV1CancelledDash"

    async def asyncSetUp(self):
        import dash_fetch

        self.dash_fetch = dash_fetch
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
        import asyncio

        started = asyncio.Event()

        async def never_returns(vid, idx, key):
            started.set()
            await asyncio.sleep(3600)

        with patch.object(self.dash_fetch, "_fetch_and_cache_dash_data", never_returns):
            owner = asyncio.create_task(self.dash_fetch._load_dash_data(self.vid, 0))
            await started.wait()
            joiner = asyncio.create_task(self.dash_fetch._load_dash_data(self.vid, 0))
            await asyncio.sleep(0)
            self.assertIn(self.key, self.dash_fetch._dash_inflight)

            owner.cancel()
            # Joiners degrade to "no dash" (falling back to durl) instead of
            # hanging forever on a future nobody will settle.
            self.assertIsNone(await asyncio.wait_for(joiner, 5))
            with self.assertRaises(asyncio.CancelledError):
                await owner
        self.assertNotIn(self.key, self.dash_fetch._dash_inflight)

        # The cancelled attempt must not poison later requests: no lock-step
        # entry, no negative cache, so the next caller fetches again.
        refetch = AsyncMock(return_value={"dash": {"video": []}})
        with patch.object(self.dash_fetch, "_fetch_and_cache_dash_data", refetch):
            self.assertEqual(
                await asyncio.wait_for(self.dash_fetch._load_dash_data(self.vid, 0), 5),
                {"dash": {"video": []}},
            )
        refetch.assert_awaited_once()

    async def test_cancelled_joiner_does_not_cancel_shared_fetch(self):
        import asyncio

        started = asyncio.Event()
        release = asyncio.Event()
        payload = {"dash": {"video": [{"id": 80}]}}

        async def gated(vid, idx, key):
            started.set()
            await release.wait()
            return payload

        with patch.object(self.dash_fetch, "_fetch_and_cache_dash_data", gated):
            owner = asyncio.create_task(self.dash_fetch._load_dash_data(self.vid, 0))
            await started.wait()
            leaver = asyncio.create_task(self.dash_fetch._load_dash_data(self.vid, 0))
            stayer = asyncio.create_task(self.dash_fetch._load_dash_data(self.vid, 0))
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
        from dash_stream import _dash_candidate_urls

        # Simulate Bilibili returning Akamai as primary base_url, with COS and M-CDN backups
        track = {
            "base_url": "https://upos-hz-mirrorakam.akamaized.net/track.m4s",
            "backup_url": [
                "https://d1.mcdn.bilivideo.cn/track.m4s",
                "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s",
            ],
        }
        candidates = _dash_candidate_urls(track)
        self.assertEqual(len(candidates), 3)
        # Direct high-speed CDN must be ranked first
        self.assertIn("mirrorcosov.bilivideo.com", candidates[0])
        # Overseas proxy mirror second
        self.assertIn("mirrorakam.akamaized.net", candidates[1])
        # PCDN/M-CDN last
        self.assertIn("mcdn.bilivideo.cn", candidates[2])

    async def test_peek_content_length_sends_range_zero_and_parses_content_range(self):
        from types import SimpleNamespace
        import dash_download_job

        sent_headers = {}

        async def fake_open(url, headers, proxy_url):
            sent_headers.update(headers)
            # EdgeOne returns 206 with Content-Range for Range: bytes=0-0
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


class PaginationVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_space_pagination_visibility(self):
        import filters  # noqa: F401
        from shared import app, render_template_with_theme

        uinfo = {"mid": 177291194, "name": "DECO27_Official", "sign": "", "face": ""}
        vlist = [{"bvid": f"BV{i}", "title": f"Video {i}", "pic": "", "duration": "3:00", "author": "DECO27", "mid": 177291194, "created": 1600000000, "play": 100} for i in range(28)]

        # Page 1 of 3 (count=69, ps=28)
        async with app.test_request_context("/space/177291194?i=1"):
            uvids = {"list": {"vlist": vlist}, "page": {"count": 69, "pn": 1, "ps": 28}}
            html1 = await render_template_with_theme("space.html", uinfo=uinfo, uvids=uvids, degraded=False, load_failed=False)
            self.assertIn("?i=2", html1)
            self.assertNotIn("?i=0", html1)

        # Page 2 of 3 (count=69, ps=28) -> Next page (?i=3) must exist
        async with app.test_request_context("/space/177291194?i=2"):
            uvids = {"list": {"vlist": vlist}, "page": {"count": 69, "pn": 2, "ps": 28}}
            html2 = await render_template_with_theme("space.html", uinfo=uinfo, uvids=uvids, degraded=False, load_failed=False)
            self.assertIn("?i=3", html2)
            self.assertIn("?i=1", html2)

        # Page 3 of 3 (count=69, ps=28) -> Last page, no next page (?i=4)
        async with app.test_request_context("/space/177291194?i=3"):
            uvids = {"list": {"vlist": vlist[:13]}, "page": {"count": 69, "pn": 3, "ps": 28}}
            html3 = await render_template_with_theme("space.html", uinfo=uinfo, uvids=uvids, degraded=False, load_failed=False)
            self.assertNotIn("?i=4", html3)
            self.assertIn("?i=2", html3)

    async def test_author_pagination_visibility(self):
        import filters  # noqa: F401
        from shared import app, render_template_with_theme

        uinfo = {"mid": 177291194, "name": "DECO27_Official", "sign": "", "face": ""}
        articles = [{"id": i, "title": f"Art {i}", "image_urls": [], "publish_time": 1600000000, "words": 100, "stats": {"view": 10, "reply": 1}, "author": {"mid": 177291194, "name": "DECO27"}} for i in range(28)]

        # Page 2 of 3 (count=69, ps=28) -> Next page (?i=3) must exist
        async with app.test_request_context("/author/177291194?i=2"):
            uarts = {"articles": articles, "count": 69, "pn": 2, "ps": 28}
            html = await render_template_with_theme("author.html", uinfo=uinfo, uarts=uarts)
            self.assertIn("?i=3", html)
            self.assertIn("?i=1", html)

    async def test_zone_pagination_visibility(self):
        import filters  # noqa: F401
        from shared import app, render_template_with_theme

        # Page 2 of 3 (count=25, size=10) -> Next page (?i=3) must exist
        async with app.test_request_context("/vv/1?i=2"):
            info = {
                "archives": [{"tid": 1, "bvid": f"BV{i}", "title": f"Video {i}", "pic": "", "duration": "3:00", "owner": {"name": "Owner"}, "stat": {"view": 10}, "pubdate": 1600000000} for i in range(10)],
                "page": {"count": 25, "num": 2, "size": 10},
            }
            html = await render_template_with_theme("zone.html", info=info)
            self.assertIn("?i=3", html)
            self.assertIn("?i=1", html)


if __name__ == "__main__":
    unittest.main()
