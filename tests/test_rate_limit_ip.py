# Copyright (C) 2026 MikuInvidious Team
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
"""Tests for trusted proxy CIDR parsing, XFF traversal, SSRF helpers, config, credentials, and HTTP client lifecycle."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import miku.core.dns_cache as dns_cache
import miku.core.rate_limit as rate_limit
import miku.state as shared
from miku.api.credential import Credential
from miku.core.config import downloads_allowed, get_default_config
from miku.services.credential import apply_cdn_cred_headers, get_web_api_cookies, is_cred_active
from miku.services.network import Network, close_global_client
from werkzeug.datastructures import Headers


def _req(remote_addr: str | None, headers: Headers | dict[str, str] | None = None):
    return SimpleNamespace(remote_addr=remote_addr, headers=headers if headers is not None else {})


class TestRateLimitClientIP(unittest.TestCase):
    def test_default_trusted_proxies_is_loopback_only(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRUSTED_PROXIES", None)
            conf = get_default_config()
            self.assertEqual(conf["rate_limit"]["trusted_proxies"], "127.0.0.1/8,::1/128")

            os.environ["TRUSTED_PROXIES"] = ""
            conf_empty = get_default_config()
            self.assertEqual(conf_empty["rate_limit"]["trusted_proxies"], "")

    def test_untrusted_private_and_public_peers_ignore_forwarded_headers(self):
        with patch.dict(rate_limit.appconf["rate_limit"], {"trusted_proxies": "127.0.0.1/8,::1/128"}):
            for peer in ("10.0.0.5", "172.17.0.2", "192.168.1.10", "198.51.100.25"):
                req = _req(peer, {"X-Forwarded-For": "203.0.113.99", "X-Real-IP": "203.0.113.88"})
                self.assertEqual(rate_limit.get_client_ip(req), peer)

    def test_configured_cidr_trusts_dual_stack_ipv4_mapped_peer(self):
        with patch.dict(rate_limit.appconf["rate_limit"], {"trusted_proxies": "10.0.0.0/8"}):
            req = _req("::ffff:10.0.0.5", {"X-Forwarded-For": "203.0.113.42"})
            self.assertEqual(rate_limit.get_client_ip(req), "203.0.113.42")

            untrusted_req = _req("::ffff:192.168.1.2", {"X-Forwarded-For": "203.0.113.42"})
            self.assertEqual(rate_limit.get_client_ip(untrusted_req), "192.168.1.2")

    def test_right_to_left_xff_ignores_spoofed_left_prefix(self):
        with patch.dict(rate_limit.appconf["rate_limit"], {"trusted_proxies": "127.0.0.1/8"}):
            req = _req("127.0.0.1", {"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
            self.assertEqual(rate_limit.get_client_ip(req), "203.0.113.7")

    def test_multi_hop_trusted_chain_unwraps_to_first_untrusted_client(self):
        with patch.dict(rate_limit.appconf["rate_limit"], {"trusted_proxies": "127.0.0.1/8,10.20.0.0/16"}):
            req = _req("127.0.0.1", {"X-Forwarded-For": "8.8.8.8, 198.51.100.12, 10.20.1.5"})
            self.assertEqual(rate_limit.get_client_ip(req), "198.51.100.12")

            all_trusted = _req("127.0.0.1", {"X-Forwarded-For": "10.20.1.1, 10.20.1.2"})
            self.assertEqual(rate_limit.get_client_ip(all_trusted), "10.20.1.1")

    def test_malformed_rightmost_xff_stops_traversal(self):
        with patch.dict(rate_limit.appconf["rate_limit"], {"trusted_proxies": "127.0.0.1/8"}):
            req = _req("127.0.0.1", {"X-Forwarded-For": "203.0.113.9, not-an-ip"})
            self.assertEqual(rate_limit.get_client_ip(req), "127.0.0.1")

            # Trusted rightmost hop followed by malformed entry stops and returns last trusted hop.
            req2 = _req("127.0.0.1", {"X-Forwarded-For": "203.0.113.9, bad, 127.0.0.2"})
            self.assertEqual(rate_limit.get_client_ip(req2), "127.0.0.2")

    def test_multiple_xff_headers_folded_right_to_left(self):
        headers = Headers([("X-Forwarded-For", "198.51.100.1"), ("X-Forwarded-For", "203.0.113.5, 10.0.0.2")])
        with patch.dict(rate_limit.appconf["rate_limit"], {"trusted_proxies": "127.0.0.1/8,10.0.0.0/8"}):
            req = _req("127.0.0.1", headers)
            self.assertEqual(rate_limit.get_client_ip(req), "203.0.113.5")

    def test_x_real_ip_fallback_when_xff_absent(self):
        with patch.dict(rate_limit.appconf["rate_limit"], {"trusted_proxies": "127.0.0.1/8"}):
            req = _req("127.0.0.1", {"X-Real-IP": "::ffff:198.51.100.44"})
            self.assertEqual(rate_limit.get_client_ip(req), "198.51.100.44")

            bad_real_ip = _req("127.0.0.1", {"X-Real-IP": "garbage"})
            self.assertEqual(rate_limit.get_client_ip(bad_real_ip), "127.0.0.1")


class TestSSRFHelpers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dns_cache.clear_cache()

    async def asyncTearDown(self):
        dns_cache.clear_cache()

    def test_is_public_ip_rejects_private_special_multicast_and_mapped(self):
        blocked = [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "::",
            "fe80::1",
            "fc00::1",
            "ff02::1",
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
            "::ffff:10.0.0.1",
            "::ffff:224.0.0.1",
            "not-an-ip",
        ]
        for addr in blocked:
            self.assertFalse(dns_cache.is_public_ip(addr), f"expected {addr} to be blocked")

        for addr in ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"):
            self.assertTrue(dns_cache.is_public_ip(addr), f"expected {addr} to be public")

    def test_is_allowed_url_host(self):
        domains = (".hdslb.com", "bilivideo.com")
        self.assertEqual(dns_cache.is_allowed_url_host("https://i0.hdslb.com./pic.jpg", domains), "i0.hdslb.com")
        self.assertEqual(dns_cache.is_allowed_url_host("http://bilivideo.com/a.m4s", domains), "bilivideo.com")

        for bad in (
            "ftp://i0.hdslb.com/x",
            "file:///etc/passwd",
            "https://user@i0.hdslb.com/x",
            "https://user:pass@i0.hdslb.com/x",
            "https://i0.hdslb.com/a\r\nb",
            "https://evil.com/x",
            "https://hdslb.com.evil.com/x",
            "",
        ):
            self.assertIsNone(dns_cache.is_allowed_url_host(bad, domains), f"expected {bad!r} to fail")

    async def test_is_safe_public_url_cached_and_uncached(self):
        domains = (".bilivideo.com",)
        public_info = [(2, 1, 6, "", ("1.1.1.1", 0))]
        private_info = [(2, 1, 6, "", ("::ffff:127.0.0.1", 0))]

        with patch.object(dns_cache.socket, "getaddrinfo", return_value=public_info) as mock_gai:
            self.assertTrue(
                await dns_cache.is_safe_public_url("https://upos-sz.bilivideo.com/x.m4s", domains, use_dns_cache=True)
            )
            self.assertTrue(
                await dns_cache.is_safe_public_url("https://upos-sz.bilivideo.com/x.m4s", domains, use_dns_cache=True)
            )
            self.assertEqual(mock_gai.call_count, 1)

            self.assertTrue(
                await dns_cache.is_safe_public_url("https://upos-sz.bilivideo.com/x.m4s", domains, use_dns_cache=False)
            )
            self.assertEqual(mock_gai.call_count, 2)

        with patch.object(dns_cache.socket, "getaddrinfo", return_value=private_info):
            self.assertFalse(
                await dns_cache.is_safe_public_url("https://upos-sz.bilivideo.com/x.m4s", domains, use_dns_cache=False)
            )


class TestDownloadsAllowedAndNetworkAndCredHelpers(unittest.IsolatedAsyncioTestCase):
    def test_downloads_allowed_variants(self):
        self.assertTrue(downloads_allowed({"site": {"site_allow_download": True}}))
        self.assertFalse(downloads_allowed({"site": {"site_allow_download": False}}))
        self.assertTrue(downloads_allowed({"site": {"site_allow_download": "true"}}))
        self.assertTrue(downloads_allowed({"site": {"site_allow_download": " True "}}))
        self.assertFalse(downloads_allowed({"site": {"site_allow_download": "false"}}))
        self.assertFalse(downloads_allowed({"site": {"site_allow_download": "0"}}))
        self.assertTrue(downloads_allowed({}))

        with patch.dict(os.environ, {"SITE_ALLOW_DOWNLOAD": "false"}):
            conf = get_default_config()
            self.assertFalse(downloads_allowed(conf))

        with patch.dict(shared.appconf["site"], {"site_allow_download": False}):
            self.assertFalse(shared.downloads_allowed())

    async def test_close_global_client_closes_all_three_and_resets_none(self):
        async_client = MagicMock()
        async_client.is_closed = False
        async_client.aclose = AsyncMock(side_effect=RuntimeError("async close failed"))

        image_client = MagicMock()
        image_client.is_closed = False
        image_client.aclose = AsyncMock()

        sync_client = MagicMock()
        sync_client.is_closed = False
        sync_client.close = MagicMock()

        Network._async_client = async_client
        Network._image_client = image_client
        Network._sync_client = sync_client
        try:
            await close_global_client()
            async_client.aclose.assert_awaited_once()
            image_client.aclose.assert_awaited_once()
            sync_client.close.assert_called_once()
            self.assertIsNone(Network._async_client)
            self.assertIsNone(Network._image_client)
            self.assertIsNone(Network._sync_client)

            # Second invocation is a safe no-op
            await close_global_client()
            image_client.aclose.assert_awaited_once()
            sync_client.close.assert_called_once()
        finally:
            Network._async_client = None
            Network._image_client = None
            Network._sync_client = None

    def test_credential_helpers_ticket_isolation_and_use_cred_guard(self):
        cred = Credential(
            sessdata="sess%20val",
            bili_jct="jct_val",
            buvid3="b3_val",
            buvid4="b4_val",
            dedeuserid="12345",
            ac_time_value="ac_val",
            bili_ticket="leak_ticket",
            bili_ticket_expires="9999999999",
        )
        disabled_conf = {"credential": {"use_cred": False}}
        enabled_conf = {"credential": {"use_cred": True}}

        self.assertFalse(is_cred_active(None, conf=enabled_conf))
        self.assertFalse(is_cred_active(cred, conf=disabled_conf))
        self.assertTrue(is_cred_active(cred, conf=enabled_conf))

        self.assertEqual(get_web_api_cookies(cred, conf=disabled_conf), {})
        web_cookies = get_web_api_cookies(cred, conf=enabled_conf)
        self.assertNotIn("bili_ticket", web_cookies)
        self.assertNotIn("bili_ticket_expires", web_cookies)
        self.assertEqual(web_cookies["SESSDATA"], "sess%20val")
        self.assertEqual(web_cookies["DedeUserID"], "12345")

        headers = {"User-Agent": "Chrome", "cookie": "stale=1"}
        apply_cdn_cred_headers(headers, cred, ticket="jwt_ticket", conf=enabled_conf)
        self.assertEqual(headers["x-bili-ticket"], "jwt_ticket")
        self.assertEqual(headers["buvid"], "b3_val")
        self.assertEqual(headers["buvid4"], "b4_val")
        self.assertNotIn("cookie", headers)
        self.assertNotIn("bili_ticket=", headers["Cookie"])
        self.assertIn("SESSDATA=sess%20val", headers["Cookie"])
        self.assertIn("DedeUserID=12345", headers["Cookie"])

        inactive_headers = {"User-Agent": "Chrome"}
        apply_cdn_cred_headers(inactive_headers, cred, ticket="jwt_ticket", conf=disabled_conf)
        self.assertEqual(inactive_headers["x-bili-ticket"], "jwt_ticket")
        self.assertNotIn("Cookie", inactive_headers)
        self.assertNotIn("buvid", inactive_headers)


if __name__ == "__main__":
    unittest.main()
