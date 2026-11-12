import asyncio
import unittest

import httpx
import miku.media.proxy as proxy
from miku.api.live import LiveDanmaku
from miku.app import app
from miku.core.csrf import CSRF_TOKEN_KEY
from miku.dash.download_job import create_download_job
from miku.state import Network, appconf, detect_theme


class TestThemeSecurity(unittest.IsolatedAsyncioTestCase):
    async def test_theme_directory_traversal_prevention(self):
        """Verify that directory traversal strings in theme parameters fall back to 'modern'."""
        async with app.test_request_context("/?theme=.."):
            self.assertEqual(detect_theme(), "modern")

        async with app.test_request_context("/?theme=../themes/modern"):
            self.assertEqual(detect_theme(), "modern")

        async with app.test_request_context("/?theme=.%2e"):
            self.assertEqual(detect_theme(), "modern")

        async with app.test_request_context("/?theme=modern"):
            self.assertEqual(detect_theme(), "modern")

    async def test_general_exception_view_fallback(self):
        """Verify that general_exception_view recovers cleanly without recursive crash."""
        client = app.test_client()
        resp = await client.get("/?theme=..")
        # Should not crash the server (returns 200 homepage with modern theme or 500 error page)
        self.assertIn(resp.status_code, (200, 500))


class TestProxyRedirectSecurity(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_to_loopback_blocked(self):
        """Verify that image proxy redirects to loopback addresses are blocked with 403."""
        def mock_handler(request: httpx.Request):
            if request.url.host == "i0.hdslb.com":
                return httpx.Response(302, headers={"Location": "http://127.0.0.1:8080/internal.png"})
            elif request.url.host == "127.0.0.1":
                return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNGfake")
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport)
        Network.get_image_client = lambda: asyncio.sleep(0, result=mock_client)

        resp = await proxy.render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/face/test.jpg")
        self.assertEqual(resp.status_code, 403)
        body = await resp.get_data()
        self.assertIn(b"Forbidden", body)
        await mock_client.aclose()

    async def test_redirect_exceeding_max_hops_blocked(self):
        """Verify that redirect loops exceeding 3 hops are rejected with 403."""
        def mock_loop(request: httpx.Request):
            # Infinite redirect loop
            return httpx.Response(302, headers={"Location": f"{request.url.scheme}://{request.url.host}{request.url.path}1"})

        transport = httpx.MockTransport(mock_loop)
        mock_client = httpx.AsyncClient(transport=transport)
        Network.get_image_client = lambda: asyncio.sleep(0, result=mock_client)

        orig_safe = proxy.is_safe_proxy_url
        proxy.is_safe_proxy_url = lambda url: asyncio.sleep(0, result=True)
        try:
            resp = await proxy.render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/face/loop.jpg")
            self.assertEqual(resp.status_code, 403)
            body = await resp.get_data()
            self.assertIn(b"Too many redirects", body)
        finally:
            proxy.is_safe_proxy_url = orig_safe
            await mock_client.aclose()


class TestLiveDanmakuTaskLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_danmaku_tasks_pruned_on_completion(self):
        """Verify that LiveDanmaku task references are pruned when handlers complete."""
        dm = LiveDanmaku(99999)
        self.assertIsInstance(dm._tasks, set)

        @dm.on("MSG")
        async def handler(data):
            await asyncio.sleep(0.01)

        for i in range(5):
            dm.dispatch("MSG", {"i": i})

        self.assertEqual(len(dm._tasks), 5)
        await asyncio.sleep(0.05)
        self.assertEqual(len(dm._tasks), 0)

    async def test_danmaku_cleanup_cancels_pending_tasks(self):
        """Verify that LiveDanmaku._cleanup cancels any running tasks and empties the set."""
        dm = LiveDanmaku(99999)

        @dm.on("SLOW")
        async def slow_handler(data):
            await asyncio.sleep(10)

        dm.dispatch("SLOW", {})
        self.assertEqual(len(dm._tasks), 1)
        await dm._cleanup()
        self.assertEqual(len(dm._tasks), 0)


class TestDownloadAccessControl(unittest.IsolatedAsyncioTestCase):
    async def test_site_allow_download_enforced(self):
        """Verify that site_allow_download=False rejects download requests across all entrypoints."""
        client = app.test_client()
        tok = "c" * 64
        async with client.session_transaction() as sess:
            sess[CSRF_TOKEN_KEY] = tok

        orig_conf = appconf.get("site_allow_download", True)
        try:
            appconf["site_allow_download"] = False

            # 1. POST /download
            resp = await client.post(
                "/download",
                headers={"X-CSRF-Token": tok},
                form={"id": "BV1xx411c7mD", "cvid": "1", "qual": "80"},
            )
            self.assertEqual(resp.status_code, 403)

            # 2. GET /proxy/download/...
            resp2 = await client.get("/proxy/download/BV1xx411c7mD/1/80")
            self.assertEqual(resp2.status_code, 403)

            # 3. Direct create_download_job
            with self.assertRaises(RuntimeError) as ctx:
                await create_download_job("BV1xx411c7mD", 1, 80)
            self.assertIn("Downloads are disabled", str(ctx.exception))
        finally:
            appconf["site_allow_download"] = orig_conf


class TestSetLangRedirectSanitization(unittest.IsolatedAsyncioTestCase):
    async def test_external_referer_redirects_to_preferences(self):
        """Verify that external referers are sanitized to safe relative paths."""
        client = app.test_client()
        tok = "d" * 64
        async with client.session_transaction() as sess:
            sess[CSRF_TOKEN_KEY] = tok

        # External referer
        resp = await client.post(
            "/set_lang",
            headers={
                "Referer": "https://evil.attacker.com/phish?lang=zh-CN",
                "X-CSRF-Token": tok,
            },
            form={"lang": "zh-CN"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers.get("Location"), "/preferences")

        # Scheme-relative referer
        resp2 = await client.post(
            "/set_lang",
            headers={
                "Referer": "//evil.attacker.com/phish?lang=zh-CN",
                "X-CSRF-Token": tok,
            },
            form={"lang": "zh-CN"},
        )
        self.assertEqual(resp2.status_code, 302)
        self.assertEqual(resp2.headers.get("Location"), "/preferences")

        # Legitimate relative referer
        resp3 = await client.post(
            "/set_lang",
            headers={
                "Referer": "/video/BV1xx411c7mD?param=test&lang=zh-CN",
                "X-CSRF-Token": tok,
            },
            form={"lang": "zh-CN"},
        )
        self.assertEqual(resp3.status_code, 302)
        self.assertEqual(resp3.headers.get("Location"), "/video/BV1xx411c7mD?param=test")


if __name__ == "__main__":
    unittest.main()
