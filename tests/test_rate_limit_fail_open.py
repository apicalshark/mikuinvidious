import unittest
from unittest.mock import AsyncMock, MagicMock

from rate_limit import RateLimiter


class TestRateLimitFailOpen(unittest.IsolatedAsyncioTestCase):
    async def test_is_allowed_fails_open_on_redis_timeout(self):
        # Mock redis pipeline that raises TimeoutError upon execute()
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=TimeoutError("Timeout reading from redis:6379"))
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis)
        allowed, info = await limiter.is_allowed("127.0.0.1:/test", limit=60, window=60)

        # Must fail open (allow request)
        self.assertTrue(allowed)
        self.assertEqual(info["limit"], 60)
        self.assertEqual(info["remaining"], 60)
        self.assertIn("reset", info)

    async def test_is_allowed_fails_open_on_redis_connection_error(self):
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Cannot connect to redis"))
        mock_redis.pipeline.return_value = mock_pipe

        limiter = RateLimiter(mock_redis)
        allowed, info = await limiter.is_allowed("127.0.0.1:/test", limit=10, window=30)

        self.assertTrue(allowed)
        self.assertEqual(info["limit"], 10)
        self.assertEqual(info["remaining"], 10)

    async def test_get_current_usage_fails_open(self):
        mock_redis = MagicMock()
        mock_redis.zremrangebyscore = AsyncMock(side_effect=TimeoutError("Redis timeout"))
        mock_redis.zcard = AsyncMock()

        limiter = RateLimiter(mock_redis)
        usage = await limiter.get_current_usage("127.0.0.1:/test", window=60)
        self.assertEqual(usage, 0)
