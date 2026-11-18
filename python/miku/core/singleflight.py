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

    def _entries(self) -> dict[str, _InflightEntry[T]]:
        loop = asyncio.get_running_loop()
        entries = self._loops.get(loop)
        if entries is None:
            entries = {}
            self._loops[loop] = entries
        return entries

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        entries = self._entries()
        entry = entries.get(key)
        if entry is None:
            task = asyncio.create_task(fn())
            entry = _InflightEntry(task=task)
            entries[key] = entry

            def _cleanup(done_task: asyncio.Task[T], k: str = key, ref: _InflightEntry[T] = entry) -> None:
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

        for loop, entries in list(self._loops.items()):
            if loop is running_loop:
                for entry in list(entries.values()):
                    if not entry.task.done():
                        entry.task.cancel()
            entries.clear()


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
