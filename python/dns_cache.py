# Copyright (C) 2026 MikuInvidious Team
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

"""Tiny TTL cache for ``getaddrinfo`` used by the SSRF guards.

Both ``proxy.is_safe_proxy_url`` and ``dash_proxy._is_safe_dash_url_async``
resolve the target hostname on *every* request just to reject private IPs.
Uncached lookups cost 50ms-1s (measured ~1s for some ``*.bilivideo.com``
edges) and run on the DASH critical path once per candidate URL.

Caching does not weaken the guard: the check was already time-of-check
versus httpx's own time-of-use resolution, and entries expire after
``DNS_CACHE_TTL`` seconds (default 120s). Concurrent misses for the same
hostname coalesce behind one lookup (singleflight).
"""

import asyncio
import socket
import time

DNS_CACHE_TTL = 120.0

_cache: dict[str, tuple[float, list]] = {}
# hostname -> [future, waiter_count]; the future is settled only when someone
# waits on it, so unobserved failures never log "exception was never retrieved".
_inflight: dict[str, list] = {}
_lock = asyncio.Lock()


async def _settle(hostname: str, ok: bool, payload):
    async with _lock:
        entry = _inflight.pop(hostname, None)
    if entry is not None:
        fut, waiters = entry
        if waiters and not fut.done():
            if ok:
                fut.set_result(payload)
            else:
                fut.set_exception(payload)


async def resolve_host(hostname: str):
    """Return ``getaddrinfo`` results for *hostname*, cached for ``DNS_CACHE_TTL``."""
    now = time.monotonic()
    hit = _cache.get(hostname)
    if hit is not None and now - hit[0] < DNS_CACHE_TTL:
        return hit[1]

    async with _lock:
        hit = _cache.get(hostname)
        if hit is not None and time.monotonic() - hit[0] < DNS_CACHE_TTL:
            return hit[1]
        entry = _inflight.get(hostname)
        if entry is None:
            fut = asyncio.get_running_loop().create_future()
            _inflight[hostname] = [fut, 0]
            owner = True
        else:
            entry[1] += 1
            fut = entry[0]
            owner = False

    if not owner:
        return await fut

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except Exception as e:
        await _settle(hostname, False, e)
        raise

    async with _lock:
        _cache[hostname] = (time.monotonic(), infos)
    await _settle(hostname, True, infos)
    return infos


def clear_cache():
    """Drop all cached entries (tests)."""
    _cache.clear()
