"""Run with PYTHONPATH=python .venv/bin/python -m unittest discover -s tests."""

import unittest
from unittest.mock import AsyncMock, patch

import views
from api import user
from api.exceptions import ResponseCodeException
from dash_proxy import _select_durl_results


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
                with (
                    patch.object(user, "Api", FakeApi),
                    patch.object(user.User, "get_user_info", AsyncMock(return_value={"name": "Test"})),
                    patch.object(views, "cache_minutes", return_value=0),
                    patch.object(views, "render_template_with_theme", render),
                ):
                    async with views.app.test_request_context("/space/123"):
                        response = await views.space_view("123")
                self.assertEqual(response.status_code, 200)
                context = render.await_args.kwargs
                self.assertEqual(context["load_failed"], load_failed)
                self.assertEqual(context["degraded"], degraded)
                self.assertEqual(len(context["uvids"]["list"]["vlist"]), count)
                self.assertEqual(response.headers.get("X-Degraded"), "risk-control" if degraded else None)
                self.assertEqual(fetch.await_count, len(responses))


class CommentPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_meta_more_returns_503_on_upstream_failure(self):
        with (
            patch("views.comment.get_comments", AsyncMock(side_effect=TimeoutError("upstream timeout"))),
            patch("views.render_template_with_theme", AsyncMock(return_value="rendered_error_comments")),
        ):
            async with views.app.test_request_context("/api/component/comments/BV1xx411c7xx/0/more?next=offset1"):
                result = await views.api_component_meta_more("BV1xx411c7xx", 0)
                # When failed, returns tuple (body, 503)
                self.assertIsInstance(result, tuple)
                self.assertEqual(result[1], 503)
                self.assertEqual(result[0], "rendered_error_comments")

    async def test_meta_more_returns_200_on_success(self):
        mock_comments = {
            "page": {"count": 1},
            "replies": [{"rpid": 123}],
            "next_offset": "offset2",
            "is_end": False,
        }
        with (
            patch("views.comment.get_comments", AsyncMock(return_value=mock_comments)),
            patch("views.render_template_with_theme", AsyncMock(return_value="rendered_comments")),
        ):
            async with views.app.test_request_context("/api/component/comments/BV1xx411c7xx/0/more?next=offset1"):
                result = await views.api_component_meta_more("BV1xx411c7xx", 0)
                # When successful, returns body directly
                self.assertEqual(result, "rendered_comments")


class DashProxyLogRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resumed_conn_skip_longer_than_idle_timeout_does_not_false_stall(self):
        import asyncio
        import dash_proxy

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

        resumed = dash_proxy._ResumedDashConn(FakeRawConn(), skip=5 * 10240 + 6, limit=5)
        with (
            patch.object(dash_proxy, "DASH_BODY_IDLE_TIMEOUT", 0.05),
            patch.object(dash_proxy, "DASH_BODY_GRACE", 0.04),
            patch.object(dash_proxy, "DASH_BODY_FLOOR_KBPS", 100),
        ):
            chunks = [c async for c in dash_proxy._yield_dash_body(resumed, "test", floor_watchdog=True)]
        self.assertEqual(b"".join(chunks), b"WORLD")

    async def test_resumed_conn_tarpit_during_skip_still_trips_floor_watchdog(self):
        import asyncio
        import dash_proxy

        class SlowRawConn:
            async def iter_chunks(self):
                for _ in range(5):
                    await asyncio.sleep(0.02)
                    yield b"x" * 10  # < 1 KB/s

        resumed = dash_proxy._ResumedDashConn(SlowRawConn(), skip=1000, limit=10)
        with (
            patch.object(dash_proxy, "DASH_BODY_IDLE_TIMEOUT", 0.5),
            patch.object(dash_proxy, "DASH_BODY_GRACE", 0.03),
            patch.object(dash_proxy, "DASH_BODY_FLOOR_KBPS", 100),
        ):
            with self.assertRaises(dash_proxy._SlowDashBody):
                _ = [c async for c in dash_proxy._yield_dash_body(resumed, "test", floor_watchdog=True)]

    async def test_failover_same_mirror_retry_when_pending_first_416s(self):
        from types import SimpleNamespace
        import dash_proxy

        dash_proxy._mirror_range_shy.clear()
        dash_proxy._mirror_health.clear()

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
        with patch.object(dash_proxy, "_fetch_dash_attempt", side_effect=fake_attempt):
            conn, used, resp = await dash_proxy._failover_dash_conn(
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
        self.assertTrue(dash_proxy._mirror_range_shy_at("upos-sz-mirrorcosov.bilivideo.com", 430980))

    async def test_fetch_dash_track_timeout_includes_host_in_error(self):
        import dash_proxy

        dash_proxy._mirror_health.clear()
        url = "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s"
        with patch.object(dash_proxy, "_fetch_dash_attempt", AsyncMock(side_effect=TimeoutError())):
            with self.assertRaises(dash_proxy._DashUpstreamError) as ctx:
                await dash_proxy._fetch_dash_track([url], {}, "", "BV1B5Jc6nEYW:0 video/80/12")
        self.assertIn("upos-sz-mirrorcosov.bilivideo.com stalled", str(ctx.exception))

    async def test_method_not_allowed_returns_405_instead_of_500(self):
        from app import app
        from quart.sessions import SecureCookieSessionInterface

        orig = app.session_interface
        app.session_interface = SecureCookieSessionInterface()
        try:
            client = app.test_client()
            resp = await client.post("/preferences")
            self.assertEqual(resp.status_code, 405)
        finally:
            app.session_interface = orig

    async def test_cancelled_video_view_leaves_no_unawaited_coroutines(self):
        import asyncio
        import gc
        import warnings

        async def cancel_gather(*coros, **kwargs):
            for c in coros:
                c.close()
            raise asyncio.CancelledError()

        with (
            patch.object(views, "cache_minutes", return_value=0),
            patch("views.asyncio.gather", side_effect=cancel_gather),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always", RuntimeWarning)
            async with views.app.test_request_context("/video/BV1xx411c7xx"):
                with self.assertRaises(asyncio.CancelledError):
                    await views.video_view("BV1xx411c7xx", 0)
            gc.collect()
        unawaited = [w for w in caught if "was never awaited" in str(w.message)]
        self.assertEqual(unawaited, [])


if __name__ == "__main__":
    unittest.main()
