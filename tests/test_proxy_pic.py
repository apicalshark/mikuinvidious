import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from miku.media.proxy import render_proxy_pic
from miku.state import image_limiter


class PooledAsyncByteStream(httpx.AsyncByteStream):
    """An AsyncByteStream that simulates a streaming connection holding a pool slot until closed."""

    def __init__(self, sem: asyncio.Semaphore, delay: float = 0.05):
        self.sem = sem
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        yield b"\xff\xd8\xff\xe0\x00\x10JFIF"

    async def aclose(self):
        if not self.closed:
            self.closed = True
            self.sem.release()


class MockPoolTransport(httpx.AsyncBaseTransport):
    """Transport with an explicit connection pool size and timeout to test pool exhaustion."""

    def __init__(self, max_connections: int = 2, pool_timeout: float = 0.1, stream_delay: float = 0.05):
        self.sem = asyncio.Semaphore(max_connections)
        self.pool_timeout = pool_timeout
        self.stream_delay = stream_delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            async with asyncio.timeout(self.pool_timeout):
                await self.sem.acquire()
        except TimeoutError as err:
            raise httpx.PoolTimeout("Connection pool timeout: no free connections available") from err

        return httpx.Response(
            200,
            headers={"Content-Type": "image/jpeg", "Content-Length": "11"},
            stream=PooledAsyncByteStream(self.sem, delay=self.stream_delay),
        )


class TestProxyPicTDD(unittest.IsolatedAsyncioTestCase):
    """TDD tests validating the old behavior (pool exhaustion) and the new behavior (buffered/protected)."""

    async def test_old_behaviour_unconsumed_stream_exhausts_pool(self):
        """Demonstrates the old behavior:

        When responses are returned as unconsumed streams, each in-flight stream
        holds a connection in the pool. When concurrent requests exceed the pool
        capacity, subsequent requests hit PoolTimeout and return HTTP 502.
        """
        # We simulate the exact old pattern:
        # 1. Transport has max 2 connections and 0.05s pool timeout.
        # 2. Each stream holds the connection for 0.2s unless consumed and closed.
        transport = MockPoolTransport(max_connections=2, pool_timeout=0.05, stream_delay=0.2)
        client = httpx.AsyncClient(transport=transport)

        # Helper replicating the old unconsumed streaming proxy implementation
        async def old_render_proxy_pic(url_path: str):
            async with image_limiter:
                req = client.build_request("GET", f"https://{url_path}")
                try:
                    resp = await client.send(req, stream=True)

                    async def _stream_body():
                        try:
                            async for chunk in resp.aiter_bytes(65536):
                                yield chunk
                        finally:
                            await resp.aclose()

                    # Returns without consuming stream: semaphore is released,
                    # but connection in pool remains held by resp!
                    return {"status": resp.status_code, "body_gen": _stream_body()}
                except Exception as e:
                    return {"status": 502, "error": type(e).__name__}

        # Fire 4 concurrent requests (pool size is only 2)
        tasks = [old_render_proxy_pic(f"i{i}.hdslb.com/bfs/test.jpg") for i in range(4)]
        results = await asyncio.gather(*tasks)

        statuses = [r["status"] for r in results]
        # In the old behavior, at least one request fails with 502 due to PoolTimeout
        self.assertIn(502, statuses, f"Expected 502 PoolTimeout in old behavior, got {statuses}")
        errors = [r.get("error") for r in results if "error" in r]
        self.assertIn("PoolTimeout", errors)

        # Clean up any opened streams
        for r in results:
            if "body_gen" in r:
                try:
                    async for _ in r["body_gen"]:
                        pass
                except Exception:
                    pass
        await client.aclose()

    async def test_new_behaviour_buffered_reads_prevent_pool_exhaustion(self):
        """Validates the new behavior:

        render_proxy_pic buffers the image bytes and closes the upstream response
        before returning to Quart. The upstream connection is released back to the
        pool immediately, so high concurrency with a small connection pool never
        exhausts the pool or triggers PoolTimeout.
        """
        transport = MockPoolTransport(max_connections=2, pool_timeout=0.1, stream_delay=0.01)
        client = httpx.AsyncClient(transport=transport)

        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
        ):
            # Fire 6 concurrent requests through render_proxy_pic against a pool of only 2 connections
            tasks = [render_proxy_pic(f"/proxy/pic/i{i}.hdslb.com/bfs/test_{i}.jpg") for i in range(6)]
            responses = await asyncio.gather(*tasks)

            # Every single request must succeed with 200 OK
            for i, resp in enumerate(responses):
                self.assertEqual(
                    resp.status_code,
                    200,
                    f"Request {i} failed with status {resp.status_code} instead of 200",
                )
                # And the response body should be readily available bytes, not an open upstream generator
                data = await resp.get_data()
                self.assertEqual(data, b"\xff\xd8\xff\xe0\x00\x10JFIF")

        await client.aclose()

    async def test_render_proxy_pic_success_headers_and_cache(self):
        """Validates that a successful image proxy response has correct headers."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg"},
                content=b"\xff\xd8\xff\xe0\x00\x10JFIF",
            )
        )
        client = httpx.AsyncClient(transport=transport)

        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
        ):
            resp = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/archive/pic.jpg")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content_type, "image/jpeg")
            self.assertEqual(resp.headers.get("Cache-Control"), "public, max-age=86400")
            self.assertEqual(await resp.get_data(), b"\xff\xd8\xff\xe0\x00\x10JFIF")

        await client.aclose()

    async def test_render_proxy_pic_bare_extension_normalization(self):
        """Validates normalization of bare MIME types returned by Bilibili CDN (e.g. 'webp' -> 'image/webp')."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"Content-Type": "webp"},
                content=b"RIFF....WEBP",
            )
        )
        client = httpx.AsyncClient(transport=transport)

        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
        ):
            resp = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/face/avatar.webp")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content_type, "image/webp")
            self.assertEqual(await resp.get_data(), b"RIFF....WEBP")

        await client.aclose()

    async def test_render_proxy_pic_invalid_content_type_rejected(self):
        """Validates that non-image Content-Type (e.g. text/html) is rejected with 403."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                content=b"<html>danger</html>",
            )
        )
        client = httpx.AsyncClient(transport=transport)

        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
        ):
            resp = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/archive/not_an_image.jpg")
            self.assertEqual(resp.status_code, 403)
            self.assertIn("Invalid content type", await resp.get_data(as_text=True))

        await client.aclose()

    async def test_render_proxy_pic_path_traversal_forbidden(self):
        """Validates path traversal attempts are rejected with 403."""
        resp = await render_proxy_pic("/proxy/pic/../etc/passwd")
        self.assertEqual(resp.status_code, 403)

        resp2 = await render_proxy_pic("/proxy/pic//absolute/path")
        self.assertEqual(resp2.status_code, 403)

    async def test_render_proxy_pic_unsafe_url_forbidden(self):
        """Validates unsafe URLs rejected by is_safe_proxy_url return 403."""
        with patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=False)):
            resp = await render_proxy_pic("/proxy/pic/evil.com/malicious.jpg")
            self.assertEqual(resp.status_code, 403)

    async def test_render_proxy_pic_oversized_image_rejected(self):
        """Validates that excessively large images (e.g. > 15MB) are rejected with 413 Payload Too Large."""
        # 16 MB Content-Length
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg", "Content-Length": str(16 * 1024 * 1024)},
                content=b"\xff\xd8" + b"\x00" * 1000,
            )
        )
        client = httpx.AsyncClient(transport=transport)

        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
        ):
            resp = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/archive/huge.jpg")
            self.assertEqual(resp.status_code, 413)

        await client.aclose()

    async def test_network_get_image_client_isolation(self):
        """Validates that Network.get_image_client returns an isolated HTTP/2 client with the safe H2 connection."""
        import httpcore._async.http2 as h2_mod
        from miku.state import Network, close_global_client

        await close_global_client()
        try:
            with patch("miku.services.network.httpx.AsyncClient", wraps=httpx.AsyncClient) as mock_client_cls:
                image_client = await Network.get_image_client()
                async_client = await Network.get_async_client()

            self.assertIsInstance(image_client, httpx.AsyncClient)
            self.assertIsInstance(async_client, httpx.AsyncClient)
            # Must be separate clients with distinct connection pools
            self.assertIsNot(image_client, async_client)
            self.assertEqual(mock_client_cls.call_args_list[0].kwargs.get("http2"), True)
            self.assertTrue(getattr(h2_mod.AsyncHTTP2Connection, "_miku_safe_http2", False))

            # Same client on subsequent calls
            self.assertIs(await Network.get_image_client(), image_client)
            self.assertIs(await Network.get_async_client(), async_client)
        finally:
            await close_global_client()

    async def test_render_proxy_pic_exact_mime_rejects_svg_and_suffix_variants(self):
        """Exact Content-Type check rejects image/png+xml and image/svg+xml with 403."""
        for bad_mime in ("image/png+xml", "image/svg+xml; charset=utf-8"):
            with self.subTest(bad_mime=bad_mime):
                transport = httpx.MockTransport(
                    lambda req, mime=bad_mime: httpx.Response(
                        200,
                        headers={"Content-Type": mime},
                        content=b"<svg/>",
                    )
                )
                client = httpx.AsyncClient(transport=transport)
                try:
                    with (
                        patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                        patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                        patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
                    ):
                        resp = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/archive/vector_test.svg")
                    self.assertEqual(resp.status_code, 403)
                finally:
                    await client.aclose()

    async def test_render_proxy_pic_forwards_query_string_and_keys_cache_per_query(self):
        """Query string is forwarded to upstream CDN and included in the disk cache key."""
        import tempfile

        from miku.app import app
        from miku.media.image_cache import ImageCache

        requested_urls = []

        def handler(req: httpx.Request) -> httpx.Response:
            requested_urls.append(str(req.url))
            return httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg"},
                content=f"img:{req.url.query.decode()}".encode(),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ImageCache(cache_dir=tmpdir)
            try:
                with (
                    patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                    patch("miku.media.proxy.image_cache", cache),
                    patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
                ):
                    async with app.test_request_context("/proxy/pic/i0.hdslb.com/bfs/pic.jpg?foo=1&bar=2"):
                        r1 = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/pic.jpg")
                    self.assertEqual(r1.status_code, 200)
                    self.assertEqual(await r1.get_data(), b"img:foo=1&bar=2")

                    # Distinct query string must miss cache and hit upstream separately
                    async with app.test_request_context("/proxy/pic/i0.hdslb.com/bfs/pic.jpg?foo=2"):
                        r2 = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/pic.jpg")
                    self.assertEqual(r2.status_code, 200)
                    self.assertEqual(await r2.get_data(), b"img:foo=2")

                    # Same query string hits disk cache
                    async with app.test_request_context("/proxy/pic/i0.hdslb.com/bfs/pic.jpg?foo=1&bar=2"):
                        r3 = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/pic.jpg")
                    self.assertEqual(r3.status_code, 200)
                    self.assertEqual(r3.headers.get("X-Cache"), "HIT")
                    self.assertEqual(await r3.get_data(), b"img:foo=1&bar=2")
            finally:
                await cache.close()
                await client.aclose()

        self.assertEqual(
            requested_urls,
            [
                "https://i0.hdslb.com/bfs/pic.jpg?foo=1&bar=2",
                "https://i0.hdslb.com/bfs/pic.jpg?foo=2",
            ],
        )

    async def test_render_proxy_pic_suppresses_aclose_error_on_upstream_failure(self):
        """A secondary error in resp.aclose() during exception cleanup returns 502 instead of crashing ASGI."""

        class FailingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise ValueError("semaphore released too many times")
                yield b""

            async def aclose(self):
                raise ValueError("semaphore released too many times")

        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg"},
                stream=FailingStream(),
            )
        )
        client = httpx.AsyncClient(transport=transport)

        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
        ):
            resp = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/archive/broken.jpg")
            self.assertEqual(resp.status_code, 502)

        await client.aclose()

    async def test_http2_cancellation_during_init_does_not_corrupt_semaphore(self):
        """Cancelling an HTTP/2 request during connection init must not desync max_streams."""
        import h2.config
        import h2.connection
        import httpcore
        import httpcore._async.http2 as h2_mod
        from miku.services.network import _ensure_safe_httpcore_http2

        _ensure_safe_httpcore_http2()

        class MockH2Stream(httpcore.AsyncNetworkStream):
            def __init__(self):
                self.srv = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False))
                self.srv.initiate_connection()
                self.out_q: asyncio.Queue[bytes] = asyncio.Queue()
                self.out_q.put_nowait(self.srv.data_to_send())

            async def read(self, max_bytes, timeout=None):
                return await self.out_q.get()

            async def write(self, buffer, timeout=None):
                for ev in self.srv.receive_data(buffer):
                    if isinstance(ev, h2.events.RequestReceived):
                        self.srv.send_headers(ev.stream_id, [(b":status", b"200"), (b"content-type", b"image/jpeg")])
                        self.srv.send_data(ev.stream_id, b"ok", end_stream=True)
                data = self.srv.data_to_send()
                if data:
                    self.out_q.put_nowait(data)

            async def aclose(self):
                pass

            async def start_tls(self, *a, **kw):
                return self

            def get_extra_info(self, info):
                return None

        conn = h2_mod.AsyncHTTP2Connection(
            httpcore.Origin(b"https", b"i0.hdslb.com", 443),
            MockH2Stream(),
        )
        req = httpcore.Request(b"GET", b"https://i0.hdslb.com/test.jpg", headers=[(b"host", b"i0.hdslb.com")])

        task = asyncio.create_task(conn.handle_async_request(req))
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        # Either init completed safely (connection usable) or aborted cleanly (closed)
        if conn.is_available():
            resp = await conn.handle_async_request(req)
            try:
                body = b"".join([chunk async for chunk in resp.stream])
            finally:
                await resp.aclose()
            self.assertEqual(resp.status, 200)
            self.assertEqual(body, b"ok")
        else:
            self.assertTrue(conn.is_closed())

    async def test_http2_concurrent_streams_cancellations_and_settings_shrink(self):
        """150 concurrent H2 streams, >100 cancelled mid-stream, and remote settings shrink all succeed without ValueError/deadlock."""
        import h2.config
        import h2.connection
        import h2.settings
        import httpcore
        import httpcore._async.http2 as h2_mod
        from miku.services.network import _ensure_safe_httpcore_http2

        _ensure_safe_httpcore_http2()

        class MockH2Stream(httpcore.AsyncNetworkStream):
            def __init__(self, hold_body: bool = False):
                self.hold_body = hold_body
                self.srv = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False))
                self.srv.initiate_connection()
                self.out_q: asyncio.Queue[bytes] = asyncio.Queue()
                self.out_q.put_nowait(self.srv.data_to_send())

            async def read(self, max_bytes, timeout=None):
                return await self.out_q.get()

            async def write(self, buffer, timeout=None):
                for ev in self.srv.receive_data(buffer):
                    if isinstance(ev, h2.events.RequestReceived):
                        self.srv.send_headers(ev.stream_id, [(b":status", b"200"), (b"content-type", b"image/jpeg")])
                        if not self.hold_body:
                            self.srv.send_data(ev.stream_id, b"ok", end_stream=True)
                data = self.srv.data_to_send()
                if data:
                    self.out_q.put_nowait(data)

            async def aclose(self):
                pass

            async def start_tls(self, *a, **kw):
                return self

            def get_extra_info(self, info):
                return None

        req = httpcore.Request(b"GET", b"https://i0.hdslb.com/test.jpg", headers=[(b"host", b"i0.hdslb.com")])

        # 1. Cancel 120 streams mid-body (> default max_concurrent_streams=100) then run another request
        hold_stream = MockH2Stream(hold_body=True)
        conn_cancel = h2_mod.AsyncHTTP2Connection(httpcore.Origin(b"https", b"i0.hdslb.com", 443), hold_stream)

        async def cancel_mid_body():
            resp = await conn_cancel.handle_async_request(req)
            try:
                async for _ in resp.stream:
                    pass
            finally:
                await resp.aclose()

        for _ in range(120):
            t = asyncio.create_task(cancel_mid_body())
            await asyncio.sleep(0.001)
            t.cancel()
            with self.assertRaises((asyncio.CancelledError, httpcore.RemoteProtocolError)):
                await t

        hold_stream.hold_body = False
        resp = await conn_cancel.handle_async_request(req)
        try:
            body = b"".join([chunk async for chunk in resp.stream])
        finally:
            await resp.aclose()
        self.assertEqual(body, b"ok")
        self.assertEqual(conn_cancel._h2_state.open_outbound_streams, 0)

        # 2. Shrink MAX_CONCURRENT_STREAMS from 100 -> 4 while 150 concurrent requests run
        stream = MockH2Stream(hold_body=False)
        conn = h2_mod.AsyncHTTP2Connection(httpcore.Origin(b"https", b"i0.hdslb.com", 443), stream)

        async def fetch():
            r = await conn.handle_async_request(req)
            try:
                return r.status, b"".join([chunk async for chunk in r.stream])
            finally:
                await r.aclose()

        stream.srv.update_settings({h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: 4})
        stream.out_q.put_nowait(stream.srv.data_to_send())

        results = await asyncio.wait_for(asyncio.gather(*(fetch() for _ in range(150))), timeout=5.0)
        self.assertEqual(len(results), 150)
        self.assertTrue(all(status == 200 and body == b"ok" for status, body in results))


if __name__ == "__main__":
    unittest.main()
