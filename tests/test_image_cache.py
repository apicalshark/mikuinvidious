import asyncio
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from image_cache import ImageCache
from proxy import render_proxy_pic


class TestImageCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="miku_test_img_cache_")
        self.cache = ImageCache(cache_dir=self.test_dir, max_size_bytes=1024 * 1024, ttl_seconds=3600)

    async def asyncTearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_cache_miss_returns_none(self):
        result = await self.cache.get("https://i0.hdslb.com/bfs/nonexistent.jpg")
        self.assertIsNone(result)

    async def test_cache_set_and_get_roundtrip(self):
        url = "https://i0.hdslb.com/bfs/archive/test.jpg"
        content = b"\xff\xd8\xff\xe0\x00\x10JFIFimagecontent"
        content_type = "image/jpeg"

        await self.cache.set(url, content, content_type)
        cached = await self.cache.get(url)

        self.assertIsNotNone(cached)
        cached_content, cached_type = cached
        self.assertEqual(cached_content, content)
        self.assertEqual(cached_type, content_type)

    async def test_cache_ttl_expiration(self):
        # Cache with 1 second TTL
        short_cache = ImageCache(cache_dir=self.test_dir, max_size_bytes=1024 * 1024, ttl_seconds=1)
        url = "https://i0.hdslb.com/bfs/archive/expire.jpg"
        content = b"image_data"

        await short_cache.set(url, content, "image/jpeg")
        # Immediately available
        self.assertIsNotNone(await short_cache.get(url))

        # Manually backdate the file's timestamp in the cache header
        key = short_cache._url_to_key(url)
        path = os.path.join(self.test_dir, f"{key}.cache")
        # Format: content_type\ncreated_at\n\ncontent
        old_data = b"image/jpeg\n" + str(int(time.time()) - 100).encode() + b"\n\nimage_data"
        with open(path, "wb") as f:
            f.write(old_data)

        # Should now be expired and return None
        self.assertIsNone(await short_cache.get(url))

    async def test_corrupted_cache_file_recovers_gracefully(self):
        url = "https://i0.hdslb.com/bfs/archive/corrupt.jpg"
        key = self.cache._url_to_key(url)
        path = os.path.join(self.test_dir, f"{key}.cache")

        # Write invalid/corrupt data without the expected format
        with open(path, "wb") as f:
            f.write(b"garbage-no-newline")

        # Must return None instead of raising an exception
        result = await self.cache.get(url)
        self.assertIsNone(result)

    async def test_cache_quota_pruning(self):
        # Cache limited to 500 bytes
        tiny_cache = ImageCache(cache_dir=self.test_dir, max_size_bytes=500, ttl_seconds=3600)

        # Store 5 items of 150 bytes each (total 750 bytes > 500 bytes)
        for i in range(5):
            url = f"https://i0.hdslb.com/bfs/archive/pic_{i}.jpg"
            data = b"X" * 150
            await tiny_cache.set(url, data, "image/jpeg")
            await asyncio.sleep(0.01)  # ensure different timestamps

        # Force a prune
        await tiny_cache.prune_if_needed()

        # The oldest items should be pruned to keep total size under limit
        total_size = sum(
            os.path.getsize(os.path.join(self.test_dir, f))
            for f in os.listdir(self.test_dir)
            if f.endswith(".cache")
        )
        self.assertLessEqual(total_size, 500)


class TestProxyPicDiskCacheIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="miku_test_proxy_cache_")

    async def asyncTearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_proxy_pic_caches_on_first_request_and_hits_on_second(self):
        custom_cache = ImageCache(cache_dir=self.test_dir)

        # Mock transport returning image bytes
        mock_send = AsyncMock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg"},
                content=b"\xff\xd8\xff\xe0\x00\x10JFIFfirstfetch",
            )
        )
        mock_client = AsyncMock()
        mock_client.build_request = httpx.AsyncClient().build_request
        mock_client.send = mock_send

        with (
            patch("proxy.is_safe_proxy_url", new=AsyncMock(return_value=True)),
            patch("proxy.image_cache", custom_cache),
            patch("shared.Network.get_image_client", new=AsyncMock(return_value=mock_client)),
        ):
            url_path = "/proxy/pic/i0.hdslb.com/bfs/archive/hit_test.jpg"

            # 1. First request: Cache MISS -> fetches from upstream
            resp1 = await render_proxy_pic(url_path)
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(resp1.headers.get("X-Cache"), "MISS")
            self.assertEqual(await resp1.get_data(), b"\xff\xd8\xff\xe0\x00\x10JFIFfirstfetch")
            self.assertEqual(mock_send.call_count, 1)

            # Verify file exists in disk cache
            cached_files = [f for f in os.listdir(self.test_dir) if f.endswith(".cache")]
            self.assertEqual(len(cached_files), 1)

            # 2. Second request: Cache HIT -> served from disk, upstream NOT called!
            resp2 = await render_proxy_pic(url_path)
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(resp2.headers.get("X-Cache"), "HIT")
            self.assertEqual(await resp2.get_data(), b"\xff\xd8\xff\xe0\x00\x10JFIFfirstfetch")
            # Upstream send count should still be 1!
            self.assertEqual(mock_send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
