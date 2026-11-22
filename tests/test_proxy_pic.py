# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import httpx
from miku.core.url_signing import sign_pic_target
from miku.media.proxy import _get_image_mirror_candidates, render_proxy_pic
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
            tasks = [render_proxy_pic(sign_pic_target(f"i{i}.hdslb.com/bfs/test_{i}.jpg")) for i in range(6)]
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
            resp = await render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/pic.jpg"))
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
            resp = await render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/face/avatar.webp"))
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
            resp = await render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/not_an_image.jpg"))
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
            resp = await render_proxy_pic(sign_pic_target("evil.com/malicious.jpg"))
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
            resp = await render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/huge.jpg"))
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
                        resp = await render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/vector_test.svg"))
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
                    s1 = sign_pic_target("i0.hdslb.com/bfs/pic.jpg?foo=1&bar=2")
                    s1_path = s1.split("?", 1)[0]
                    async with app.test_request_context(s1):
                        r1 = await render_proxy_pic(s1_path)
                    self.assertEqual(r1.status_code, 200)
                    self.assertEqual(await r1.get_data(), b"img:foo=1&bar=2")

                    # Distinct query string must miss cache and hit upstream separately
                    s2 = sign_pic_target("i0.hdslb.com/bfs/pic.jpg?foo=2")
                    s2_path = s2.split("?", 1)[0]
                    async with app.test_request_context(s2):
                        r2 = await render_proxy_pic(s2_path)
                    self.assertEqual(r2.status_code, 200)
                    self.assertEqual(await r2.get_data(), b"img:foo=2")

                    # Same query string hits disk cache
                    async with app.test_request_context(s1):
                        r3 = await render_proxy_pic(s1_path)
                    self.assertEqual(r3.status_code, 200)
                    self.assertEqual(r3.headers.get("X-Cache"), "HIT")
                    self.assertEqual(await r3.get_data(), b"img:foo=1&bar=2")
            finally:
                await cache.close()
                await client.aclose()

        expected_primary = _get_image_mirror_candidates("https://i0.hdslb.com/bfs/pic.jpg?foo=1&bar=2")[0]
        expected_host = urlparse(expected_primary).netloc
        self.assertEqual(
            requested_urls,
            [
                f"https://{expected_host}/bfs/pic.jpg?foo=1&bar=2",
                f"https://{expected_host}/bfs/pic.jpg?foo=2",
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
            resp = await render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/broken.jpg"))
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
                    if isinstance(ev, h2.events.RequestReceived) and ev.stream_id in self.srv.streams:
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
            await asyncio.sleep(0)
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


class TestProxyPicShardingAndRetry(unittest.IsolatedAsyncioTestCase):
    def test_sharding_spreads_across_fast_cdns_and_distinct_fallback(self):
        """BFS image URLs keep original primary (e.g. i0) and fall back to i2.biliimg.com then i1.hdslb.com."""
        cands = _get_image_mirror_candidates("https://i0.hdslb.com/bfs/archive/cover_0.jpg@320w.webp")
        self.assertEqual(
            [urlparse(u).netloc for u in cands],
            ["i0.hdslb.com", "i2.biliimg.com", "i1.hdslb.com"],
        )

        # If original is i2.biliimg.com, fallbacks skip it and use i0 then i1
        cands_i2 = _get_image_mirror_candidates("https://i2.biliimg.com/bfs/archive/cover_0.jpg@320w.webp")
        self.assertEqual(
            [urlparse(u).netloc for u in cands_i2],
            ["i2.biliimg.com", "i0.hdslb.com", "i1.hdslb.com"],
        )

        # Non-BFS paths and non-CDN hosts are not rewritten
        self.assertEqual(
            _get_image_mirror_candidates("https://i0.hdslb.com/favicon.ico"),
            ["https://i0.hdslb.com/favicon.ico"],
        )
        self.assertEqual(
            _get_image_mirror_candidates("https://article.biliimg.com/bfs/article/1.jpg"),
            ["https://article.biliimg.com/bfs/article/1.jpg"],
        )

    async def test_retry_once_on_html_response_succeeds_and_caches_original_url(self):
        """When primary mirror returns text/html, render_proxy_pic retries once on alternate mirror."""
        import tempfile

        from miku.media.image_cache import ImageCache

        requested_hosts = []

        def handler(req: httpx.Request) -> httpx.Response:
            requested_hosts.append(req.url.host)
            if len(requested_hosts) == 1:
                return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>throttled</html>")
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"\xff\xd8\xff\xe0OK")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ImageCache(cache_dir=tmpdir)
            try:
                with (
                    patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                    patch("miku.media.proxy.image_cache", cache),
                    patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
                ):
                    signed_url = sign_pic_target("i0.hdslb.com/bfs/archive/retry_html.jpg")
                    resp = await render_proxy_pic(signed_url)
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(await resp.get_data(), b"\xff\xd8\xff\xe0OK")
                    self.assertEqual(len(requested_hosts), 2)
                    self.assertNotEqual(requested_hosts[0], requested_hosts[1])

                    # Cached under original URL key
                    resp2 = await render_proxy_pic(signed_url)
                    self.assertEqual(resp2.status_code, 200)
                    self.assertEqual(resp2.headers.get("X-Cache"), "HIT")
                    self.assertEqual(len(requested_hosts), 2)
            finally:
                await cache.close()
                await client.aclose()

    async def test_retry_once_on_non_200_and_timeout(self):
        """Retries once on non-200 status or ReadTimeout, and stops after 2 total attempts on failure."""
        for mode in ("non_200", "timeout"):
            with self.subTest(mode=mode):
                attempts = []

                def handler(req: httpx.Request, m=mode, attempts=attempts) -> httpx.Response:
                    attempts.append(req.url.host)
                    if len(attempts) == 1:
                        if m == "non_200":
                            return httpx.Response(502, headers={"Content-Type": "text/html"}, content=b"bad gateway")
                        raise httpx.ReadTimeout("upstream timed out", request=req)
                    return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNGok")

                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                try:
                    with (
                        patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                        patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                        patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                        patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
                    ):
                        resp = await render_proxy_pic(sign_pic_target(f"i0.hdslb.com/bfs/archive/{mode}.png"))
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(await resp.get_data(), b"\x89PNGok")
                    self.assertEqual(len(attempts), 2)
                    self.assertNotEqual(attempts[0], attempts[1])
                finally:
                    await client.aclose()

    async def test_exhausted_retry_returns_502_on_double_timeout(self):
        """If primary and fallback mirrors all time out, returns 502 after 3 attempts."""
        attempts = []

        def handler(req: httpx.Request) -> httpx.Response:
            attempts.append(req.url.host)
            raise httpx.ReadTimeout("timed out", request=req)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                resp = await render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/both_timeout.jpg"))
            self.assertEqual(resp.status_code, 502)
            self.assertEqual(attempts, ["i0.hdslb.com", "i2.biliimg.com", "i1.hdslb.com"])
        finally:
            await client.aclose()


class TestProxyPicSecurityAndNegativeCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from miku.media import proxy

        proxy._negative_cache.clear()

    async def test_unsigned_and_tampered_urls_rejected_with_403_before_network(self):
        safe_mock = AsyncMock(return_value=True)
        client_mock = AsyncMock()
        signed = sign_pic_target("i0.hdslb.com/bfs/archive/good.jpg")
        tampered = signed.replace("good.jpg", "evil.jpg")

        with (
            patch("miku.media.proxy.is_safe_proxy_url", new=safe_mock),
            patch("miku.state.Network.get_image_client", new=client_mock),
        ):
            r_unsigned = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/archive/good.jpg")
            r_tampered = await render_proxy_pic(tampered)

        self.assertEqual(r_unsigned.status_code, 403)
        self.assertEqual(r_tampered.status_code, 403)
        safe_mock.assert_not_awaited()
        client_mock.assert_not_awaited()

    async def test_404_and_410_do_not_retry_and_hit_negative_cache(self):
        for code in (404, 410):
            with self.subTest(code=code):
                calls = []

                def handler(req: httpx.Request, status=code, calls=calls) -> httpx.Response:
                    calls.append(str(req.url))
                    return httpx.Response(status, headers={"Content-Type": "text/html"}, content=b"missing")

                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                disk_set = AsyncMock()
                try:
                    with (
                        patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                        patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                        patch("miku.media.proxy.image_cache.set", new=disk_set),
                        patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
                    ):
                        target = sign_pic_target(f"i0.hdslb.com/bfs/archive/missing_{code}.jpg")
                        r1 = await render_proxy_pic(target)
                        self.assertEqual(r1.status_code, code)
                        self.assertEqual(r1.headers.get("Cache-Control"), "public, max-age=60")
                        self.assertEqual(len(calls), 1)
                        disk_set.assert_not_awaited()

                        r2 = await render_proxy_pic(target)
                        self.assertEqual(r2.status_code, code)
                        self.assertEqual(r2.headers.get("X-Cache"), "NEGATIVE-HIT")
                        self.assertEqual(len(calls), 1)
                finally:
                    await client.aclose()

    async def test_singleflight_coalesces_concurrent_misses_and_survives_follower_cancel(self):
        upstream_calls = 0
        started = asyncio.Event()
        proceed = asyncio.Event()

        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                await proceed.wait()
                yield b"\xff\xd8\xff\xe0SINGLE"

        async def async_handler(req: httpx.Request) -> httpx.Response:
            nonlocal upstream_calls
            upstream_calls += 1
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, stream=SlowStream())

        client = httpx.AsyncClient(transport=httpx.MockTransport(async_handler))
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                target = sign_pic_target("i0.hdslb.com/bfs/archive/singleflight.jpg")
                tasks = [asyncio.create_task(render_proxy_pic(target)) for _ in range(5)]
                await asyncio.wait_for(started.wait(), timeout=2.0)
                await asyncio.sleep(0.01)

                # Cancel one follower mid-flight; others must still succeed from the single upstream call.
                tasks[1].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await tasks[1]

                proceed.set()
                results = await asyncio.gather(tasks[0], tasks[2], tasks[3], tasks[4])
                self.assertEqual(upstream_calls, 1)
                for resp in results:
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(await resp.get_data(), b"\xff\xd8\xff\xe0SINGLE")
        finally:
            await client.aclose()


class TestProxyPicWaveRetryAndH2Coalescing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from miku.media import proxy

        proxy._negative_cache.clear()

    async def test_burst_failures_grouped_into_waves_across_mirrors(self):
        """10 concurrent images with 3 primary failures group the 3 into Wave 1, then remaining failures into Wave 2."""
        from miku.media import proxy

        self.assertTrue(hasattr(proxy, "_WaveRetryCoordinator"))

        events: list[tuple[str, str]] = []
        wave1_hosts: list[str] = []
        wave2_hosts: list[str] = []
        fail_primary_paths = {f"/bfs/archive/burst_{i}.jpg" for i in (2, 5, 8)}
        fail_wave1_paths = {f"/bfs/archive/burst_{i}.jpg" for i in (2, 8)}

        async def handler(req: httpx.Request) -> httpx.Response:
            host = req.url.host
            path = req.url.path
            events.append((host, path))
            if host == "i0.hdslb.com":
                await asyncio.sleep(0.005)
                if path in fail_primary_paths:
                    return httpx.Response(502, headers={"Content-Type": "text/html"}, content=b"bad")
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"OK:" + path.encode())
            if host == "i2.biliimg.com":
                wave1_hosts.append(path)
                await asyncio.sleep(0.01)
                if path in fail_wave1_paths:
                    return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"throttled")
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"OK:" + path.encode())
            if host == "i1.hdslb.com":
                wave2_hosts.append(path)
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"OK:" + path.encode())
            return httpx.Response(502)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                urls = [sign_pic_target(f"i0.hdslb.com/bfs/archive/burst_{i}.jpg") for i in range(10)]
                responses = await asyncio.gather(*(render_proxy_pic(u) for u in urls))

            for i, resp in enumerate(responses):
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(await resp.get_data(), f"OK:/bfs/archive/burst_{i}.jpg".encode())

            self.assertEqual(set(wave1_hosts), fail_primary_paths)
            self.assertEqual(set(wave2_hosts), fail_wave1_paths)
            # All 3 Wave 1 requests must start before any Wave 2 request starts (grouped wave progression)
            last_w1_idx = max(idx for idx, (h, _) in enumerate(events) if h == "i2.biliimg.com")
            first_w2_idx = min(idx for idx, (h, _) in enumerate(events) if h == "i1.hdslb.com")
            self.assertLess(last_w1_idx, first_w2_idx)
        finally:
            await client.aclose()

    async def test_isolated_single_failure_flushes_without_coalesce_delay(self):
        """A single failing image with no peer requests flushes Wave 1 without scheduling a coalesce timer."""
        from miku.media.proxy import _get_loop_state

        async def handler(req: httpx.Request) -> httpx.Response:
            if req.url.host == "i0.hdslb.com":
                return httpx.Response(502, headers={"Content-Type": "text/html"}, content=b"err")
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"OK")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        loop = asyncio.get_running_loop()
        orig_call_later = loop.call_later
        delayed_calls = []

        def spy_call_later(delay, callback, *args, **kwargs):
            delayed_calls.append(delay)
            return orig_call_later(delay, callback, *args, **kwargs)

        try:
            _get_loop_state()
            with (
                patch.object(loop, "call_later", side_effect=spy_call_later),
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                resp = await asyncio.wait_for(
                    render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/single_fast.jpg")),
                    timeout=2.0,
                )

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(delayed_calls, [])
        finally:
            await client.aclose()

    async def test_cold_origin_or_tunnel_opens_single_h2_connection_for_wave(self):
        """Concurrent grouped fallback requests to a cold tunnel/H2 origin must open only 1 H2 connection."""
        import httpcore

        class FakeColdTunnelConn:
            def __init__(self, origin):
                self._origin = origin
                self._connected = False
                self._connection = None
                self._closed = False

            def can_handle_request(self, origin):
                return origin == self._origin

            def is_available(self):
                # Simulates AsyncTunnelHTTPConnection: False until CONNECT+TLS completes!
                return self._connected and not self._closed

            def is_closed(self):
                return self._closed

            async def connect_and_handle(self, req: httpx.Request) -> httpx.Response:
                if not self._connected:
                    await asyncio.sleep(0.02)  # slow TLS + ALPN handshake
                    self._connected = True
                    self._connection = type("FakeH2", (), {"_h2_state": object(), "_sent_connection_init": True})()
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"TUNNEL_OK")

        class FakePool:
            def __init__(self):
                self._connections: list[FakeColdTunnelConn] = []
                self.opened_counts: dict[str, int] = {}

            async def handle(self, req: httpx.Request) -> httpx.Response:
                host = req.url.host
                origin = httpcore.Origin(b"https", host.encode("ascii"), 443)
                avail = [c for c in self._connections if c.can_handle_request(origin) and c.is_available()]
                if avail:
                    conn = avail[0]
                else:
                    conn = FakeColdTunnelConn(origin)
                    self._connections.append(conn)
                    self.opened_counts[host] = self.opened_counts.get(host, 0) + 1
                return await conn.connect_and_handle(req)

        fake_pool = FakePool()

        class FakeTransport(httpx.AsyncBaseTransport):
            def __init__(self, pool):
                self._pool = pool

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.host == "i0.hdslb.com":
                    return httpx.Response(502, headers={"Content-Type": "text/html"}, content=b"fail")
                return await self._pool.handle(request)

        client = httpx.AsyncClient(transport=FakeTransport(fake_pool))
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                urls = [sign_pic_target(f"i0.hdslb.com/bfs/archive/tunnel_{i}.jpg") for i in range(3)]
                responses = await asyncio.gather(*(render_proxy_pic(u) for u in urls))

            for resp in responses:
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(await resp.get_data(), b"TUNNEL_OK")
            self.assertEqual(fake_pool.opened_counts.get("i2.biliimg.com"), 1)
        finally:
            await client.aclose()

    async def test_mixed_primary_host_failures_consolidate_onto_one_wave_mirror(self):
        """Failures from i0, i1, i2 consolidate onto a single shared untried CDN (s2.hdslb.com) in Wave 1."""
        fallback_hosts: list[str] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            if req.url.host in ("i0.hdslb.com", "i1.hdslb.com", "i2.biliimg.com"):
                return httpx.Response(502, headers={"Content-Type": "text/html"}, content=b"fail")
            fallback_hosts.append(req.url.host)
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"CONSOLIDATED")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                urls = [
                    sign_pic_target("i0.hdslb.com/bfs/archive/mix_0.jpg"),
                    sign_pic_target("i1.hdslb.com/bfs/archive/mix_1.jpg"),
                    sign_pic_target("i2.biliimg.com/bfs/archive/mix_2.jpg"),
                ]
                responses = await asyncio.gather(*(render_proxy_pic(u) for u in urls))

            for resp in responses:
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(await resp.get_data(), b"CONSOLIDATED")
            self.assertEqual(fallback_hosts, ["s2.hdslb.com", "s2.hdslb.com", "s2.hdslb.com"])
        finally:
            await client.aclose()

    def test_negative_cache_eviction_and_ttl_expiry(self):
        from miku.core.singleflight import BoundedNegativeCache

        cache = BoundedNegativeCache(max_entries=2, ttl_seconds=10.0)
        cache.set("https://i0.hdslb.com/bfs/1.jpg", 404)
        cache.set("https://i0.hdslb.com/bfs/2.jpg", 410)
        self.assertIsNotNone(cache.get("https://i0.hdslb.com/bfs/1.jpg"))  # refresh LRU order
        cache.set("https://i0.hdslb.com/bfs/3.jpg", 404)  # evicts 2.jpg (oldest)
        self.assertIsNone(cache.get("https://i0.hdslb.com/bfs/2.jpg"))
        self.assertIsNotNone(cache.get("https://i0.hdslb.com/bfs/1.jpg"))
        self.assertIsNotNone(cache.get("https://i0.hdslb.com/bfs/3.jpg"))

        # TTL expiration
        with patch("miku.core.singleflight.time.monotonic", return_value=100.0):
            ttl_cache = BoundedNegativeCache(max_entries=10, ttl_seconds=5.0)
            ttl_cache.set("https://i0.hdslb.com/bfs/exp.jpg", 404)
        with patch("miku.core.singleflight.time.monotonic", return_value=106.0):
            self.assertIsNone(ttl_cache.get("https://i0.hdslb.com/bfs/exp.jpg"))

    async def test_quart_route_percent_encoded_paths_and_query_strings(self):
        """End-to-end Quart test_client handles %20, UTF-8 %E4..., %2B, %3F and query strings without 403."""
        from miku.app import app

        seen_urls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_urls.append(str(req.url))
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"ENC_OK")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        test_client = app.test_client()
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                targets = [
                    "i0.hdslb.com/bfs/emote/doge%20a.png",
                    "i0.hdslb.com/bfs/archive/%E4%B8%AD%E6%96%87.jpg?w=320&h=180",
                    "i0.hdslb.com/bfs/archive/a%2Bb%3Fc.jpg?x=1",
                ]
                for t in targets:
                    signed = sign_pic_target(t)
                    path_only = signed.split("?", 1)[0]
                    resp = await test_client.get(signed, scope_base={"raw_path": path_only.encode("ascii")})
                    self.assertEqual(resp.status_code, 200, f"Failed for {signed}")
                    self.assertEqual(await resp.get_data(), b"ENC_OK")

                plus_signed = sign_pic_target("i0.hdslb.com/bfs/archive/a+b.jpg")
                resp_fallback = await test_client.get(plus_signed, scope_base={"raw_path": None})
                self.assertEqual(resp_fallback.status_code, 200)
        finally:
            await client.aclose()

        self.assertEqual(
            seen_urls,
            [
                "https://i0.hdslb.com/bfs/emote/doge%20a.png",
                "https://i0.hdslb.com/bfs/archive/%E4%B8%AD%E6%96%87.jpg?w=320&h=180",
                "https://i0.hdslb.com/bfs/archive/a%2Bb%3Fc.jpg?x=1",
                "https://i0.hdslb.com/bfs/archive/a+b.jpg",
            ],
        )

    async def test_origin_guard_releases_lock_after_headers_before_body_stream(self):
        """Warmup lock must release right after client.send headers arrive, not wait for body download."""
        import httpcore

        body_gate = asyncio.Event()
        req2_headers_arrived = asyncio.Event()
        req1_started = asyncio.Event()

        class GatedBodyStream(httpx.AsyncByteStream):
            def __init__(self, first: bool):
                self.first = first

            async def __aiter__(self):
                if self.first:
                    await body_gate.wait()
                yield b"BODY"

        class FakeConn:
            def __init__(self, origin):
                self._origin = origin
                self._connected = False
                self._connection = None

            def can_handle_request(self, origin):
                return origin == self._origin

            def is_available(self):
                return self._connected

            def is_closed(self):
                return False

            def has_expired(self):
                return False

        class FakePool:
            def __init__(self):
                self._connections: list[FakeConn] = []
                self.calls = 0

            async def handle(self, req: httpx.Request) -> httpx.Response:
                self.calls += 1
                origin = httpcore.Origin(b"https", req.url.host.encode("ascii"), 443)
                if not self._connections:
                    conn = FakeConn(origin)
                    self._connections.append(conn)
                    req1_started.set()
                    await asyncio.sleep(0.01)
                    conn._connected = True
                    conn._connection = type("FakeH2", (), {"_h2_state": object()})()
                    return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, stream=GatedBodyStream(True))
                req2_headers_arrived.set()
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, stream=GatedBodyStream(False))

        pool = FakePool()

        class FakeTransport(httpx.AsyncBaseTransport):
            def __init__(self, p):
                self._pool = p

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return await self._pool.handle(request)

        client = httpx.AsyncClient(transport=FakeTransport(pool))
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                t1 = asyncio.create_task(render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/stream1.jpg")))
                await asyncio.wait_for(req1_started.wait(), timeout=1.0)
                t2 = asyncio.create_task(render_proxy_pic(sign_pic_target("i0.hdslb.com/bfs/archive/stream2.jpg")))
                # Req 2 headers must arrive while Req 1's body is still blocked on body_gate!
                await asyncio.wait_for(req2_headers_arrived.wait(), timeout=0.5)
                r2 = await asyncio.wait_for(t2, timeout=0.5)
                self.assertEqual(r2.status_code, 200)
                body_gate.set()
                r1 = await asyncio.wait_for(t1, timeout=0.5)
                self.assertEqual(r1.status_code, 200)
        finally:
            body_gate.set()
            await client.aclose()

    async def test_expired_h2_connection_triggers_single_flight_replacement(self):
        """Expired pool connections (has_expired()==True) must not bypass the warmup lock."""
        import httpcore
        from miku.media.proxy import _has_ready_h2_connection

        class ExpiredConn:
            def can_handle_request(self, origin):
                return origin == httpcore.Origin(b"https", b"i2.biliimg.com", 443)

            def is_closed(self):
                return False

            def is_available(self):
                return True

            def has_expired(self):
                return True

            _connection = type("FakeH2", (), {"_h2_state": object()})()

        class FakeTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self._pool = type("P", (), {"_connections": [ExpiredConn()]})()

        client = httpx.AsyncClient(transport=FakeTransport())
        try:
            self.assertFalse(_has_ready_h2_connection(client, "https://i2.biliimg.com/bfs/a.jpg"))
        finally:
            await client.aclose()

    async def test_dispatch_wave_runs_distinct_host_groups_concurrently(self):
        """Outlier groups in the same wave targeting different hosts must run concurrently, not serially."""
        import weakref

        from miku.media.proxy import _WaveRetryCoordinator

        host_started = {"s2.hdslb.com": asyncio.Event(), "i1.hdslb.com": asyncio.Event()}
        both_started = asyncio.Event()

        async def handler(req: httpx.Request) -> httpx.Response:
            if req.url.host in ("i0.hdslb.com", "i2.biliimg.com"):
                return httpx.Response(502, headers={"Content-Type": "text/html"}, content=b"fail")
            if req.url.host in host_started:
                host_started[req.url.host].set()
                if all(ev.is_set() for ev in host_started.values()):
                    both_started.set()
                # If groups ran serially, waiting for both_started would deadlock/timeout!
                await asyncio.wait_for(both_started.wait(), timeout=0.5)
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"PARALLEL")
            return httpx.Response(502)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        loop = asyncio.get_running_loop()
        coord = _WaveRetryCoordinator(loop, weakref.WeakKeyDictionary(), window_seconds=0.04, max_waves=2)
        try:
            from urllib.parse import urlparse

            from miku.media.proxy import _RetryItem

            items = [
                # Tried i0, i2.biliimg, i2.hdslb, s2 -> only i1 untried
                _RetryItem(
                    "https://i0.hdslb.com/bfs/1.jpg",
                    urlparse("https://i0.hdslb.com/bfs/1.jpg"),
                    {},
                    client,
                    {"i0.hdslb.com", "i2.biliimg.com", "i2.hdslb.com", "s2.hdslb.com"},
                    loop.create_future(),
                ),
                # Tried i0, i2.biliimg, i2.hdslb, i1 -> only s2 untried
                _RetryItem(
                    "https://i0.hdslb.com/bfs/2.jpg",
                    urlparse("https://i0.hdslb.com/bfs/2.jpg"),
                    {},
                    client,
                    {"i0.hdslb.com", "i2.biliimg.com", "i2.hdslb.com", "i1.hdslb.com"},
                    loop.create_future(),
                ),
            ]
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
            ):
                await coord._dispatch_wave(1, items)
            for it in items:
                res = await it.future
                self.assertEqual(res.status, 200)
                self.assertEqual(res.body, b"PARALLEL")
        finally:
            await coord.close()
            await client.aclose()

    async def test_close_pic_proxy_allows_new_coordinator_on_same_loop(self):
        from miku.media.proxy import _get_loop_state, _loop_states, close_pic_proxy

        closed_loop = asyncio.new_event_loop()
        old_state = _get_loop_state()
        _loop_states[closed_loop] = old_state
        closed_loop.close()

        pruned_state = _get_loop_state()
        self.assertNotIn(closed_loop, _loop_states)
        await close_pic_proxy()
        new_state = _get_loop_state()
        self.assertIsNot(new_state, pruned_state)
        self.assertFalse(new_state.coordinator._closed)
        await close_pic_proxy()

    async def test_mounted_socks_transport_detected_as_warm_and_invalid_port_502(self):
        import httpcore
        from miku.media.proxy import _has_ready_h2_connection

        class WarmConn:
            def can_handle_request(self, origin):
                return origin == httpcore.Origin(b"https", b"i0.hdslb.com", 443)

            def is_closed(self):
                return False

            def is_available(self):
                return True

            def has_expired(self):
                return False

            _connection = type("FakeH2", (), {"_h2_state": object()})()

        class FakePoolTransport(httpx.AsyncBaseTransport):
            def __init__(self, connections):
                self._pool = type("P", (), {"_connections": connections})()

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200)

        mounted_warm = FakePoolTransport([WarmConn()])
        mounted_cold = FakePoolTransport([])
        direct_warm = FakePoolTransport([WarmConn()])
        direct_cold = FakePoolTransport([])

        mounted_client = httpx.AsyncClient(transport=direct_cold, mounts={"all://": mounted_warm})
        cold_mounted_client = httpx.AsyncClient(transport=direct_warm, mounts={"all://": mounted_cold})
        direct_client = httpx.AsyncClient(transport=direct_warm)
        try:
            self.assertTrue(_has_ready_h2_connection(mounted_client, "https://i0.hdslb.com/bfs/a.jpg"))
            self.assertFalse(_has_ready_h2_connection(cold_mounted_client, "https://i0.hdslb.com/bfs/a.jpg"))
            self.assertTrue(_has_ready_h2_connection(direct_client, "https://i0.hdslb.com/bfs/a.jpg"))
        finally:
            await mounted_client.aclose()
            await cold_mounted_client.aclose()
            await direct_client.aclose()

        async def ok_handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        client = httpx.AsyncClient(transport=httpx.MockTransport(ok_handler))
        try:
            with (
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                bad_port_url = sign_pic_target("i0.hdslb.com:badport/bfs/a.jpg")
                resp = await render_proxy_pic(bad_port_url)
            self.assertEqual(resp.status_code, 502)
        finally:
            await client.aclose()

    async def test_cold_origin_waiters_do_not_starve_warm_origin_permits(self):
        import httpcore
        import miku.media.proxy as proxy_mod

        cold_headers_gate = asyncio.Event()
        cold_started = asyncio.Event()

        class FakeConn:
            def __init__(self, origin, connected=False):
                self._origin = origin
                self._connected = connected
                self._connection = type("FakeH2", (), {"_h2_state": object()})() if connected else None

            def can_handle_request(self, origin):
                return origin == self._origin

            def is_available(self):
                return self._connected

            def is_closed(self):
                return False

            def has_expired(self):
                return False

        class FakePool:
            def __init__(self):
                warm_origin = httpcore.Origin(b"https", b"i1.hdslb.com", 443)
                self._connections = [FakeConn(warm_origin, connected=True)]

            async def handle(self, req: httpx.Request) -> httpx.Response:
                if req.url.host == "i0.hdslb.com":
                    cold_started.set()
                    await cold_headers_gate.wait()
                    return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"COLD")
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"WARM")

        pool = FakePool()

        class FakeTransport(httpx.AsyncBaseTransport):
            def __init__(self, p):
                self._pool = p

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return await self._pool.handle(request)

        client = httpx.AsyncClient(transport=FakeTransport(pool))
        tight_limiter = asyncio.Semaphore(2)
        try:
            with (
                patch.object(proxy_mod, "image_limiter", tight_limiter),
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.media.proxy.image_cache.set", new=AsyncMock()),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                cold_tasks = [
                    asyncio.create_task(render_proxy_pic(sign_pic_target(f"i0.hdslb.com/bfs/cold_{i}.jpg")))
                    for i in range(6)
                ]
                await asyncio.wait_for(cold_started.wait(), timeout=1.0)
                warm_resp = await asyncio.wait_for(
                    render_proxy_pic(sign_pic_target("i1.hdslb.com/bfs/warm.jpg")),
                    timeout=1.0,
                )
                self.assertEqual(warm_resp.status_code, 200)
                cold_headers_gate.set()
                await asyncio.wait_for(asyncio.gather(*cold_tasks), timeout=2.0)
        finally:
            cold_headers_gate.set()
            await client.aclose()

    async def test_unsigned_proxy_pic_rejected_before_rate_limiter_and_neg_ttl_decays(self):
        from unittest.mock import MagicMock

        import miku.core.rate_limit as rate_limit
        import miku.media.proxy as proxy_mod
        from miku.app import app

        proxy_mod._negative_cache.clear()
        limiter = MagicMock()
        limiter.is_allowed = AsyncMock(return_value=(False, {"remaining": 0, "reset": 1, "limit": 10}))
        test_client = app.test_client()
        with (
            patch.dict(rate_limit.appconf["rate_limit"], {"enabled": True}),
            patch.object(rate_limit, "get_rate_limiter", return_value=limiter),
        ):
            r = await test_client.get("/proxy/pic/i0.hdslb.com/bfs/a.jpg")
            self.assertEqual(r.status_code, 403)
        limiter.is_allowed.assert_not_awaited()

        def handler_404(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b"missing")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler_404))
        now = [1000.0]
        try:
            with (
                patch("miku.media.proxy.time.monotonic", side_effect=lambda: now[0]),
                patch("miku.core.singleflight.time.monotonic", side_effect=lambda: now[0]),
                patch("miku.media.proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
                patch("miku.media.proxy.image_cache.get", new=AsyncMock(return_value=None)),
                patch("miku.state.Network.get_image_client", new=AsyncMock(return_value=client)),
            ):
                u = sign_pic_target("i0.hdslb.com/bfs/ttl_decay.jpg")
                r1 = await render_proxy_pic(u)
                self.assertEqual(r1.headers.get("Cache-Control"), "public, max-age=60")
                now[0] = 1045.0
                r2 = await render_proxy_pic(u)
                self.assertEqual(r2.headers.get("Cache-Control"), "public, max-age=15")
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
