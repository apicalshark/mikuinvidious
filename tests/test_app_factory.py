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


if __name__ == "__main__":
    unittest.main()
