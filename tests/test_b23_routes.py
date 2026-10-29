import unittest
from unittest.mock import AsyncMock, patch

from app import app


class TestSearchAutoJump(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = app.test_client()

    async def test_search_b23_url_redirects_to_video(self):
        with patch(
            "views_home.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/video/BV1xx411c7xx"}),
        ) as mock_resolve:
            resp = await self.client.get("/search?q=https://b23.tv/abc123")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/video/BV1xx411c7xx")
            mock_resolve.assert_awaited_once_with("https://b23.tv/abc123")

    async def test_search_b23_url_redirects_to_bangumi(self):
        with patch(
            "views_home.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/bangumi/play/ep12345"}),
        ):
            resp = await self.client.get("/search?q=https://b23.tv/ep12345")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/bangumi/play/ep12345")

    async def test_search_b23_url_with_share_text_redirects(self):
        with patch(
            "views_home.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/video/BV1yy411c7yy"}),
        ):
            resp = await self.client.get(
                "/search",
                query_string={"q": "【2026最新混剪】 https://b23.tv/xyz789 赶紧来看！"},
            )
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/video/BV1yy411c7yy")

    async def test_search_normal_keyword_falls_through_to_search(self):
        mock_search_result = {
            "result": [],
            "numResults": 0,
            "numPages": 0,
        }
        with patch("api.search.search_by_type", new=AsyncMock(return_value=mock_search_result)):
            resp = await self.client.get("/search?q=normal_keyword")
            self.assertEqual(resp.status_code, 200)

    async def test_search_b23_failure_falls_through_to_search(self):
        mock_search_result = {
            "result": [],
            "numResults": 0,
            "numPages": 0,
        }
        with patch(
            "views_home.resolve_b23_target",
            new=AsyncMock(return_value={"status": "error", "error": "Failed to resolve shortlink: HTTP 404"}),
        ), patch("api.search.search_by_type", new=AsyncMock(return_value=mock_search_result)):
            resp = await self.client.get("/search?q=https://b23.tv/notfound123")
            self.assertEqual(resp.status_code, 200)


class TestRootB32tvRedirect(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = app.test_client()

    async def test_root_redirect_video(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/video/BV1xx411c7xx"}),
        ) as mock_resolve:
            resp = await self.client.get("/abc123")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/video/BV1xx411c7xx")
            mock_resolve.assert_awaited_once_with("https://b23.tv/abc123")

    async def test_root_redirect_bangumi_ep(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/bangumi/play/ep1234"}),
        ):
            resp = await self.client.get("/ep1234")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/bangumi/play/ep1234")

    async def test_root_redirect_bangumi_ss(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/bangumi/play/ss5678"}),
        ):
            resp = await self.client.get("/ss5678")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/bangumi/play/ss5678")

    async def test_root_redirect_live(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/live/12345"}),
        ):
            resp = await self.client.get("/live99")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/live/12345")

    async def test_root_redirect_opus(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/read/opus12345"}),
        ):
            resp = await self.client.get("/opus99")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/read/opus12345")

    async def test_root_redirect_read_cv(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/read/cv12345"}),
        ):
            resp = await self.client.get("/cv9999")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/read/cv12345")

    async def test_root_redirect_space(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "ok", "local_path": "/space/123456"}),
        ):
            resp = await self.client.get("/sp1234")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers.get("Location"), "/space/123456")

    async def test_root_redirect_resolution_failure_renders_error(self):
        with patch(
            "app.resolve_b23_target",
            new=AsyncMock(return_value={"status": "error", "error": "Page not found (HTTP 404)"}),
        ):
            resp = await self.client.get("/notfnd")
            self.assertEqual(resp.status_code, 404)

    async def test_root_redirect_invalid_format(self):
        resp = await self.client.get("/bad!chars")
        self.assertEqual(resp.status_code, 400)
