import unittest
from unittest.mock import AsyncMock, MagicMock

from miku.media.stream import CdnConnection, CdnProtocolError, _http_connect_tunnel


def _make_connected_conn(url: str, headers: dict | None = None) -> tuple[CdnConnection, MagicMock]:
    conn = CdnConnection(url, headers=headers or {})
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    conn._connected = True
    conn._writer = writer
    return conn, writer


class TestCdnProtocolValidation(unittest.IsolatedAsyncioTestCase):
    async def test_send_request_rejects_crlf_nul_and_spaces_in_path(self):
        for bad_path in (
            "/video.mp4\r\nX-Injected: 1",
            "/video.mp4\nHost: evil",
            "/video\x00.mp4",
            "/video bad.mp4",
        ):
            with self.subTest(bad_path=bad_path):
                conn, writer = _make_connected_conn(f"https://cdn.example.com{bad_path}")
                with self.assertRaises(CdnProtocolError):
                    await conn.send_request()
                writer.write.assert_not_called()

    async def test_send_request_rejects_invalid_header_names_and_values(self):
        for bad_name in ("x-bad:name", "x bad", "x\rname", "x\nname", "x\x00name", ""):
            with self.subTest(bad_name=bad_name):
                conn, writer = _make_connected_conn(
                    "https://cdn.example.com/video.mp4",
                    headers={bad_name: "ok"},
                )
                with self.assertRaises(CdnProtocolError):
                    await conn.send_request()
                writer.write.assert_not_called()

        for bad_val in ("abc\r\nInjected: 1", "abc\ndef", "abc\x00def"):
            with self.subTest(bad_val=bad_val):
                conn, writer = _make_connected_conn(
                    "https://cdn.example.com/video.mp4",
                    headers={"x-test": bad_val},
                )
                with self.assertRaises(CdnProtocolError):
                    await conn.send_request()
                writer.write.assert_not_called()

    async def test_send_request_allows_spaces_and_colons_in_header_values(self):
        conn, writer = _make_connected_conn(
            "https://cdn.example.com/video.mp4?a=1",
            headers={"referer": "https://www.bilibili.com/", "user-agent": "Mozilla/5.0 Test"},
        )
        await conn.send_request()
        writer.write.assert_called_once()

    async def test_http_connect_tunnel_rejects_control_chars_and_whitespace(self):
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        reader = MagicMock()

        for bad_host in ("evil.com\r\nHost: x", "evil.com\nhost", "evil\x00.com", "evil .com", ""):
            with self.subTest(bad_host=bad_host):
                with self.assertRaises(CdnProtocolError):
                    await _http_connect_tunnel(writer, reader, bad_host, 443, 5.0)
                writer.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
