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
"""Redis-backed application caching with stale-while-revalidate support."""

from __future__ import annotations

import asyncio
import functools
import re
import time
from typing import Any

import orjson
from quart import request

from miku.core.config import CACHE_SCHEMA_VERSION, CACHE_STALE_GRACE_SECONDS


def safe_json_loads(data: str | bytes, default: Any = None) -> Any:
    """Safely parse JSON with validation for dict or list."""
    if not data:
        return default
    try:
        parsed = orjson.loads(data)
        if not isinstance(parsed, (dict, list)):
            return default
        return parsed
    except (orjson.JSONDecodeError, UnicodeDecodeError, ValueError):
        return default


def versioned_key(key: str, version: int = CACHE_SCHEMA_VERSION) -> str:
    """Namespace a cache key with the payload-schema version."""
    if re.match(r"miku_v[0-9]+:", key):
        return key
    return f"miku_v{version}:{key}"


async def cache_get(redis_client, key: str, max_age_seconds: float) -> dict[str, Any] | None:
    """Return the cached payload dict if fresh, else None.

    Entries carry a ``fetched_at`` stamp; readers older than their own
    max-age treat the entry as expired. Anything unreadable (Redis down,
    corrupt JSON, legacy entries without a stamp, pre-versioning keys) is
    a silent miss so the caller falls back to live data instead of 500ing.
    """
    try:
        raw = await redis_client.get(versioned_key(key))
    except Exception:
        return None
    data = safe_json_loads(raw, default=None)
    if not isinstance(data, dict):
        return None
    try:
        age = time.time() - float(data.get("fetched_at", 0))
    except (TypeError, ValueError):
        return None
    if age < 0 or age > max_age_seconds:
        return None
    return data


async def cache_get_stale(
    redis_client,
    key: str,
    max_age_seconds: float,
    stale_window_seconds: float = CACHE_STALE_GRACE_SECONDS,
) -> tuple[dict[str, Any] | None, bool]:
    """Return ``(payload, is_stale)`` for fresh-or-expired entries.

    Fresh entries (age <= max_age) come back with ``is_stale=False``;
    expired-but-intact entries within the stale window come back with
    ``is_stale=True`` for degraded serving when upstream fails; anything
    older/unreadable returns ``(None, False)``.
    """
    try:
        raw = await redis_client.get(versioned_key(key))
    except Exception:
        return None, False
    data = safe_json_loads(raw, default=None)
    if not isinstance(data, dict):
        return None, False
    try:
        age = time.time() - float(data.get("fetched_at", 0))
    except (TypeError, ValueError):
        return None, False
    if age < 0 or age > max_age_seconds + stale_window_seconds:
        return None, False
    return data, age > max_age_seconds


async def cache_set(redis_client, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
    """Store a payload dict with a fetched_at stamp; failures are silent.

    Redis expiry is freshness TTL plus the stale-serve grace window so
    expired entries remain available to cache_get_stale.
    """
    if ttl_seconds <= 0 or not isinstance(payload, dict):
        return
    try:
        payload = dict(payload)
        payload["fetched_at"] = time.time()
        raw = orjson.dumps(payload)
        await redis_client.set(
            versioned_key(key),
            raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw),
            ex=ttl_seconds + CACHE_STALE_GRACE_SECONDS,
        )
    except Exception:
        pass


class SimpleCache:
    """View-level cache decorator with singleflight coalescing."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._inflight: dict[str, list] = {}

    def set_redis(self, redis_client) -> None:
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is not None:
            return self._redis
        import miku.state as shared

        return shared.appredis

    def _settle(self, cache_key: str, fut: asyncio.Future, ok: bool, payload: Any) -> None:
        """Settle joiners of cache_key iff fut is still the live entry."""
        entry = self._inflight.get(cache_key)
        if entry is not None and entry[0] is fut:
            del self._inflight[cache_key]
            if entry[1] and not fut.done():
                if ok:
                    fut.set_result(payload)
                else:
                    fut.set_exception(payload)

    def _join(self, cache_key: str) -> tuple[asyncio.Future, bool]:
        """Register on the in-flight render for cache_key; returns (future, is_owner)."""
        entry = self._inflight.get(cache_key)
        if entry is None:
            entry = [asyncio.get_running_loop().create_future(), 0]
            self._inflight[cache_key] = entry
            return entry[0], True
        entry[1] += 1
        return entry[0], False

    def cached(self, timeout: int = 300, key_prefix: str = "view/%s"):
        """Decorator to serve cached HTML or render once across concurrent misses."""

        def decorator(f):
            @functools.wraps(f)
            async def decorated_function(*args, **kwargs):
                cache_key = versioned_key(key_prefix % request.full_path)

                try:
                    cached_val = await self.redis.get(cache_key)
                    if cached_val:
                        return cached_val
                except Exception:
                    pass

                fut, owner = self._join(cache_key)
                if not owner:
                    try:
                        return await asyncio.shield(fut)
                    except asyncio.CancelledError:
                        cur = self._inflight.get(cache_key)
                        if cur is not None and cur[0] is fut and cur[1] > 0:
                            cur[1] -= 1
                        raise

                try:
                    response = await f(*args, **kwargs)
                    if isinstance(response, str):
                        try:
                            await self.redis.setex(cache_key, timeout, response)
                        except Exception:
                            pass
                    self._settle(cache_key, fut, True, response)
                    return response
                except asyncio.CancelledError:
                    self._settle(cache_key, fut, False, RuntimeError("cached render cancelled"))
                    raise
                except Exception as exc:
                    self._settle(cache_key, fut, False, exc)
                    raise

            return decorated_function

        return decorator
