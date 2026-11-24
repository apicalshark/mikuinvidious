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

    async def test_redis_cooldown_skips_redis_and_recovers_after_expiry(self):
        from unittest.mock import patch

        from redis.exceptions import MaxConnectionsError

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(
            side_effect=[
                MaxConnectionsError("Too many connections"),
                [1, 0, 1, True],
            ]
        )
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis, redis_cooldown=30.0)
        with patch("miku.core.rate_limit.time.monotonic", side_effect=[100.0, 110.0, 130.0]):
            allowed1, info1 = await limiter.is_allowed("127.0.0.1:/a", limit=5, window=60)
            allowed2, info2 = await limiter.is_allowed("127.0.0.1:/a", limit=5, window=60)
            allowed3, info3 = await limiter.is_allowed("127.0.0.1:/a", limit=5, window=60)

        self.assertTrue(allowed1)
        self.assertTrue(allowed2)
        self.assertTrue(allowed3)
        self.assertEqual(info1["remaining"], 4)
        self.assertEqual(info2["remaining"], 3)
        self.assertEqual(info3["remaining"], 4)
        self.assertEqual(mock_pipe.execute.await_count, 2)
        mock_redis.pipeline.assert_called_with()

    async def test_late_redis_success_does_not_clear_active_cooldown(self):
        import asyncio
        import time
        from unittest.mock import patch

        mock_redis = MagicMock()
        in_flight_started = asyncio.Event()
        allow_in_flight_finish = asyncio.Event()

        async def slow_execute():
            in_flight_started.set()
            await allow_in_flight_finish.wait()
            return [1, 0, 1, True]

        slow_pipe = MagicMock()
        slow_pipe.execute = AsyncMock(side_effect=slow_execute)
        fail_pipe = MagicMock()
        fail_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis.pipeline.side_effect = [slow_pipe, fail_pipe]

        limiter = RateLimiter(mock_redis, redis_cooldown=30.0)
        base = time.monotonic()
        clock = [base]
        with patch("miku.core.rate_limit.time.monotonic", side_effect=lambda: clock[0]):
            task_slow = asyncio.create_task(limiter.is_allowed("127.0.0.1:/slow", limit=5, window=60))
            await in_flight_started.wait()

            clock[0] = base + 1.0
            allowed_fail, _ = await limiter.is_allowed("127.0.0.1:/fail", limit=5, window=60)
            self.assertTrue(allowed_fail)
            self.assertEqual(limiter._redis_until, base + 31.0)

            clock[0] = base + 2.0
            allow_in_flight_finish.set()
            allowed_slow, _ = await task_slow
            self.assertTrue(allowed_slow)
            self.assertEqual(limiter._redis_until, base + 31.0)

            allowed_after, _ = await limiter.is_allowed("127.0.0.1:/after", limit=5, window=60)
            self.assertTrue(allowed_after)

        self.assertEqual(mock_redis.pipeline.call_count, 2)

    async def test_in_memory_mode_enforces_limits_without_redis(self):
        limiter = RateLimiter(None)
        self.assertEqual(await limiter.get_current_usage("127.0.0.1:/mem", window=60), 0)

        for i in range(2):
            allowed, info = await limiter.is_allowed("127.0.0.1:/mem", limit=2, window=60)
            self.assertTrue(allowed)
            self.assertEqual(info["remaining"], 1 - i)

        allowed, info = await limiter.is_allowed("127.0.0.1:/mem", limit=2, window=60)
        self.assertFalse(allowed)
        self.assertEqual(info["remaining"], 0)
        self.assertEqual(await limiter.get_current_usage("127.0.0.1:/mem", window=60), 2)

    async def test_get_rate_limiter_selects_backend(self):
        from unittest.mock import patch

        from miku.core import rate_limit as rl

        prev = rl._rate_limiter
        try:
            rl._rate_limiter = None
            with patch.dict(rl.appconf["rate_limit"], {"backend": "memory"}):
                mem_limiter = rl.get_rate_limiter()
                self.assertIsNone(mem_limiter.redis)

            rl._rate_limiter = None
            with patch.dict(rl.appconf["rate_limit"], {"backend": "redis"}):
                redis_limiter = rl.get_rate_limiter()
                self.assertIs(redis_limiter.redis, rl.appredis)
        finally:
            rl._rate_limiter = prev
