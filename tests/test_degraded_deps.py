"""Degraded-dependency behaviour on the playback path.

Three failures used to turn a 120ms fragment into seconds of stall:

* a Redis GET against an unreachable server cost the client's full retry
  budget (measured 3.7s) because redis-py defaults to 10 exponential retries;
* the dash payload lived only in Redis, so every fragment paid that budget and
  the manifest route raised (measured 7054ms/fragment, 4708ms -> 500);
* a failed ticket refresh had no cooldown, so each fragment retried the
  upstream POST (measured 93ms).

These tests pin the fixes: a bounded client, a process-memory payload copy with
a Redis failure cooldown, a ticket failure cooldown, and stale-while-revalidate
DNS so a cold lookup never sits inline on a fragment.
"""

import asyncio
import contextlib
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import dash_fetch
import dns_cache
import shared


def _payload(expiry_offset: int = 7200, host: str = "upos-sz-mirrorcosov") -> dict:
    """Minimal playurl payload with a signature stamp *expiry_offset* away."""
    stamp = int(time.time()) + expiry_offset
    url = f"https://{host}.bilivideo.com/x.m4s?deadline={stamp}"
    return {"dash": {"video": [{"id": 32, "base_url": url, "backup_url": []}]}}


class _DashCase(unittest.IsolatedAsyncioTestCase):
    """Shared harness: stub the video client and count Redis/upstream calls."""

    async def asyncSetUp(self):
        dash_fetch._dash_mem.clear()
        dash_fetch._dash_redis_until = 0.0
        self.redis_gets = 0
        self.fetches = 0

    async def asyncTearDown(self):
        dash_fetch._dash_mem.clear()
        dash_fetch._dash_redis_until = 0.0

    def _redis(self, value=None, error=None):
        async def get(key):
            self.redis_gets += 1
            if error is not None:
                raise error
            return value

        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=get)
        redis.setex = AsyncMock()
        redis.delete = AsyncMock()
        return patch.object(dash_fetch, "appredis", redis)

    def _upstream(self, payload):
        async def fetch(_v, _idx):
            self.fetches += 1
            return payload

        @contextlib.contextmanager
        def ctx():
            # Video() validates the BV id and would build a real client: stub it.
            with (
                patch.object(dash_fetch, "video_get_dash_for_qn", fetch),
                patch.object(dash_fetch.video, "Video", lambda **_kw: object()),
            ):
                yield

        return ctx()


class DashMemoryCacheTests(_DashCase):
    async def test_second_fragment_is_served_from_memory(self):
        payload = _payload()
        with self._redis(), self._upstream(payload):
            first = await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)
            gets_after_first = self.redis_gets
            second = await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)

        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        self.assertEqual(self.fetches, 1, "second fragment must not refetch playurl")
        self.assertEqual(self.redis_gets, gets_after_first, "second fragment must not touch Redis")

    async def test_unreachable_redis_does_not_raise_and_fetches_playurl(self):
        payload = _payload()
        with self._redis(error=ConnectionError("redis down")), self._upstream(payload):
            data = await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)

        self.assertEqual(data, payload)
        self.assertEqual(self.fetches, 1)

    async def test_redis_failure_opens_a_cooldown_window(self):
        payload = _payload()
        with self._redis(error=ConnectionError("redis down")), self._upstream(payload):
            await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)
            after_failure = self.redis_gets
            self.assertGreater(dash_fetch._dash_redis_until, time.monotonic())
            dash_fetch._dash_mem.clear()  # force the Redis path again
            await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)

        self.assertEqual(self.redis_gets, after_failure, "a miss inside the cooldown must not call Redis again")
        self.assertEqual(self.fetches, 2, "the payload is still refetched, just without Redis")

    async def test_successful_redis_call_clears_the_cooldown(self):
        with self._redis(), self._upstream(_payload()):
            dash_fetch._dash_redis_until = time.monotonic() + 60
            await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)
            self.assertEqual(self.redis_gets, 0, "cooldown must skip Redis")

            dash_fetch._dash_redis_until = time.monotonic() + 60
            await dash_fetch._dash_redis_get("probe")  # inside the window: skipped
            self.assertEqual(self.redis_gets, 0)

            dash_fetch._dash_redis_until = 0.0
            await dash_fetch._load_dash_data("BV1TxxC6vEo5", 0)
        self.assertEqual(self.redis_gets, 2, "a live Redis is used again")

    async def test_refresh_drops_the_memory_copy(self):
        old = _payload()
        new = _payload(host="upos-sz-mirrorhw")
        with self._redis(), self._upstream(old):
            self.assertEqual(await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0), old)
        with self._redis(), self._upstream(new):
            refreshed = await dash_fetch._refresh_dash_data("BV1TxxC6vEo4", 0)
        self.assertEqual(refreshed, new)

    async def test_expired_signature_is_not_served_from_memory(self):
        stale = _payload(expiry_offset=-3600)
        fresh = _payload()
        with self._redis(), self._upstream(stale):
            await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)
        with self._redis(), self._upstream(fresh):
            data = await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)
        self.assertEqual(data["dash"]["video"][0]["base_url"], fresh["dash"]["video"][0]["base_url"])
        self.assertEqual(self.fetches, 2)

    async def test_cache_write_failure_still_returns_the_payload(self):
        payload = _payload()

        async def down(*_a, **_k):
            raise ConnectionError("redis down")

        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=down)
        redis.setex = AsyncMock(side_effect=down)
        with patch.object(dash_fetch, "appredis", redis), self._upstream(payload):
            data = await dash_fetch._load_dash_data("BV1TxxC6vEo4", 0)
        self.assertEqual(data, payload)


class RedisClientBoundTests(unittest.TestCase):
    def test_client_has_a_bounded_retry_budget(self):
        kwargs = shared.appredis.connection_pool.connection_kwargs
        self.assertEqual(kwargs["socket_timeout"], shared.REDIS_SOCKET_TIMEOUT)
        self.assertEqual(kwargs["socket_connect_timeout"], shared.REDIS_SOCKET_TIMEOUT)
        self.assertLessEqual(shared.REDIS_SOCKET_TIMEOUT, 2.0)
        self.assertEqual(kwargs["retry"]._retries, 0, "redis-py would retry 10x by default")


class TicketCooldownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        shared.TicketManager._ticket = None
        shared.TicketManager._expiry = 0
        shared.TicketManager._inflight = None
        shared.TicketManager._fail_until = 0.0

    async def asyncTearDown(self):
        shared.TicketManager._fail_until = 0.0

    async def test_failed_refresh_is_not_retried_inside_the_window(self):
        attempts = 0

        async def failing(*_a, **_k):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("ticket service down")

        with (
            patch.object(shared, "get_bili_ticket", failing),
            patch.object(shared.appredis, "mget", AsyncMock(return_value=(None, None))),
        ):
            for _ in range(3):
                self.assertIsNone(await shared.TicketManager.get_ticket())

        self.assertEqual(attempts, 1, "one upstream attempt per cooldown window")

    async def test_cooldown_expires(self):
        attempts = 0

        async def failing(*_a, **_k):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("down")

        with (
            patch.object(shared, "get_bili_ticket", failing),
            patch.object(shared.appredis, "mget", AsyncMock(return_value=(None, None))),
        ):
            await shared.TicketManager.get_ticket()
            shared.TicketManager._fail_until = 0.0  # window elapsed
            await shared.TicketManager.get_ticket()

        self.assertEqual(attempts, 2)

    async def test_success_clears_the_cooldown(self):
        async def ok(*_a, **_k):
            return "ticket-value", int(time.time()) + 3600

        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[True, True])
        with (
            patch.object(shared, "get_bili_ticket", ok),
            patch.object(shared.appredis, "mget", AsyncMock(return_value=(None, None))),
            patch.object(shared.appredis, "pipeline", lambda **_k: pipe),
        ):
            ticket = await shared.TicketManager.get_ticket()

        self.assertEqual(ticket, "ticket-value")
        self.assertEqual(shared.TicketManager._fail_until, 0.0)


class DnsStaleWhileRevalidateTests(unittest.IsolatedAsyncioTestCase):
    HOST = "upos-sz-mirrorcosov.bilivideo.com"

    async def asyncSetUp(self):
        dns_cache.clear_cache()
        self.lookups = 0
        self.info = [(2, 1, 6, "", ("203.0.113.7", 0))]

    async def asyncTearDown(self):
        dns_cache.clear_cache()

    def _lookup(self, fail_after=None):
        def blocking(hostname, *a, **k):
            self.lookups += 1
            if fail_after is not None and self.lookups > fail_after:
                raise OSError("resolver down")
            return self.info

        return blocking

    async def _settle_refresh(self):
        """Wait (bounded, no fixed sleep) for the background task to finish."""
        task = dns_cache._refreshing.get(self.HOST)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 2.0)

    async def test_stale_entry_is_served_then_revalidated(self):
        clock = [1000.0]
        with (
            patch.object(dns_cache.time, "monotonic", lambda: clock[0]),
            patch.object(dns_cache.socket, "getaddrinfo", self._lookup()),
        ):
            first = await dns_cache.resolve_host(self.HOST)
            self.assertEqual(self.lookups, 1)

            clock[0] += dns_cache.DNS_CACHE_TTL - 1
            self.assertEqual(await dns_cache.resolve_host(self.HOST), first)
            self.assertEqual(self.lookups, 1, "inside the TTL must not resolve again")

            clock[0] += 2  # expired, still inside the grace window
            self.assertEqual(await dns_cache.resolve_host(self.HOST), first)
            await self._settle_refresh()

        self.assertEqual(self.lookups, 2, "background revalidation must run")

    async def test_entry_past_the_grace_blocks_on_a_lookup(self):
        clock = [2000.0]
        with (
            patch.object(dns_cache.time, "monotonic", lambda: clock[0]),
            patch.object(dns_cache.socket, "getaddrinfo", self._lookup()),
        ):
            await dns_cache.resolve_host(self.HOST)
            clock[0] += dns_cache.DNS_CACHE_TTL + dns_cache.DNS_STALE_GRACE + 1
            await dns_cache.resolve_host(self.HOST)
        self.assertEqual(self.lookups, 2, "a too-stale entry must be resolved again inline")

    async def test_failed_revalidation_keeps_the_previous_answer(self):
        clock = [3000.0]
        with (
            patch.object(dns_cache.time, "monotonic", lambda: clock[0]),
            patch.object(dns_cache.socket, "getaddrinfo", self._lookup(fail_after=1)),
        ):
            first = await dns_cache.resolve_host(self.HOST)
            clock[0] += dns_cache.DNS_CACHE_TTL + 1
            self.assertEqual(await dns_cache.resolve_host(self.HOST), first)
            await self._settle_refresh()
        self.assertEqual(self.lookups, 2)


if __name__ == "__main__":
    unittest.main()
