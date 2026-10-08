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


if __name__ == "__main__":
    unittest.main()
