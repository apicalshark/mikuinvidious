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
"""Unit tests for user space, article reader, and pagination routes."""

import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.views as views
import miku.views.space as views_space
from miku.api import user
from miku.api.exceptions import ResponseCodeException
from miku.state import Network, app, render_template_with_theme


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
                    def result(self, fetch=fetch):
                        return fetch()

                render = AsyncMock(return_value="space page")

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


class PaginationVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_space_pagination_visibility(self):
        import miku.ui.filters as filters  # noqa: F401

        uinfo = {"mid": 177291194, "name": "DECO27_Official", "sign": "", "face": ""}
        vlist = [
            {
                "bvid": f"BV{i}",
                "title": f"Video {i}",
                "pic": "",
                "duration": "3:00",
                "author": "DECO27",
                "mid": 177291194,
                "created": 1600000000,
                "play": 100,
            }
            for i in range(28)
        ]

        # Page 1 of 3 (count=69, ps=28)
        async with app.test_request_context("/space/177291194?i=1"):
            uvids = {"list": {"vlist": vlist}, "page": {"count": 69, "pn": 1, "ps": 28}}
            html1 = await render_template_with_theme(
                "space.html", uinfo=uinfo, uvids=uvids, degraded=False, load_failed=False
            )
            self.assertIn("?i=2", html1)
            self.assertNotIn("?i=0", html1)

        # Page 2 of 3 (count=69, ps=28) -> Next page (?i=3) must exist
        async with app.test_request_context("/space/177291194?i=2"):
            uvids = {"list": {"vlist": vlist}, "page": {"count": 69, "pn": 2, "ps": 28}}
            html2 = await render_template_with_theme(
                "space.html", uinfo=uinfo, uvids=uvids, degraded=False, load_failed=False
            )
            self.assertIn("?i=3", html2)
            self.assertIn("?i=1", html2)

        # Page 3 of 3 (count=69, ps=28) -> Next page (?i=4) must NOT exist
        async with app.test_request_context("/space/177291194?i=3"):
            uvids = {"list": {"vlist": vlist[:13]}, "page": {"count": 69, "pn": 3, "ps": 28}}
            html3 = await render_template_with_theme(
                "space.html", uinfo=uinfo, uvids=uvids, degraded=False, load_failed=False
            )
            self.assertNotIn("?i=4", html3)
            self.assertIn("?i=2", html3)

    async def test_author_pagination_visibility(self):
        import miku.ui.filters as filters  # noqa: F401

        uinfo = {"mid": 177291194, "name": "DECO27_Official", "sign": "", "face": ""}
        articles = [
            {
                "id": i,
                "title": f"Art {i}",
                "image_urls": [],
                "publish_time": 1600000000,
                "words": 100,
                "stats": {"view": 10, "reply": 1},
                "author": {"mid": 177291194, "name": "DECO27"},
            }
            for i in range(28)
        ]

        # Page 2 of 3 (count=69, ps=28) -> Next page (?i=3) must exist
        async with app.test_request_context("/author/177291194?i=2"):
            uarts = {"articles": articles, "count": 69, "pn": 2, "ps": 28}
            html = await render_template_with_theme("author.html", uinfo=uinfo, uarts=uarts)
            self.assertIn("?i=3", html)
            self.assertIn("?i=1", html)

    async def test_zone_pagination_visibility(self):
        import miku.ui.filters as filters  # noqa: F401

        # Page 2 of 3 (count=25, size=10) -> Next page (?i=3) must exist
        async with app.test_request_context("/vv/1?i=2"):
            info = {
                "archives": [
                    {
                        "tid": 1,
                        "bvid": f"BV{i}",
                        "title": f"Video {i}",
                        "pic": "",
                        "duration": "3:00",
                        "owner": {"name": "Owner"},
                        "stat": {"view": 10},
                        "pubdate": 1600000000,
                    }
                    for i in range(10)
                ],
                "page": {"count": 25, "num": 2, "size": 10},
            }
            html = await render_template_with_theme("zone.html", info=info)
            self.assertIn("?i=3", html)
            self.assertIn("?i=1", html)


class ArticleViewLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_view_logs_on_status_failure(self):
        mock_resp = AsyncMock()
        mock_resp.status_code = 412
        mock_resp.text = "Risk control"
        mock_resp.aclose = AsyncMock()

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)

        log_out = io.StringIO()
        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch("asyncio.sleep", AsyncMock()),
            patch("sys.stdout", log_out),
        ):
            async with app.test_request_context("/read/cv27188030"):
                resp = await views_space.read_view("cv27188030")

        self.assertEqual(resp[1], 502)
        logs = log_out.getvalue()
        self.assertIn("[Article] Attempt 1/3 for cv27188030", logs)
        self.assertIn("returned HTTP 412", logs)
        self.assertIn("[Article] Failed to fetch article cv27188030", logs)
        self.assertIn("last_status=412", logs)

    async def test_read_view_logs_and_fast_exits_on_404(self):
        mock_resp = AsyncMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.aclose = AsyncMock()

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)

        log_out = io.StringIO()
        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch("asyncio.sleep", AsyncMock()),
            patch("sys.stdout", log_out),
        ):
            async with app.test_request_context("/read/cv99999999"):
                resp = await views_space.read_view("cv99999999")

        self.assertEqual(resp[1], 404)
        self.assertEqual(mock_client.send.call_count, 1)
        logs = log_out.getvalue()
        self.assertIn("[Article] cv99999999", logs)
        self.assertIn("returned HTTP 404 Not Found", logs)

    async def test_read_view_logs_on_unparseable_content(self):
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Empty stripped anti-bot response</body></html>"
        mock_resp.aclose = AsyncMock()

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)

        log_out = io.StringIO()
        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch("asyncio.sleep", AsyncMock()),
            patch("sys.stdout", log_out),
        ):
            async with app.test_request_context("/read/cv27188030"):
                resp = await views_space.read_view("cv27188030")

        self.assertEqual(resp[1], 502)
        logs = log_out.getvalue()
        self.assertIn("[Article] Attempt 1/3 for cv27188030", logs)
        self.assertIn("stripped or unparseable content", logs)
        self.assertIn("[Article] Failed to fetch article cv27188030", logs)

    async def test_read_view_logs_on_network_error(self):
        mock_client = AsyncMock()
        mock_client.build_request = MagicMock()
        mock_client.send = AsyncMock(side_effect=OSError("Connection reset by peer"))

        log_out = io.StringIO()
        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch("asyncio.sleep", AsyncMock()),
            patch("sys.stdout", log_out),
        ):
            async with app.test_request_context("/read/cv27188030"):
                resp = await views_space.read_view("cv27188030")

        self.assertEqual(resp[1], 502)
        logs = log_out.getvalue()
        self.assertIn("[Article] Attempt 1/3 for cv27188030", logs)
        self.assertIn("network error", logs)
        self.assertIn("Connection reset by peer", logs)
        self.assertIn("[Article] Failed to fetch article cv27188030", logs)


if __name__ == "__main__":
    unittest.main()
