import socket
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.media.res as res
from miku.app import app


class TestRes(unittest.IsolatedAsyncioTestCase):
    async def test_is_safe_subtitle_url_ssrf_checks(self):
        # 1. HTTP scheme and non-allowlisted domains rejected before DNS
        self.assertFalse(await res._is_safe_subtitle_url("http://aisubtitle.hdslb.com/sub.json"))
        self.assertFalse(await res._is_safe_subtitle_url("https://evil.example.com/sub.json"))

        # 2. Private DNS result rejected even for allowlisted domain (fresh DNS resolution)
        with patch(
            "miku.core.dns_cache.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
        ):
            self.assertFalse(await res._is_safe_subtitle_url("https://aisubtitle.hdslb.com/sub.json"))

        with patch(
            "miku.core.dns_cache.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))],
        ):
            self.assertFalse(await res._is_safe_subtitle_url("https://aisubtitle.hdslb.com/sub.json"))

        # 3. Public DNS result accepted for https + allowlisted suffix
        with patch(
            "miku.core.dns_cache.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0))],
        ):
            self.assertTrue(await res._is_safe_subtitle_url("https://aisubtitle.hdslb.com/sub.json"))

    async def test_subtitle_vtt_res_status_ssrf_and_follow_redirects_false(self):
        fake_cred = MagicMock()
        fake_cred.sessdata = "s123"

        fake_video = MagicMock()
        fake_video.get_subtitle_meta = AsyncMock(
            return_value=[{"lan": "zh-CN", "subtitle_url": "//aisubtitle.hdslb.com/sub.json"}]
        )

        # 1. Non-200 status -> 502, verify follow_redirects=False and timeout=15.0
        seen_kwargs = {}

        async def fake_get(url, **kwargs):
            seen_kwargs.update(kwargs)
            resp = MagicMock()
            resp.status_code = 302
            resp.content = b""
            return resp

        fake_client = MagicMock()
        fake_client.get = AsyncMock(side_effect=fake_get)

        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res, "make_video", return_value=fake_video),
            patch.object(res, "_is_safe_subtitle_url", AsyncMock(return_value=True)),
            patch.object(res.shared.Network, "get_async_client", AsyncMock(return_value=fake_client)),
        ):
            async with app.test_request_context("/res/subtitle/BV1xx411c7mD:0:zh-CN"):
                resp = await res.subtitle_vtt_res("BV1xx411c7mD", "0", "zh-CN")

        self.assertEqual(resp.status_code, 502)
        self.assertFalse(seen_kwargs.get("follow_redirects"))
        self.assertEqual(seen_kwargs.get("timeout"), 15.0)

        # 2. Unsafe subtitle URL -> 404 before client.get is ever called
        fake_client.get.reset_mock()
        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res, "make_video", return_value=fake_video),
            patch.object(res, "_is_safe_subtitle_url", AsyncMock(return_value=False)),
            patch.object(res.shared.Network, "get_async_client", AsyncMock(return_value=fake_client)),
        ):
            async with app.test_request_context("/res/subtitle/BV1xx411c7mD:0:zh-CN"):
                resp_unsafe = await res.subtitle_vtt_res("BV1xx411c7mD", "0", "zh-CN")

        self.assertEqual(resp_unsafe.status_code, 404)
        fake_client.get.assert_not_awaited()

        # 3. HTTP 200 -> converts BCC JSON to WebVTT
        async def ok_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b'{"body":[{"from":0,"to":1.5,"content":"hello"}]}'
            return resp

        fake_client.get = AsyncMock(side_effect=ok_get)
        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", AsyncMock(return_value=True)),
            patch.object(res, "make_video", return_value=fake_video),
            patch.object(res, "_is_safe_subtitle_url", AsyncMock(return_value=True)),
            patch.object(res.shared.Network, "get_async_client", AsyncMock(return_value=fake_client)),
        ):
            async with app.test_request_context("/res/subtitle/BV1xx411c7mD:0:zh-CN"):
                resp_ok = await res.subtitle_vtt_res("BV1xx411c7mD", "0", "zh-CN")

        self.assertEqual(resp_ok.status_code, 200)
        body = (await resp_ok.get_data()).decode()
        self.assertTrue(body.startswith("WEBVTT"))
        self.assertIn("hello", body)

    async def test_av_id_supported_on_danmaku_and_subtitle_list(self):
        fake_cred = MagicMock()
        fake_cred.sessdata = "s123"
        seen_calls = []

        class _FakeVideo:
            def __init__(self, bvid=None, aid=None, credential=None):
                seen_calls.append({"bvid": bvid, "aid": aid, "credential": credential})

            async def get_danmaku_xml(self, idx):
                return '<i><d p="1.0,1,25,16777215,0,0,0,0">miku</d></i>'

            async def get_subtitle_meta(self, page_index=0):
                return [{"lan": "en-US", "lan_doc": "English", "subtitle_url": "//aisubtitle.hdslb.com/s.json"}]

        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", AsyncMock(return_value=True)),
            patch.object(res.video, "Video", _FakeVideo),
        ):
            async with app.test_request_context("/res/danmaku/av170001"):
                dm_resp = await res.danmaku_res("av170001", 0)
            async with app.test_request_context("/res/subtitle/av170001"):
                sub_resp = await res.subtitle_list_res("av170001", 0)

        self.assertEqual(dm_resp.status_code, 200)
        self.assertEqual(len(await dm_resp.get_json()), 1)
        self.assertEqual(sub_resp.status_code, 200)
        self.assertEqual((await sub_resp.get_json())[0]["lan"], "en-US")
        self.assertEqual(len(seen_calls), 2)
        self.assertEqual(seen_calls[0], {"bvid": None, "aid": 170001, "credential": fake_cred})
        self.assertEqual(seen_calls[1], {"bvid": None, "aid": 170001, "credential": fake_cred})


if __name__ == "__main__":
    unittest.main()
