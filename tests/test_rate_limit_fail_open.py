# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest
from unittest.mock import AsyncMock, MagicMock

from miku.core.rate_limit import RateLimiter


class TestRateLimitFailOpen(unittest.IsolatedAsyncioTestCase):
    async def test_is_allowed_uses_in_memory_fallback_on_redis_timeout(self):
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=TimeoutError("Timeout reading from redis:6379"))
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis)
        allowed, info = await limiter.is_allowed("127.0.0.1:/test", limit=60, window=60)

        self.assertTrue(allowed)
        self.assertEqual(info["limit"], 60)
        self.assertEqual(info["remaining"], 59)
        self.assertIn("reset", info)

    async def test_is_allowed_enforces_limit_in_fallback_on_redis_connection_error(self):
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Cannot connect to redis"))
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis)
        for idx in range(3):
            allowed, info = await limiter.is_allowed("127.0.0.1:/strict", limit=3, window=30)
            self.assertTrue(allowed)
            self.assertEqual(info["remaining"], 2 - idx)

        allowed, info = await limiter.is_allowed("127.0.0.1:/strict", limit=3, window=30)
        self.assertFalse(allowed)
        self.assertEqual(info["remaining"], 0)
        self.assertGreaterEqual(info["retry_after"], 1)

    async def test_fallback_evicts_oldest_keys_when_bounded(self):
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Cannot connect to redis"))
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis, fallback_max_keys=2)
        await limiter.is_allowed("ip1:/a", limit=5, window=60)
        await limiter.is_allowed("ip2:/a", limit=5, window=60)
        await limiter.is_allowed("ip3:/a", limit=5, window=60)

        self.assertEqual(len(limiter._fallback_buckets), 2)
        self.assertNotIn("ip1:/a", limiter._fallback_buckets)

    async def test_get_current_usage_uses_fallback(self):
        mock_redis = MagicMock()
        mock_redis.zremrangebyscore = AsyncMock(side_effect=TimeoutError("Redis timeout"))
        mock_redis.zcard = AsyncMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=TimeoutError("Redis timeout"))
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis)
        self.assertEqual(await limiter.get_current_usage("127.0.0.1:/test", window=60), 0)
        await limiter.is_allowed("127.0.0.1:/test", limit=5, window=60)
        self.assertEqual(await limiter.get_current_usage("127.0.0.1:/test", window=60), 1)

    async def test_non_positive_limit_does_not_grow_fallback_store(self):
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis, fallback_max_keys=2)
        for i in range(5):
            allowed, info = await limiter.is_allowed(f"10.0.0.{i}:/test", limit=0, window=60)
            self.assertFalse(allowed)
            self.assertEqual(info["remaining"], 0)
        self.assertEqual(len(limiter._fallback_buckets), 0)

    async def test_default_key_uses_route_template_instead_of_expanded_path(self):
        from unittest.mock import patch

        from miku.core import rate_limit as rl
        from quart import Quart

        app = Quart(__name__)

        @app.route("/item/<item_id>")
        @rl.rate_limit(limit=2, window=60)
        async def get_item(item_id):
            return item_id

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis.pipeline.return_value = mock_pipe
        limiter = RateLimiter(mock_redis, fallback_max_keys=16)

        with (
            patch.dict(rl.appconf["rate_limit"], {"enabled": True}),
            patch.object(rl, "get_rate_limiter", return_value=limiter),
        ):
            client = app.test_client()
            r1 = await client.get("/item/aaa")
            r2 = await client.get("/item/bbb")
            r3 = await client.get("/item/ccc")
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r3.status_code, 429)
            self.assertEqual(len(limiter._fallback_buckets), 1)
