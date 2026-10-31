"""Playback mirror sweep is bounded by time, not by a mirror count.

A refusing edge fails in ~100ms, so a count cap (the old `_PLAYBACK_MAX_MIRRORS
= 2`) threw away reachable mirrors: dash.js saw a 502 and retried from scratch.
A stalled edge is the opposite — its 4s handshake already spends the fragment
budget, so stacking a second one outlives the client's ~7s abandonment.
"""

import asyncio
import unittest
from unittest.mock import patch

import dash_common
import dash_stream
import stream

URLS = [f"https://upos-sz-mirror{i}.bilivideo.com/obj/x.m4s" for i in range(1, 5)]


class _Clock:
    """Stand-in for ``dash_stream.time`` so attempts can 'cost' seconds instantly."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return 1767225600.0


class _Resp:
    status_code = 206
    headers = {"content-length": "100", "content-range": "bytes 0-99/1000"}


class _Conn:
    async def close(self):
        pass


class MirrorBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dash_common._mirror_health.clear()
        dash_common._mirror_range_shy.clear()

    async def _sweep(self, costs, budget, succeed_at=None, urls=URLS):
        clock = _Clock()
        attempts = []

        async def fake_attempt(url, headers, proxy_url, timeout=None):
            index = len(attempts)
            attempts.append(url)
            clock.now += costs[index]
            if succeed_at is not None and index == succeed_at:
                return _Conn(), _Resp()
            raise stream.CdnConnectError("simulated edge failure")

        with (
            patch.object(dash_stream, "_fetch_dash_attempt", fake_attempt),
            patch.object(dash_stream, "time", clock),
        ):
            try:
                result = await dash_stream._fetch_dash_track(
                    urls, {"range": "bytes=0-99"}, "", "test", mirror_budget=budget
                )
            except dash_stream._DashUpstreamError:
                result = None
        return attempts, result

    async def test_cheap_failures_reach_every_mirror(self):
        """Four fast failures all happen inside the budget: no mirror is wasted."""
        attempts, result = await self._sweep([0.05] * 4, budget=3.0)
        self.assertIsNone(result)
        self.assertEqual(attempts, URLS)

    async def test_success_after_two_fast_failures(self):
        """The old two-mirror cap returned 502 here; the sweep now serves."""
        attempts, result = await self._sweep([0.05] * 4, budget=3.0, succeed_at=2)
        self.assertEqual(attempts, URLS[:3])
        self.assertEqual(result[2], URLS[2])

    async def test_stalled_edge_still_gets_the_mandatory_failover(self):
        """A 4s stall spends the budget, but the second mirror is still tried."""
        attempts, result = await self._sweep([4.0, 4.0, 4.0, 4.0], budget=3.0)
        self.assertIsNone(result)
        self.assertEqual(len(attempts), 2)

    async def test_stall_then_healthy_mirror_still_recovers(self):
        """Failing over past a stall must not regress into a 502."""
        attempts, result = await self._sweep([4.0, 0.05, 0.05, 0.05], budget=3.0, succeed_at=1)
        self.assertEqual(attempts, URLS[:2])
        self.assertEqual(result[2], URLS[1])

    async def test_budget_bounds_mirrors_after_the_mandatory_pair(self):
        attempts, result = await self._sweep([1.6] * 4, budget=3.0)
        self.assertIsNone(result)
        self.assertEqual(len(attempts), 2)  # 1.6s each: 3.2s is past the budget

    async def test_budget_still_allows_a_third_cheap_mirror(self):
        attempts, result = await self._sweep([1.2] * 4, budget=3.0)
        self.assertIsNone(result)
        self.assertEqual(len(attempts), 3)  # 2.4s < 3.0s allows one more; 3.6s stops

    async def test_downloads_keep_the_unbounded_sweep(self):
        attempts, result = await self._sweep([10.0] * 4, budget=None)
        self.assertIsNone(result)
        self.assertEqual(attempts, URLS)

    async def test_first_mirror_is_always_attempted(self):
        attempts, result = await self._sweep([99.0], budget=0.001, succeed_at=0)
        self.assertEqual(attempts, URLS[:1])
        self.assertIsNotNone(result)


class _RangeServer:
    """Minimal 206 origin used as the last mirror in the sweep."""

    def __init__(self):
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}/track.m4s"

    async def close(self):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                head += chunk
            body = b"x" * 100
            writer.write(
                b"HTTP/1.1 206 Partial Content\r\nContent-Length: 100\r\n"
                b"Content-Range: bytes 0-99/1000\r\nConnection: close\r\n\r\n" + body
            )
            await writer.drain()
        finally:
            writer.close()


class MirrorBudgetEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dash_common._mirror_health.clear()

    async def test_refused_mirrors_no_longer_hide_a_reachable_one(self):
        server = await _RangeServer().start()
        try:
            dead = [f"http://127.0.0.1:1/dead{i}.m4s" for i in range(3)]
            urls = [*dead, server.url]
            conn, _resp, used, length, _ = await dash_stream._fetch_dash_track(
                urls, {"range": "bytes=0-99"}, "", "test", mirror_budget=3.0
            )
            await conn.close()
            self.assertEqual(used, server.url)
            self.assertEqual(length, "100")

            # Old behaviour: the sweep stopped after two candidates.
            with self.assertRaises(dash_stream._DashUpstreamError):
                await dash_stream._fetch_dash_track(
                    urls[:2],
                    {"range": "bytes=0-99"},
                    "",
                    "test",
                    mirror_budget=3.0,
                )
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
