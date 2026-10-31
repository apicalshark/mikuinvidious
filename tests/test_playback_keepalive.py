"""Playback keep-alive pool + shared TLS context.

The pool banks a CDN socket after an exactly-consumed length-framed body so the
next fragment of the same rendition skips the TCP/TLS handshake (measured
45-51 ms of a 177 ms 256 KiB fragment). These tests pin the safety rules:
bank only boundary-exact bodies, never resurrect a demoted mirror, survive a
stale socket by reconnecting, and bound the idle set.
"""

import asyncio
import ssl
import unittest
from unittest.mock import AsyncMock, patch

import dash_stream
import stream


class _Server:
    """Tiny Range-speaking origin that counts accepted TCP connections."""

    def __init__(self, *, declare_connection_close: bool = False):
        self.accepted = 0
        self.requests = []
        self.declare_connection_close = declare_connection_close
        self._server = None
        self.port = 0

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/track.m4s"

    async def close(self):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        self.accepted += 1
        try:
            while True:
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = await reader.read(4096)
                    if not chunk:
                        return
                    head += chunk
                lines = head.split(b"\r\n")
                self.requests.append(lines[0].decode())
                rng = ""
                for line in lines[1:]:
                    name, _, value = line.partition(b":")
                    if name.strip().lower() == b"range":
                        rng = value.decode().strip()
                start, end = 0, 99
                if rng.startswith("bytes="):
                    lo, _, hi = rng[6:].partition("-")
                    start = int(lo)
                    end = int(hi) if hi else start + 99
                body = bytes((i % 251) for i in range(start, end + 1))
                extra = b"Connection: close\r\n" if self.declare_connection_close else b""
                writer.write(
                    b"HTTP/1.1 206 Partial Content\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + f"Content-Range: bytes {start}-{end}/100000\r\n".encode()
                    + b"Content-Type: video/mp4\r\n"
                    + extra
                    + b"\r\n"
                    + body
                )
                await writer.drain()
                if self.declare_connection_close:
                    return
        except Exception:
            return
        finally:
            writer.close()


class SharedTlsContextTests(unittest.IsolatedAsyncioTestCase):
    def test_context_is_built_once(self):
        self.assertIsInstance(stream._TLS_CONTEXT, ssl.SSLContext)

    async def test_connect_uses_the_shared_context(self):
        """A per-connection create_default_context() cost 3.9 ms of CA parsing."""
        conn = stream.CdnConnection("https://i0.hdslb.com/a.jpg")
        writer = AsyncMock()
        open_connection = AsyncMock(return_value=(AsyncMock(), writer))
        with (
            patch("stream.asyncio.open_connection", open_connection),
            patch("stream.ssl.create_default_context", side_effect=AssertionError("must not rebuild")),
        ):
            await conn.connect()
        kwargs = open_connection.await_args.kwargs
        self.assertIs(kwargs["ssl"], stream._TLS_CONTEXT)


class BankableTests(unittest.IsolatedAsyncioTestCase):
    """`_dash_conn_bankable` is the boundary-exactness gate."""

    def _headers(self, status=206, length="100", connection=None):
        resp = stream.CdnResponse()
        resp.status_code = status
        resp.headers = {"content-length": length}
        if connection:
            resp.headers["connection"] = connection
        return resp

    async def test_exactly_consumed_206_is_bankable(self):
        self.assertTrue(dash_stream._dash_conn_bankable(object(), self._headers(), 100))

    async def test_short_or_long_body_is_not_bankable(self):
        self.assertFalse(dash_stream._dash_conn_bankable(object(), self._headers(), 99))
        self.assertFalse(dash_stream._dash_conn_bankable(object(), self._headers(), 101))

    async def test_unframed_or_non_206_is_not_bankable(self):
        self.assertFalse(dash_stream._dash_conn_bankable(object(), self._headers(status=200), 100))
        self.assertFalse(dash_stream._dash_conn_bankable(object(), self._headers(length=""), 100))

    async def test_server_close_is_not_bankable(self):
        self.assertFalse(dash_stream._dash_conn_bankable(object(), self._headers(connection="close"), 100))

    async def test_salvaged_connection_is_not_bankable(self):
        """A _ResumedDashConn can leave unread bytes queued on the socket."""
        salvaged = dash_stream._ResumedDashConn(object(), skip=10, limit=50)
        self.assertFalse(dash_stream._dash_conn_bankable(salvaged, self._headers(), 50))


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_take_give_round_trip(self):
        pool = dash_stream._PlaybackKeepAlivePool()
        conn = AsyncMock()
        await pool.give("http://h/a.m4s", "", conn)
        self.assertIs(await pool.take("http://h/a.m4s", ""), conn)
        # taken once only
        self.assertIsNone(await pool.take("http://h/a.m4s", ""))
        await pool.close_all()

    async def test_key_includes_proxy_and_url(self):
        pool = dash_stream._PlaybackKeepAlivePool()
        conn = AsyncMock()
        await pool.give("http://h/a.m4s", "", conn)
        self.assertIsNone(await pool.take("http://h/a.m4s", "socks5://127.0.0.1:1080"))
        self.assertIsNone(await pool.take("http://h/b.m4s", ""))
        self.assertIs(await pool.take("http://h/a.m4s", ""), conn)
        await pool.close_all()

    async def test_second_give_for_same_url_closes_the_first(self):
        pool = dash_stream._PlaybackKeepAlivePool()
        first, second = AsyncMock(), AsyncMock()
        await pool.give("http://h/a.m4s", "", first)
        await pool.give("http://h/a.m4s", "", second)
        first.close.assert_awaited()
        self.assertIs(await pool.take("http://h/a.m4s", ""), second)
        await pool.close_all()

    async def test_expired_entry_is_closed_not_returned(self):
        pool = dash_stream._PlaybackKeepAlivePool(ttl=0.01)
        conn = AsyncMock()
        await pool.give("http://h/a.m4s", "", conn)
        await asyncio.sleep(0.03)
        self.assertIsNone(await pool.take("http://h/a.m4s", ""))
        conn.close.assert_awaited()

    async def test_capacity_evicts_oldest(self):
        pool = dash_stream._PlaybackKeepAlivePool(max_idle=2)
        conns = [AsyncMock() for _ in range(3)]
        for i, c in enumerate(conns):
            await pool.give(f"http://h/{i}.m4s", "", c)
            await asyncio.sleep(0.001)
        conns[0].close.assert_awaited()
        self.assertIsNone(await pool.take("http://h/0.m4s", ""))
        self.assertIs(await pool.take("http://h/2.m4s", ""), conns[2])
        await pool.close_all()


class FragmentReuseTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end through `_fetch_dash_track`: one TCP connection for two fragments."""

    async def asyncSetUp(self):
        self.server = await _Server().start()

    async def asyncTearDown(self):
        await self.server.close()

    async def test_second_fragment_reuses_the_banked_socket(self):
        pool = dash_stream._PlaybackKeepAlivePool()
        headers = {"range": "bytes=0-99", "user-agent": "test"}

        conn, resp, used, length, _ = await dash_stream._fetch_dash_track(
            [self.server.url], dict(headers), "", "test", reuse_pool=pool
        )
        body = b"".join([c async for c in conn.iter_chunks()])
        self.assertEqual(len(body), 100)
        self.assertTrue(dash_stream._dash_conn_bankable(conn, resp, len(body)))
        await pool.give(used, "", conn)
        self.assertEqual(self.server.accepted, 1)

        conn2, resp2, _, _, _ = await dash_stream._fetch_dash_track(
            [self.server.url], {"range": "bytes=100-199", "user-agent": "test"}, "", "test", reuse_pool=pool
        )
        body2 = b"".join([c async for c in conn2.iter_chunks()])
        self.assertEqual(len(body2), 100)
        self.assertEqual(self.server.accepted, 1, "second fragment must not open a new TCP connection")
        self.assertEqual(len(self.server.requests), 2)
        await dash_stream._quiet_close(conn2)

    async def test_stale_banked_socket_falls_back_to_a_fresh_connect(self):
        pool = dash_stream._PlaybackKeepAlivePool()
        dead = stream.CdnConnection(self.server.url, headers={"range": "bytes=0-99"})
        await pool.give(self.server.url, "", dead)  # never connected: reuse must fail

        conn, _, _, _, _ = await dash_stream._fetch_dash_track(
            [self.server.url], {"range": "bytes=0-99"}, "", "test", reuse_pool=pool
        )
        body = b"".join([c async for c in conn.iter_chunks()])
        self.assertEqual(len(body), 100)
        self.assertEqual(self.server.accepted, 1)
        await dash_stream._quiet_close(conn)

    async def test_connection_close_responses_are_not_banked(self):
        server = await _Server(declare_connection_close=True).start()
        try:
            conn, resp, _, _, _ = await dash_stream._fetch_dash_track([server.url], {"range": "bytes=0-99"}, "", "test")
            body = b"".join([c async for c in conn.iter_chunks()])
            self.assertFalse(dash_stream._dash_conn_bankable(conn, resp, len(body)))
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
