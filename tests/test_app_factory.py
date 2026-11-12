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
        self.assertEqual(app.config["BODY_TIMEOUT"], 10800)
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


if __name__ == "__main__":
    unittest.main()
