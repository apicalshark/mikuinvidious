# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.media.res as res
from miku.app import app
from miku.core.url_signing import sign_danmaku_url, sign_subtitle_list_url, sign_subtitle_vtt_url


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
            vtt_path = sign_subtitle_vtt_url("BV1xx411c7mD", 0, "zh-CN")
            vtt_sig = vtt_path.split("/")[4]
            async with app.test_request_context(vtt_path):
                resp = await res.subtitle_vtt_res("BV1xx411c7mD", "0", "zh-CN", sig=vtt_sig)

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
            vtt_path_2 = sign_subtitle_vtt_url("BV1xx411c7mD", 0, "zh-CN")
            vtt_sig_2 = vtt_path_2.split("/")[4]
            async with app.test_request_context(vtt_path_2):
                resp_unsafe = await res.subtitle_vtt_res("BV1xx411c7mD", "0", "zh-CN", sig=vtt_sig_2)

        self.assertEqual(resp_unsafe.status_code, 404)
        fake_client.get.assert_not_awaited()

        # 3. HTTP 200 -> converts BCC JSON to WebVTT
        async def ok_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b'{"body":[{"from":0,"to":1.5,"content":"hello"}]}'
            return resp

        fake_client.get = AsyncMock(side_effect=ok_get)
        res._subtitle_neg_cache.clear()
        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", AsyncMock(return_value=True)),
            patch.object(res, "make_video", return_value=fake_video),
            patch.object(res, "_is_safe_subtitle_url", AsyncMock(return_value=True)),
            patch.object(res.shared.Network, "get_async_client", AsyncMock(return_value=fake_client)),
        ):
            async with app.test_request_context(vtt_path_2):
                resp_ok = await res.subtitle_vtt_res("BV1xx411c7mD", "0", "zh-CN", sig=vtt_sig_2)

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
            dm_path = sign_danmaku_url("av170001", 0)
            dm_sig = dm_path.split("/")[4]
            async with app.test_request_context(dm_path):
                dm_resp = await res.danmaku_res("av170001", 0, sig=dm_sig)
            sub_path = sign_subtitle_list_url("av170001", 0)
            sub_sig = sub_path.split("/")[4]
            async with app.test_request_context(sub_path):
                sub_resp = await res.subtitle_list_res("av170001", 0, sig=sub_sig)

        self.assertEqual(dm_resp.status_code, 200)
        self.assertEqual(len(await dm_resp.get_json()), 1)
        self.assertEqual(sub_resp.status_code, 200)
        self.assertEqual((await sub_resp.get_json())[0]["lan"], "en-US")
        self.assertEqual(len(seen_calls), 2)
        self.assertEqual(seen_calls[0], {"bvid": None, "aid": 170001, "credential": fake_cred})
        self.assertEqual(seen_calls[1], {"bvid": None, "aid": 170001, "credential": fake_cred})

    async def test_unsigned_and_tampered_res_routes_return_403(self):
        client = app.test_client()
        mock_get = AsyncMock()
        with patch.object(res.appredis, "get", mock_get):
            for path in (
                "/res/danmaku/BV1xx411c7mD",
                "/res/danmaku/BV1xx411c7mD:0",
                "/res/danmaku/s/bad_sig_00000000000000/BV1xx411c7mD:0",
                "/res/subtitle/BV1xx411c7mD",
                "/res/subtitle/BV1xx411c7mD:0",
                "/res/subtitle/BV1xx411c7mD:0:zh-CN",
                "/res/subtitle/s/bad_sig_00000000000000/BV1xx411c7mD:0:zh-CN",
            ):
                resp = await client.get(path)
                self.assertEqual(resp.status_code, 403, path)
        mock_get.assert_not_awaited()

    async def test_subtitle_vtt_singleflight_and_404_negative_cache(self):
        res._subtitle_neg_cache.clear()
        fake_cred = MagicMock()
        fake_cred.sessdata = "s123"
        meta_calls = 0
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def slow_meta(page_index=0):
            nonlocal meta_calls
            meta_calls += 1
            started.set()
            await proceed.wait()
            return [{"lan": "zh-CN", "subtitle_url": "//aisubtitle.hdslb.com/ok.json"}]

        fake_video = MagicMock()
        fake_video.get_subtitle_meta = slow_meta

        async def ok_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b'{"body":[{"from":0,"to":1.0,"content":"hi"}]}'
            return resp

        fake_client = MagicMock()
        fake_client.get = AsyncMock(side_effect=ok_get)
        test_client = app.test_client()

        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", AsyncMock(return_value=True)),
            patch.object(res, "make_video", return_value=fake_video),
            patch.object(res, "_is_safe_subtitle_url", AsyncMock(return_value=True)),
            patch.object(res.shared.Network, "get_async_client", AsyncMock(return_value=fake_client)),
        ):
            vtt_url = sign_subtitle_vtt_url("BV1xx411c7mD", 0, "zh-CN")
            tasks = [asyncio.create_task(test_client.get(vtt_url)) for _ in range(4)]
            try:
                await asyncio.wait_for(started.wait(), timeout=2.0)
                loop = asyncio.get_running_loop()
                sf_key = "BV1xx411c7mD:0:zh-CN"

                async def wait_all_waiters():
                    while True:
                        entry = res._subtitle_vtt_sf._loops.get(loop, {}).get(sf_key)
                        if entry is not None and entry.waiters >= 4:
                            return
                        await asyncio.sleep(0)

                await asyncio.wait_for(wait_all_waiters(), timeout=2.0)
                proceed.set()
                resps = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)
            except BaseException:
                proceed.set()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        self.assertEqual(meta_calls, 1)
        for r in resps:
            self.assertEqual(r.status_code, 200)

        missing_calls = 0

        async def missing_meta(page_index=0):
            nonlocal missing_calls
            missing_calls += 1
            return []

        fake_video.get_subtitle_meta = missing_meta
        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res, "make_video", return_value=fake_video),
        ):
            miss_url = sign_subtitle_vtt_url("BV1xx411c7mD", 0, "fr-FR")
            r1 = await test_client.get(miss_url)
            r2 = await test_client.get(miss_url)
        self.assertEqual(r1.status_code, 404)
        self.assertEqual(r2.status_code, 404)
        self.assertEqual(missing_calls, 1)

    async def test_danmaku_negative_caches_clean_empty_but_not_upstream_412(self):
        from miku.api import video as video_api

        res._danmaku_neg_cache.clear()
        test_client = app.test_client()
        calls = 0

        def handler_412(req):
            nonlocal calls
            calls += 1
            import httpx

            return httpx.Response(412, text="Precondition Failed")

        def handler_empty(req):
            nonlocal calls
            calls += 1
            import httpx

            return httpx.Response(200, content=b"")

        import httpx

        client_412 = httpx.AsyncClient(transport=httpx.MockTransport(handler_412))
        client_empty = httpx.AsyncClient(transport=httpx.MockTransport(handler_empty))
        try:
            with (
                patch.object(res.appredis, "get", AsyncMock(return_value=None)),
                patch.object(video_api, "get_bili_client", AsyncMock(return_value=client_412)),
                patch.object(video_api, "_get_anonymous_cookies", AsyncMock(return_value={})),
                patch.object(video_api.Video, "_get_cid_by_index", AsyncMock(return_value=12345)),
            ):
                u_412 = sign_danmaku_url("BV1xx411c7mD", 0)
                r1 = await test_client.get(u_412)
                r2 = await test_client.get(u_412)
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(calls, 2)

            calls = 0
            with (
                patch.object(res.appredis, "get", AsyncMock(return_value=None)),
                patch.object(video_api, "get_bili_client", AsyncMock(return_value=client_empty)),
                patch.object(video_api, "_get_anonymous_cookies", AsyncMock(return_value={})),
                patch.object(video_api.Video, "_get_cid_by_index", AsyncMock(return_value=12346)),
            ):
                u_empty = sign_danmaku_url("BV1xx411c7mD", 1)
                r3 = await test_client.get(u_empty)
                r4 = await test_client.get(u_empty)
            self.assertEqual(r3.status_code, 200)
            self.assertEqual(r4.status_code, 200)
            self.assertEqual(calls, 1)
        finally:
            await client_412.aclose()
            await client_empty.aclose()

    async def test_subtitle_gaia_v_voucher_not_negative_cached(self):
        from types import SimpleNamespace

        from miku.api import video as video_api
        from miku.core.url_signing import sign_subtitle_list_url, sign_subtitle_vtt_url

        res._subtitle_neg_cache.clear()
        fake_cred = SimpleNamespace(sessdata="fake-sessdata")
        test_client = app.test_client()
        calls = 0

        async def v_voucher_playurl(self, api, params, label):
            nonlocal calls
            calls += 1
            return {"v_voucher": "voucher_123"}

        setex_mock = AsyncMock()
        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", setex_mock),
            patch.object(video_api.Video, "_get_cid_by_index", AsyncMock(return_value=12345)),
            patch.object(video_api.Video, "_request_playurl", v_voucher_playurl),
        ):
            r_list = await test_client.get(sign_subtitle_list_url("BV1xx411c7mD", 0))
            self.assertEqual(r_list.status_code, 200)
            self.assertEqual(await r_list.get_json(), [])
            setex_mock.assert_not_awaited()

            vtt_url = sign_subtitle_vtt_url("BV1xx411c7mD", 0, "zh-CN")
            r_vtt_1 = await test_client.get(vtt_url)
            r_vtt_2 = await test_client.get(vtt_url)

        self.assertEqual(r_vtt_1.status_code, 502)
        self.assertEqual(r_vtt_2.status_code, 502)
        self.assertEqual(calls, 3)

    async def test_unsigned_res_routes_reject_before_rate_limiter(self):
        import miku.core.rate_limit as rate_limit

        limiter = MagicMock()
        limiter.is_allowed = AsyncMock(return_value=(False, {"remaining": 0, "reset": 1, "limit": 10}))
        test_client = app.test_client()
        with (
            patch.dict(rate_limit.appconf["rate_limit"], {"enabled": True}),
            patch.object(rate_limit, "get_rate_limiter", return_value=limiter),
        ):
            for path in (
                "/res/danmaku/BV1xx411c7mD",
                "/res/danmaku/s/0000000000000000/BV1xx411c7mD:0",
                "/res/subtitle/BV1xx411c7mD",
                "/res/subtitle/s/0000000000000000/BV1xx411c7mD:0",
                "/res/subtitle/BV1xx411c7mD:0:zh-CN",
                "/res/subtitle/s/0000000000000000/BV1xx411c7mD:0:zh-CN",
            ):
                r = await test_client.get(path)
                self.assertEqual(r.status_code, 403, path)
        limiter.is_allowed.assert_not_awaited()

    async def test_empty_subtitle_meta_and_html_404_block_page_not_negative_cached(self):
        from types import SimpleNamespace

        from miku.api import video as video_api

        res._subtitle_neg_cache.clear()
        fake_cred = SimpleNamespace(sessdata="fake-sessdata")
        test_client = app.test_client()
        setex_mock = AsyncMock()
        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", setex_mock),
            patch.object(video_api.Video, "_get_cid_by_index", AsyncMock(return_value=12345)),
            patch.object(video_api.Video, "_request_playurl", AsyncMock(return_value=None)),
        ):
            vtt_empty = sign_subtitle_vtt_url("BV1xx411c7mD", 0, "ja-JP")
            r1 = await test_client.get(vtt_empty)
            self.assertEqual(r1.status_code, 502)
            self.assertIsNone(res._subtitle_neg_cache.get("BV1xx411c7mD:0:ja-JP"))
            r_list = await test_client.get(sign_subtitle_list_url("BV1xx411c7mD", 0))
            self.assertEqual(r_list.status_code, 200)
            setex_mock.assert_not_awaited()

        block_resp = MagicMock()
        block_resp.status_code = 404
        block_resp.headers = {"content-type": "text/html; charset=utf-8"}
        block_resp.content = b"<html><body>blocked</body></html>"
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=block_resp)
        fake_video = MagicMock()
        fake_video.get_subtitle_meta = AsyncMock(
            return_value=[{"lan": "ko-KR", "subtitle_url": "//aisubtitle.hdslb.com/ko.json"}]
        )
        with (
            patch.object(res.shared, "appcred", fake_cred),
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res, "make_video", return_value=fake_video),
            patch.object(res, "_is_safe_subtitle_url", AsyncMock(return_value=True)),
            patch.object(res.shared.Network, "get_async_client", AsyncMock(return_value=fake_client)),
        ):
            r_block = await test_client.get(sign_subtitle_vtt_url("BV1xx411c7mD", 0, "ko-KR"))
            self.assertEqual(r_block.status_code, 502)
            self.assertIsNone(res._subtitle_neg_cache.get("BV1xx411c7mD:0:ko-KR"))

    async def test_res_rechecks_positive_cache_inside_singleflight(self):
        xml = '<i><d p="1.0,1,25,16777215,0,0,0,0">cached</d></i>'
        reads = [None, xml.encode("utf-8")]
        fake_video = MagicMock()
        fake_video.get_danmaku_xml = AsyncMock(return_value="")
        with (
            patch.object(res.appredis, "get", AsyncMock(side_effect=reads)),
            patch.object(res, "make_video", return_value=fake_video),
        ):
            u = sign_danmaku_url("BV1xx411c7mD", 4)
            sig = u.split("/")[4]
            async with app.test_request_context(u):
                resp = await res.danmaku_res("BV1xx411c7mD", 4, sig=sig)
        self.assertEqual(resp.status_code, 200)
        data = await resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["text"], "cached")
        fake_video.get_danmaku_xml.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
