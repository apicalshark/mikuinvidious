# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.
import asyncio
import unittest

from miku.services.concurrency import Singleflight


class TestSingleflight(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sf = Singleflight()

    async def test_single_execution(self):
        call_count = 0

        async def work():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return "ok"

        res = await self.sf.execute("k1", work)
        self.assertEqual(res, "ok")
        self.assertEqual(call_count, 1)
        self.assertFalse(self.sf.is_in_flight("k1"))
        self.assertEqual(self.sf.in_flight_count, 0)

    async def test_concurrent_coalescing(self):
        call_count = 0
        started = asyncio.Event()

        async def work():
            nonlocal call_count
            call_count += 1
            started.set()
            await asyncio.sleep(0.05)
            return {"data": 42}

        async def call_worker():
            return await self.sf.execute("k1", work)

        task1 = asyncio.create_task(call_worker())
        await started.wait()
        task2 = asyncio.create_task(call_worker())
        task3 = asyncio.create_task(call_worker())

        r1, r2, r3 = await asyncio.gather(task1, task2, task3)
        self.assertEqual(r1, {"data": 42})
        self.assertEqual(r2, {"data": 42})
        self.assertEqual(r3, {"data": 42})
        self.assertEqual(call_count, 1)
        self.assertFalse(self.sf.is_in_flight("k1"))

    async def test_distinct_keys_execute_independently(self):
        counts = {"a": 0, "b": 0}

        async def work(key):
            counts[key] += 1
            await asyncio.sleep(0.02)
            return f"val_{key}"

        r_a, r_b = await asyncio.gather(
            self.sf.execute("a", lambda: work("a")),
            self.sf.execute("b", lambda: work("b")),
        )
        self.assertEqual(r_a, "val_a")
        self.assertEqual(r_b, "val_b")
        self.assertEqual(counts["a"], 1)
        self.assertEqual(counts["b"], 1)

    async def test_subsequent_calls_re_execute(self):
        counter = 0

        async def work():
            nonlocal counter
            counter += 1
            return counter

        r1 = await self.sf.execute("k", work)
        r2 = await self.sf.execute("k", work)
        self.assertEqual(r1, 1)
        self.assertEqual(r2, 2)

    async def test_exception_propagation_to_waiters(self):
        call_count = 0
        started = asyncio.Event()

        class CustomError(Exception):
            pass

        async def failing_work():
            nonlocal call_count
            call_count += 1
            started.set()
            await asyncio.sleep(0.02)
            raise CustomError("upstream failed")

        task1 = asyncio.create_task(self.sf.execute("k_err", failing_work))
        await started.wait()
        task2 = asyncio.create_task(self.sf.execute("k_err", failing_work))

        with self.assertRaises(CustomError):
            await task1
        with self.assertRaises(CustomError):
            await task2

        self.assertEqual(call_count, 1)
        self.assertFalse(self.sf.is_in_flight("k_err"))

    async def test_owner_cancellation_settles_waiters(self):
        started = asyncio.Event()

        async def slow_work():
            started.set()
            await asyncio.sleep(1.0)
            return "done"

        owner_task = asyncio.create_task(self.sf.execute("k_cancel", slow_work))
        await started.wait()
        waiter_task = asyncio.create_task(self.sf.execute("k_cancel", slow_work))

        # Cancel the owner task
        owner_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await owner_task

        # Waiter should receive RuntimeError indicating cancellation, not hang!
        with self.assertRaises(RuntimeError) as ctx:
            await waiter_task
        self.assertIn("cancelled", str(ctx.exception).lower())
        self.assertFalse(self.sf.is_in_flight("k_cancel"))

    async def test_waiter_cancellation_preserves_owner(self):
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.sleep(0.05)
            return "owner_finished"

        owner_task = asyncio.create_task(self.sf.execute("k_waiter_cancel", work))
        await started.wait()
        waiter_task = asyncio.create_task(self.sf.execute("k_waiter_cancel", work))

        # Cancel waiter only
        waiter_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter_task

        # Owner should complete normally
        owner_res = await owner_task
        self.assertEqual(owner_res, "owner_finished")


if __name__ == "__main__":
    unittest.main()
