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
"""Tests for trusted proxy CIDR parsing and client-IP traversal."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import miku.core.rate_limit as rate_limit
from miku.core.config import get_default_config
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


class TestMappedTrustedNetworks(unittest.TestCase):
    def test_mapped_prefixes_preserve_range(self):
        nets = rate_limit._parse_trusted_networks(
            "::ffff:10.0.0.5/128,::ffff:192.168.1.5/120,::ffff:0:0/96,::/80,::1/128,127.0.0.0/8"
        )
        self.assertEqual(
            tuple(map(str, nets)), ("10.0.0.5/32", "192.168.1.0/24", "0.0.0.0/0", "::/80", "::1/128", "127.0.0.0/8")
        )


if __name__ == "__main__":
    unittest.main()
