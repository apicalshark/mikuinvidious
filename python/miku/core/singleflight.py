# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loop-scoped cancellation-safe singleflight coalescing and bounded negative caching."""

from __future__ import annotations

import asyncio
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _InflightEntry[T]:
    task: asyncio.Task[T]
    waiters: int = 0


class SingleflightGroup[T]:
    """Coalesce concurrent async calls per key on the active event loop."""

    def __init__(self) -> None:
        self._loops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, _InflightEntry[T]]] = (
            weakref.WeakKeyDictionary()
        )
        self._loop_tasks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[asyncio.Task[T]]] = (
            weakref.WeakKeyDictionary()
        )

    def _entries(self, loop: asyncio.AbstractEventLoop) -> dict[str, _InflightEntry[T]]:
        entries = self._loops.get(loop)
        if entries is None:
            entries = {}
            self._loops[loop] = entries
        return entries

    def _tasks(self, loop: asyncio.AbstractEventLoop) -> set[asyncio.Task[T]]:
        tasks = self._loop_tasks.get(loop)
        if tasks is None:
            tasks = set()
            self._loop_tasks[loop] = tasks
        return tasks

    def _prune_closed_loops(self) -> None:
        for lp in [k for k in list(self._loops) if k.is_closed()]:
            self._loops.pop(lp, None)
        for lp in [k for k in list(self._loop_tasks) if k.is_closed()]:
            self._loop_tasks.pop(lp, None)

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        loop = asyncio.get_running_loop()
        entries = self._entries(loop)
        tasks = self._tasks(loop)
        entry = entries.get(key)
        if entry is None:

            async def _run() -> T:
                return await fn()

            task = asyncio.create_task(_run())
            entry = _InflightEntry(task=task)
            entries[key] = entry
            tasks.add(task)

            def _cleanup(done_task: asyncio.Task[T], k: str = key, ref: _InflightEntry[T] = entry) -> None:
                tasks.discard(done_task)
                if entries.get(k) is ref:
                    entries.pop(k, None)
                if not done_task.cancelled():
                    done_task.exception()

            task.add_done_callback(_cleanup)

        entry.waiters += 1
        try:
            return await asyncio.shield(entry.task)
        finally:
            entry.waiters -= 1
            if entry.waiters <= 0 and not entry.task.done():
                if entries.get(key) is entry:
                    entries.pop(key, None)
                entry.task.cancel()

    def clear(self) -> None:
        running_loop: asyncio.AbstractEventLoop | None
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        self._prune_closed_loops()
        if running_loop is None:
            return
        entries = self._loops.pop(running_loop, None)
        if entries is not None:
            for entry in list(entries.values()):
                if not entry.task.done() and not entry.task.cancelling():
                    entry.task.cancel()
            entries.clear()
        tasks = self._loop_tasks.get(running_loop)
        if tasks:
            for task in list(tasks):
                if not task.done() and not task.cancelling():
                    task.cancel()

    async def drain_current_loop(self) -> None:
        self.clear()
        running_loop = asyncio.get_running_loop()
        pending = [t for t in list(self._loop_tasks.get(running_loop, ())) if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class BoundedNegativeCache[T]:
    """Bounded in-memory LRU cache with per-entry TTL expiration."""

    def __init__(self, max_entries: int = 4096, ttl_seconds: float = 60.0) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        item = self._entries.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() >= expires_at:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: T, ttl_seconds: float | None = None) -> T:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        self._entries[key] = (time.monotonic() + ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        self._entries.clear()
