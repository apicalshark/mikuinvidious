# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
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
            patch.object(views_space, "_resolve_cv_opus_id", AsyncMock(return_value=None)),
            patch("asyncio.sleep", AsyncMock()),
            patch("sys.stdout", log_out),
        ):
            async with app.test_request_context("/read/cv27188030"):
                resp = await views_space.read_view("cv27188030")

        self.assertEqual(resp[1], 502)
        logs = log_out.getvalue()
        self.assertIn("[Article] Attempt 1/5 for cv27188030", logs)
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
            patch.object(views_space, "_resolve_cv_opus_id", AsyncMock(return_value=None)),
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
            patch.object(views_space, "_resolve_cv_opus_id", AsyncMock(return_value=None)),
            patch("asyncio.sleep", AsyncMock()),
            patch("sys.stdout", log_out),
        ):
            async with app.test_request_context("/read/cv27188030"):
                resp = await views_space.read_view("cv27188030")

        self.assertEqual(resp[1], 502)
        logs = log_out.getvalue()
        self.assertIn("[Article] Attempt 1/5 for cv27188030", logs)
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
            patch.object(views_space, "_resolve_cv_opus_id", AsyncMock(return_value=None)),
            patch("asyncio.sleep", AsyncMock()),
            patch("sys.stdout", log_out),
        ):
            async with app.test_request_context("/read/cv27188030"):
                resp = await views_space.read_view("cv27188030")

        self.assertEqual(resp[1], 502)
        logs = log_out.getvalue()
        self.assertIn("[Article] Attempt 1/5 for cv27188030", logs)
        self.assertIn("network error", logs)
        self.assertIn("Connection reset by peer", logs)
        self.assertIn("[Article] Failed to fetch article cv27188030", logs)

    async def test_read_view_sends_buvid3_cookie(self):
        mock_resp = AsyncMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.aclose = AsyncMock()

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)

        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(
                views_space, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "TEST-BUVID3-infoc"})
            ),
        ):
            async with app.test_request_context("/read/cv12576864"):
                resp = await views_space.read_view("cv12576864")

        self.assertEqual(resp[1], 404)
        _, kwargs = mock_client.build_request.call_args
        self.assertIn("buvid3=TEST-BUVID3-infoc", kwargs["headers"].get("Cookie", ""))

    async def test_read_view_unwraps_cv_opus_dict_mapping(self):
        async def fake_cache_get(key, _ttl):
            if key == "cv:opus:12576864":
                return {"opus_id": "99887766"}
            return None

        mock_client = AsyncMock()
        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=10),
            patch.object(views_space, "cache_get", side_effect=fake_cache_get),
        ):
            async with app.test_request_context("/read/cv12576864"):
                resp = await views_space.read_view("cv12576864")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers.get("Location"), "/opus/99887766")
        mock_client.send.assert_not_awaited()

    async def test_read_view_preresolves_cv_to_opus_before_scrape(self):
        api_resp_509 = AsyncMock()
        api_resp_509.status_code = 200
        api_resp_509.text = '{"code": -509, "message": "too many requests"}'
        api_resp_509.aclose = AsyncMock()

        api_resp_ok = AsyncMock()
        api_resp_ok.status_code = 200
        api_resp_ok.text = '{"code": 0, "data": {"dyn_id_str": "428546721494533554"}}'
        api_resp_ok.aclose = AsyncMock()

        mock_client = AsyncMock()
        requested_urls = []

        def fake_build_request(method, target_url, **kwargs):
            requested_urls.append(target_url)
            return MagicMock()

        mock_client.build_request = MagicMock(side_effect=fake_build_request)
        mock_client.send = AsyncMock(side_effect=[api_resp_509, api_resp_ok])

        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space, "get_wbi_mixin_key", AsyncMock(return_value="0123456789abcdef0123456789abcdef")),
            patch("asyncio.sleep", AsyncMock()),
        ):
            async with app.test_request_context("/read/cv7376731"):
                resp = await views_space.read_view("cv7376731")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers.get("Location"), "/opus/428546721494533554")
        self.assertEqual(len(requested_urls), 2)
        self.assertTrue(requested_urls[0].startswith("https://api.bilibili.com/x/article/view?id=7376731"))
        self.assertIn("gaia_source=main_web", requested_urls[0])
        self.assertIn("web_location=333.976", requested_urls[0])
        self.assertIn("w_rid=", requested_urls[0])

    async def test_opus_view_falls_back_to_opus_json_api_when_html_scrape_fails(self):
        opus_payload = {
            "item": {
                "basic": {"title": "Opus Title", "comment_id_str": "7376731", "comment_type": 12, "rid_str": "7376731"},
                "modules": [
                    {"module_type": "MODULE_TYPE_TITLE", "module_title": {"text": "Opus Title"}},
                    {
                        "module_type": "MODULE_TYPE_AUTHOR",
                        "module_author": {"mid": 519487206, "name": "Tester", "face": "", "pub_ts": "1598617209"},
                    },
                    {
                        "module_type": "MODULE_TYPE_CONTENT",
                        "module_content": {
                            "paragraphs": [
                                {
                                    "para_type": 1,
                                    "text": {
                                        "nodes": [
                                            {
                                                "type": "TEXT_NODE_TYPE_WORD",
                                                "word": {"words": "Hello Opus JSON", "color": "var(--Cy4, #4FD3D1)"},
                                            }
                                        ]
                                    },
                                }
                            ]
                        },
                    },
                ],
            }
        }
        viewinfo_payload = {
            "stats": {"view": 5392},
            "image_urls": ["https://i0.hdslb.com/bfs/article/banner/top.png"],
        }
        mock_resp = AsyncMock()
        mock_resp.status_code = 412
        mock_resp.text = ""
        mock_resp.aclose = AsyncMock()

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        render = AsyncMock(return_value="json rendered")
        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch("asyncio.sleep", AsyncMock()),
            patch.object(
                views_space.opus,
                "Opus",
                return_value=MagicMock(get_info=AsyncMock(return_value=opus_payload)),
            ),
            patch.object(
                views_space.article,
                "Article",
                return_value=MagicMock(get_info=AsyncMock(return_value=viewinfo_payload)),
            ),
            patch.object(
                views_space.user,
                "User",
                return_value=MagicMock(get_user_info=AsyncMock(return_value={})),
            ),
            patch.object(views_space, "_fetch_article_comments", AsyncMock(return_value=({}, "7376731", 12, False))),
            patch.object(views_space, "render_template_with_theme", render),
        ):
            async with app.test_request_context("/opus/428546721494533554"):
                html = await views_space.read_view("428546721494533554")

        self.assertEqual(html, "json rendered")
        mock_client.send.assert_awaited()
        self.assertEqual(
            mock_client.build_request.call_args[0][1],
            "https://www.bilibili.com/opus/428546721494533554",
        )
        self.assertIn("Hello Opus JSON", render.await_args.kwargs["article_content"])
        self.assertIn("#4FD3D1", render.await_args.kwargs["article_content"])
        self.assertEqual(
            render.await_args.kwargs["arinfo"]["covers"],
            ["/proxy/pic/i0.hdslb.com/bfs/article/banner/top.png"],
        )
        self.assertEqual(render.await_args.kwargs["arinfo"]["stats"]["view"], 5392)

    async def test_opus_view_scrapes_opus_url_with_cookie(self):
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html></html>"
        mock_resp.url = "https://www.bilibili.com/opus/99887766"
        mock_resp.aclose = AsyncMock()

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)

        render = AsyncMock(return_value="rendered")
        with (
            patch.object(Network, "get_async_client", AsyncMock(return_value=mock_client)),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space, "article_to_html", return_value="<p>ok</p>"),
            patch.object(views_space, "get_article_info", return_value={"title": "T", "stats": {"view": 1}}),
            patch.object(views_space.opus, "Opus", return_value=MagicMock(get_info=AsyncMock(return_value={}))),
            patch.object(views_space, "_fetch_article_comments", AsyncMock(return_value=({}, "", 12, False))),
            patch.object(views_space, "render_template_with_theme", render),
        ):
            async with app.test_request_context("/opus/99887766"):
                html = await views_space.read_view("99887766")

        self.assertEqual(html, "rendered")
        _, req_kwargs = mock_client.build_request.call_args
        self.assertEqual(mock_client.build_request.call_args[0][1], "https://www.bilibili.com/opus/99887766")
        self.assertIn("Cookie", req_kwargs["headers"])
        self.assertEqual(render.await_args.kwargs["opus_id"], "99887766")

    async def test_author_view_enriches_articles_with_opus_links(self):
        import miku.ui.filters as filters  # noqa: F401

        uinfo = {"mid": 519487206, "name": "Tester", "sign": "", "face": "https://i0.hdslb.com/bfs/face/a.jpg"}
        uarts = {
            "articles": [
                {
                    "id": 7376731,
                    "title": "Sample Article",
                    "image_urls": ["https://i0.hdslb.com/bfs/article/a.png"],
                    "publish_time": 1600000000,
                    "stats": {"view": 100},
                    "author": {"mid": 519487206, "name": "Tester"},
                }
            ],
            "count": 1,
            "pn": 1,
            "ps": 20,
        }
        opus_feed = {
            "items": [{"opus_id": "428546721494533554", "content": "Sample Article"}],
            "offset": "",
            "has_more": False,
        }
        with (
            patch.object(user.User, "get_user_info", AsyncMock(return_value=uinfo)),
            patch.object(user.User, "get_articles", AsyncMock(return_value=uarts)),
            patch.object(user.User, "get_opus_articles", AsyncMock(return_value=opus_feed)),
            patch.object(views_space, "cache_minutes", return_value=0),
        ):
            async with app.test_request_context("/author/519487206"):
                html = await views_space.author_view("519487206")

        self.assertIn("/opus/428546721494533554", html)
        self.assertIn("https://www.bilibili.com/opus/428546721494533554", html)
        self.assertNotIn("/read/opus", html)
        self.assertNotIn("/read/cv7376731", html)

    async def test_read_rejects_read_opus_alias_with_404(self):
        async with app.test_request_context("/read/opus733063343227535376"):
            resp = await views_space.read_view("opus733063343227535376")
        self.assertEqual(resp[1], 404)

        async with app.test_request_context("/opus/opus733063343227535376"):
            resp_bad_opus = await views_space.read_view("opus733063343227535376")
        self.assertEqual(resp_bad_opus[1], 404)


if __name__ == "__main__":
    unittest.main()
