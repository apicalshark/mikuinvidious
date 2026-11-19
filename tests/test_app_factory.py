# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
import unittest

from miku.core.app import create_app
from miku.core.config import get_default_config
from quart.sessions import SecureCookieSessionInterface


class TestAppFactory(unittest.TestCase):
    def test_default_app_creation(self):
        conf = get_default_config()
        app = create_app(conf)
        self.assertIsNotNone(app)
        self.assertEqual(app.name, "miku")
        self.assertIsInstance(app.session_interface, SecureCookieSessionInterface)
        self.assertTrue(len(app.secret_key) > 0)
        self.assertEqual(app.config["RESPONSE_TIMEOUT"], 10800)
        self.assertEqual(app.config["BODY_TIMEOUT"], 30)
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 1 * 1024 * 1024)
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        import os

        self.assertTrue(os.path.isdir(app.template_folder))
        self.assertTrue(os.path.isdir(app.static_folder))
        self.assertTrue(os.path.isdir(os.path.join(app.template_folder, "themes", "modern")))

    def test_custom_config_override(self):
        conf = get_default_config()
        conf["server"]["debug"] = True
        conf["server"]["secret_key"] = "test_custom_secret"
        conf["quart"]["CUSTOM_SETTING"] = "hello_world"

        app = create_app(conf)
        self.assertTrue(app.debug)
        self.assertEqual(app.secret_key, "test_custom_secret")
        self.assertEqual(app.config["CUSTOM_SETTING"], "hello_world")

    def test_session_cookie_secure_https(self):
        conf = get_default_config()
        conf["site"]["site_url"] = "https://example.com"
        app = create_app(conf)
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

        conf["site"]["site_url"] = "http://example.com"
        app_http = create_app(conf)
        self.assertFalse(app_http.config["SESSION_COOKIE_SECURE"])


class TestSecurityHeaders(unittest.IsolatedAsyncioTestCase):
    async def test_csp_and_coop_headers(self):
        from miku.app import app

        client = app.test_client()
        resp = await client.get("/preferences")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Cross-Origin-Opener-Policy"), "same-origin")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        csp = resp.headers.get("Content-Security-Policy", "")
        self.assertIn("connect-src 'self' blob:", csp)
        self.assertNotIn("wss:", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    async def test_chunked_multipart_exceeding_max_content_length_returns_413(self):
        from quart import request

        conf = get_default_config()
        conf["quart"] = {"MAX_CONTENT_LENGTH": 256}
        app = create_app(conf)

        @app.route("/upload", methods=["POST"])
        async def upload():
            await request.form
            return "ok"

        boundary = "----mikuBoundary"
        payload = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="big.bin"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n" + ("A" * 1024) + f"\r\n--{boundary}--\r\n"
        ).encode()

        chunks = [payload[i : i + 128] for i in range(0, len(payload), 128)]
        sent: list[dict] = []

        async def receive():
            if chunks:
                body = chunks.pop(0)
                return {"type": "http.request", "body": body, "more_body": bool(chunks)}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/upload",
            "raw_path": b"/upload",
            "query_string": b"",
            "headers": [
                (b"host", b"localhost"),
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                (b"transfer-encoding", b"chunked"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("localhost", 80),
        }

        await app.asgi_app(scope, receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        self.assertEqual(start["status"], 413)


if __name__ == "__main__":
    unittest.main()
