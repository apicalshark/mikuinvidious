# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from miku.app import app
from miku.core.url_signing import sign_live_hls_url, verify_live_hls_url
from miku.media.proxy import _rewrite_hls_playlist


class _FakeCdnConn:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.closed = False

    async def iter_chunks(self):
        for c in self._chunks:
            yield c

    async def close(self):
        self.closed = True


class TestLiveHlsUrlSigning(unittest.TestCase):
    def test_sign_and_verify_roundtrip(self):
        target = "https://d1--ov-gotcha207.bilivideo.com/live-bvc/123/index.m3u8?expires=999"
        signed = sign_live_hls_url(target)
        self.assertTrue(signed.startswith("/proxy/live_hls?"))
        qs = parse_qs(urlparse(signed).query)
        self.assertEqual(qs["u"][0], target)
        sig = qs["sig"][0]
        self.assertTrue(verify_live_hls_url(sig, target))
        self.assertFalse(verify_live_hls_url(sig, target + "&tampered=1"))
        self.assertFalse(verify_live_hls_url("bad-sig", target))
        self.assertFalse(verify_live_hls_url(sig, None))
        self.assertFalse(verify_live_hls_url(None, target))


class TestLiveHlsPlaylistRewrite(unittest.IsolatedAsyncioTestCase):
    async def test_rewrite_hls_playlist_rewrites_segments_and_uri_attrs(self):
        base = "https://d1--ov-gotcha207.bilivideo.com/live-bvc/525756/index.m3u8?token=abc"
        playlist = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:7\n"
            '#EXT-X-MAP:URI="h12345.m4s?sig=1"\n'
            '#EXT-X-KEY:METHOD=AES-128,URI="https://d1--ov-gotcha207.bilivideo.com/key.bin"\n'
            "#EXTINF:2.000,\n"
            "seg001.m4s?foo=bar\n"
            "#EXTINF:2.000,\n"
            "https://d1--ov-gotcha207.bilivideo.com/live-bvc/525756/seg002.m4s\n"
            "#EXTINF:2.000,\n"
            "http://127.0.0.1/evil.m4s\n"
        )

        async def fake_safe(url: str) -> bool:
            return urlparse(url).hostname == "d1--ov-gotcha207.bilivideo.com"

        with patch("miku.media.proxy.is_safe_proxy_url", side_effect=fake_safe):
            rewritten = await _rewrite_hls_playlist(playlist, base)

        lines = rewritten.splitlines()
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertIn("/proxy/live_hls?sig=", lines[2])
        self.assertIn("h12345.m4s", lines[2])
        self.assertIn("/proxy/live_hls?sig=", lines[3])
        self.assertIn("key.bin", lines[3])
        self.assertTrue(lines[5].startswith("/proxy/live_hls?sig="))
        self.assertTrue(lines[7].startswith("/proxy/live_hls?sig="))
        self.assertEqual(lines[9], "http://127.0.0.1/evil.m4s")


class TestLiveHlsProxyRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_live_hls_follows_redirect_and_rewrites_playlist(self):
        initial_url = "https://live.bilibili.com/xlive/play.m3u8?room=1"
        cdn_url = "https://d1--ov-gotcha207.bilivideo.com/live-bvc/525756/index.m3u8?expires=1795091193"
        playlist_body = b'#EXTM3U\n#EXT-X-MAP:URI="init.m4s"\n#EXTINF:1.0,\nseg1.m4s\n'

        conn1 = _FakeCdnConn([])
        rh1 = SimpleNamespace(status_code=302, headers={"location": cdn_url})

        conn2 = _FakeCdnConn([playlist_body])
        rh2 = SimpleNamespace(status_code=200, headers={"content-type": "application/vnd.apple.mpegurl"})

        client = app.test_client()
        with (
            patch("miku.media.proxy.appredis.get", new=AsyncMock(return_value=initial_url)),
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.media.proxy.TicketManager.get_ticket", new=AsyncMock(return_value="tkt")),
            patch("miku.media.proxy._establish_cdn_connection", new=AsyncMock(return_value=(conn1, rh1))),
            patch("miku.media.proxy._cdn_handshake", new=AsyncMock(return_value=(conn2, rh2))),
        ):
            resp = await client.get("/proxy/live/12345?format=hls")
            data = await resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Content-Type"), "application/vnd.apple.mpegurl")
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertTrue(conn1.closed)
        self.assertTrue(conn2.closed)
        self.assertIn("/proxy/live_hls?sig=", data)
        for line in data.splitlines():
            if line and not line.startswith("#"):
                self.assertTrue(line.startswith("/proxy/live_hls?sig="))

    async def test_proxy_live_hls_endpoint_rejects_bad_sig_and_unsafe_url(self):
        client = app.test_client()
        resp_bad = await client.get("/proxy/live_hls?sig=invalid&u=https://d1--ov-gotcha207.bilivideo.com/a.m4s")
        self.assertEqual(resp_bad.status_code, 403)

        signed = sign_live_hls_url("http://127.0.0.1/secret.m4s")
        with patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=False)):
            resp_unsafe = await client.get(signed)
        self.assertEqual(resp_unsafe.status_code, 403)

    async def test_proxy_live_hls_endpoint_streams_segment(self):
        seg_url = "https://d1--ov-gotcha207.bilivideo.com/live-bvc/525756/seg1.m4s"
        signed = sign_live_hls_url(seg_url)
        conn = _FakeCdnConn([b"\x00\x00\x00\x18ftyp", b"moofdata"])
        rh = SimpleNamespace(
            status_code=200,
            headers={"content-type": "application/octet-stream", "content-length": "16"},
        )

        client = app.test_client()
        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.media.proxy.TicketManager.get_ticket", new=AsyncMock(return_value="tkt")),
            patch("miku.media.proxy._establish_cdn_connection", new=AsyncMock(return_value=(conn, rh))),
        ):
            resp = await client.get(signed)
            body = await resp.get_data()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Content-Type"), "video/mp4")
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(body, b"\x00\x00\x00\x18ftypmoofdata")
        self.assertTrue(conn.closed)
