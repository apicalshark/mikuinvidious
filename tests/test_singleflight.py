# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import unittest
from unittest.mock import patch

from miku.core.singleflight import BoundedNegativeCache, SingleflightGroup


class TestSingleflightGroup(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_concurrent_calls_and_isolates_partial_cancel(self):
        sf = SingleflightGroup()
        calls = 0
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def work():
            nonlocal calls
            calls += 1
            started.set()
            await proceed.wait()
            return {"ok": True}

        tasks = [asyncio.create_task(sf.do("k1", work)) for _ in range(5)]
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await asyncio.sleep(0.01)

        tasks[1].cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tasks[1]

        proceed.set()
        results = await asyncio.gather(tasks[0], tasks[2], tasks[3], tasks[4])
        self.assertEqual(calls, 1)
        self.assertEqual(results, [{"ok": True}] * 4)

    async def test_cancels_leader_when_all_waiters_cancel_and_allows_retry(self):
        sf = SingleflightGroup()
        calls = 0
        started = asyncio.Event()
        cancelled_seen = asyncio.Event()

        async def work():
            nonlocal calls
            calls += 1
            started.set()
            try:
                await asyncio.sleep(5)
                return "done"
            except asyncio.CancelledError:
                cancelled_seen.set()
                raise

        t1 = asyncio.create_task(sf.do("k2", work))
        t2 = asyncio.create_task(sf.do("k2", work))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        t1.cancel()
        t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        await asyncio.wait_for(cancelled_seen.wait(), timeout=1.0)

        async def work2():
            nonlocal calls
            calls += 1
            return "recovered"

        self.assertEqual(await sf.do("k2", work2), "recovered")
        self.assertEqual(calls, 2)

    async def test_exception_propagates_to_all_waiters_and_clears_inflight(self):
        sf = SingleflightGroup()
        calls = 0
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def failing():
            nonlocal calls
            calls += 1
            started.set()
            await proceed.wait()
            raise RuntimeError("boom")

        t1 = asyncio.create_task(sf.do("k3", failing))
        t2 = asyncio.create_task(sf.do("k3", failing))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        proceed.set()

        r1, r2 = await asyncio.gather(t1, t2, return_exceptions=True)
        self.assertIsInstance(r1, RuntimeError)
        self.assertIsInstance(r2, RuntimeError)

        async def ok():
            nonlocal calls
            calls += 1
            return 42

        self.assertEqual(await sf.do("k3", ok), 42)
        self.assertEqual(calls, 2)

    async def test_do_accepts_completed_future_factory(self):
        sf = SingleflightGroup()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result("from-future")
        self.assertEqual(await sf.do("fut", lambda: fut), "from-future")

    async def test_clear_preserves_other_live_loops_and_drain_awaits_cancelled_cleanup(self):
        import threading

        sf = SingleflightGroup()
        other_ready = threading.Event()
        other_release = threading.Event()
        other_result: list[str] = []

        def run_other_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def inner():
                async def slow():
                    other_ready.set()
                    while not other_release.is_set():
                        await asyncio.sleep(0.01)
                    return "other-ok"

                other_result.append(await sf.do("shared", slow))

            try:
                loop.run_until_complete(inner())
            finally:
                loop.close()

        t = threading.Thread(target=run_other_loop)
        t.start()
        self.assertTrue(other_ready.wait(timeout=2.0))

        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def current_work():
            started.set()
            try:
                await asyncio.sleep(5)
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        waiter = asyncio.create_task(sf.do("shared", current_work))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)

        self.assertFalse(cleaned.is_set())
        await asyncio.wait_for(sf.drain_current_loop(), timeout=1.0)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(len(sf._loops), 1)

        other_release.set()
        t.join(timeout=2.0)
        self.assertEqual(other_result, ["other-ok"])


class TestBoundedNegativeCache(unittest.TestCase):
    def test_lru_eviction_and_ttl_expiry(self):
        cache = BoundedNegativeCache(max_entries=2, ttl_seconds=10.0)
        cache.set("k1", 404)
        cache.set("k2", 410)
        self.assertEqual(cache.get("k1"), 404)
        cache.set("k3", 404)
        self.assertIsNone(cache.get("k2"))
        self.assertEqual(cache.get("k1"), 404)
        self.assertEqual(cache.get("k3"), 404)

        with patch("miku.core.singleflight.time.monotonic", return_value=100.0):
            ttl_cache = BoundedNegativeCache(max_entries=10, ttl_seconds=5.0)
            ttl_cache.set("exp", 404)
        with patch("miku.core.singleflight.time.monotonic", return_value=104.0):
            self.assertEqual(ttl_cache.get("exp"), 404)
        with patch("miku.core.singleflight.time.monotonic", return_value=106.0):
            self.assertIsNone(ttl_cache.get("exp"))


if __name__ == "__main__":
    unittest.main()
