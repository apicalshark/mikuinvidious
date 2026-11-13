import re
import unittest
from unittest.mock import AsyncMock, patch

from miku.app import app


class TestConvertView(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = app.test_client()

    async def _get_csrf_token(self) -> str:
        resp = await self.client.get("/convert")
        self.assertEqual(resp.status_code, 200)
        text = await resp.get_data(as_text=True)
        match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', text)
        self.assertIsNotNone(match)
        return match.group(1)

    async def test_get_convert_page(self):
        resp = await self.client.get("/convert")
        self.assertEqual(resp.status_code, 200)
        text = await resp.get_data(as_text=True)
        self.assertIn("<form", text.lower())
        self.assertIn("convert", text.lower())

    async def test_post_convert_form_success(self):
        token = await self._get_csrf_token()
        mock_result = {
            "status": "ok",
            "original": "https://b23.tv/abc123",
            "target_url": "https://www.bilibili.com/video/BV1xx411c7xx",
            "clean_url": "https://www.bilibili.com/video/BV1xx411c7xx",
            "local_path": "/video/BV1xx411c7xx",
            "type": "video",
            "id": "BV1xx411c7xx",
            "error": None,
        }
        with patch("miku.views.home.resolve_b23_target", new=AsyncMock(return_value=mock_result)):
            resp = await self.client.post("/convert", form={"url": "https://b23.tv/abc123", "csrf_token": token})
            self.assertEqual(resp.status_code, 200)
            text = await resp.get_data(as_text=True)
            self.assertIn("https://www.bilibili.com/video/BV1xx411c7xx", text)
            self.assertIn("/video/BV1xx411c7xx", text)

    async def test_post_convert_form_empty_or_invalid(self):
        token = await self._get_csrf_token()
        mock_result = {
            "status": "error",
            "original": "invalid-link",
            "target_url": "",
            "clean_url": "",
            "local_path": "",
            "type": "",
            "id": "",
            "error": "No valid b23.tv or Bilibili URL found in input",
        }
        with patch("miku.views.home.resolve_b23_target", new=AsyncMock(return_value=mock_result)):
            resp = await self.client.post("/convert", form={"url": "invalid-link", "csrf_token": token})
            self.assertEqual(resp.status_code, 200)
            text = await resp.get_data(as_text=True)
            self.assertIn("No valid b23.tv or Bilibili URL found in input", text)

    async def test_post_convert_form_missing_csrf_rejected(self):
        resp = await self.client.post("/convert", form={"url": "https://b23.tv/abc123"})
        self.assertEqual(resp.status_code, 403)

    async def test_post_convert_form_mismatched_csrf_rejected(self):
        await self._get_csrf_token()
        resp = await self.client.post("/convert", form={"url": "https://b23.tv/abc123", "csrf_token": "wrong-token"})
        self.assertEqual(resp.status_code, 403)

    async def test_post_api_convert_success(self):
        token = await self._get_csrf_token()
        mock_result = {
            "status": "ok",
            "original": "https://b23.tv/abc123",
            "target_url": "https://www.bilibili.com/video/BV1xx411c7xx",
            "clean_url": "https://www.bilibili.com/video/BV1xx411c7xx",
            "local_path": "/video/BV1xx411c7xx",
            "type": "video",
            "id": "BV1xx411c7xx",
            "error": None,
        }
        with patch("miku.views.home.resolve_b23_target", new=AsyncMock(return_value=mock_result)):
            resp = await self.client.post(
                "/api/convert",
                headers={"X-CSRF-Token": token},
                json={"url": "https://b23.tv/abc123"},
            )
            self.assertEqual(resp.status_code, 200)
            data = await resp.get_json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["clean_url"], "https://www.bilibili.com/video/BV1xx411c7xx")
            self.assertEqual(data["local_path"], "/video/BV1xx411c7xx")
            self.assertEqual(data["type"], "video")
            self.assertEqual(data["id"], "BV1xx411c7xx")

    async def test_post_api_convert_missing_csrf_rejected(self):
        resp = await self.client.post("/api/convert", json={"url": "https://b23.tv/abc123"})
        self.assertEqual(resp.status_code, 403)

    async def test_post_api_convert_mismatched_csrf_rejected(self):
        await self._get_csrf_token()
        resp = await self.client.post(
            "/api/convert",
            headers={"X-CSRF-Token": "wrong-token"},
            json={"url": "https://b23.tv/abc123"},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_post_api_convert_missing_url(self):
        token = await self._get_csrf_token()
        resp = await self.client.post("/api/convert", headers={"X-CSRF-Token": token}, json={"invalid_key": "abc"})
        self.assertEqual(resp.status_code, 400)
        data = await resp.get_json()
        self.assertEqual(data["status"], "error")
        self.assertIn("error", data)

    async def test_post_api_convert_empty_url(self):
        token = await self._get_csrf_token()
        resp = await self.client.post("/api/convert", headers={"X-CSRF-Token": token}, json={"url": "   "})
        self.assertEqual(resp.status_code, 400)
        data = await resp.get_json()
        self.assertEqual(data["status"], "error")

    async def test_post_api_convert_invalid_url(self):
        token = await self._get_csrf_token()
        mock_result = {
            "status": "error",
            "original": "not-a-valid-url",
            "target_url": "",
            "clean_url": "",
            "local_path": "",
            "type": "",
            "id": "",
            "error": "No valid b23.tv or Bilibili URL found in input",
        }
        with patch("miku.views.home.resolve_b23_target", new=AsyncMock(return_value=mock_result)):
            resp = await self.client.post(
                "/api/convert",
                headers={"X-CSRF-Token": token},
                json={"url": "not-a-valid-url"},
            )
            self.assertEqual(resp.status_code, 400)
            data = await resp.get_json()
            self.assertEqual(data["status"], "error")
            self.assertEqual(data["error"], "No valid b23.tv or Bilibili URL found in input")

    async def test_footer_link_present(self):
        with patch("miku.views.home.homepage.get_videos", new=AsyncMock(return_value=[])):
            resp_en = await self.client.get("/?lang=en")
            self.assertEqual(resp_en.status_code, 200)
            text_en = await resp_en.get_data(as_text=True)
            self.assertIn('<a href="/convert"', text_en)
            self.assertIn("Link converter", text_en)

            resp_zh = await self.client.get("/?lang=zh-CN")
            self.assertEqual(resp_zh.status_code, 200)
            text_zh = await resp_zh.get_data(as_text=True)
            self.assertIn('<a href="/convert"', text_zh)
            self.assertIn("链接转换器", text_zh)
