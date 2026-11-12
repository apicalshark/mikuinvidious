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
"""Concurrency primitives for async request coalescing and worker coordination.

Provides Singleflight: an async pattern that coalesces duplicate concurrent
requests for the same key into a single execution, preventing thundering herds
and duplicate upstream round-trips.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class Singleflight[T]:
    """Coalesces concurrent async operations for the same key.

    When multiple concurrent coroutines invoke ``execute(key, fn)``, only one
    coroutine actually executes ``fn()``. All other concurrent callers wait for
    and share the result of that single call.

    Key Invariants & Safety Guarantees:
    1. Cancellation Safety:
       - If a joiner (waiter) is cancelled, its cancellation does not abort the
         owner's execution or affect other joiners.
       - If the owner task is cancelled (e.g., caller timeout), joiners are
         settled with a ``RuntimeError`` instead of hanging or silently aborting.
    2. Unobserved Failure Prevention:
       - If the owner fails and nobody joined (waiters == 0), no unretrieved
         exception is attached to unawaited internal futures, avoiding asyncio
         event loop warnings.
    """

    def __init__(self) -> None:
        # key -> [future, waiter_count]
        self._inflight: dict[Any, list] = {}
        self._lock = asyncio.Lock()

    async def execute(self, key: Any, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute `fn()` for `key` or await in-flight execution if one is running."""
        async with self._lock:
            entry = self._inflight.get(key)
            if entry is None:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._inflight[key] = [fut, 0]
                is_owner = True
            else:
                entry[1] += 1
                fut = entry[0]
                is_owner = False

        if not is_owner:
            return await asyncio.shield(fut)

        try:
            result = await fn()
        except asyncio.CancelledError:
            self._settle(
                key,
                fut,
                ok=False,
                payload=RuntimeError(f"Singleflight operation for key {key!r} cancelled"),
            )
            raise
        except Exception as exc:
            self._settle(key, fut, ok=False, payload=exc)
            raise

        self._settle(key, fut, ok=True, payload=result)
        return result

    def _settle(self, key: Any, fut: asyncio.Future, ok: bool, payload: Any) -> None:
        """Settle joiners and clean up state iff fut is still the live entry.

        Synchronous so it safely runs under cancellation and cleanup.
        """
        entry = self._inflight.get(key)
        if entry is not None and entry[0] is fut:
            del self._inflight[key]
            if entry[1] and not fut.done():
                if ok:
                    fut.set_result(payload)
                else:
                    fut.set_exception(payload)

    def is_in_flight(self, key: Any) -> bool:
        """Check whether an operation for `key` is currently executing."""
        return key in self._inflight

    @property
    def in_flight_count(self) -> int:
        """Number of distinct operations currently in flight."""
        return len(self._inflight)

    def clear(self) -> None:
        """Clear all in-flight tracking and settle any waiting joiners."""
        entries = list(self._inflight.items())
        self._inflight.clear()
        for key, (fut, waiters) in entries:
            if waiters and not fut.done():
                fut.set_exception(RuntimeError(f"Singleflight cleared for key {key!r}"))
