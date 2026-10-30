import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from proxy import render_proxy_pic
from shared import image_limiter


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
            patch("proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("shared.Network.get_async_client", new=AsyncMock(return_value=client)),
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
            patch("proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("shared.Network.get_async_client", new=AsyncMock(return_value=client)),
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
            patch("proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("shared.Network.get_async_client", new=AsyncMock(return_value=client)),
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
            patch("proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("shared.Network.get_async_client", new=AsyncMock(return_value=client)),
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
        with patch("proxy.is_safe_proxy_url", new=AsyncMock(return_value=False)):
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
            patch("proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("shared.Network.get_async_client", new=AsyncMock(return_value=client)),
        ):
            resp = await render_proxy_pic("/proxy/pic/i0.hdslb.com/bfs/archive/huge.jpg")
            self.assertEqual(resp.status_code, 413)

        await client.aclose()


if __name__ == "__main__":
    unittest.main()
