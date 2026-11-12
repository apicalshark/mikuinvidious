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

"""Tiny TTL cache for ``getaddrinfo`` used by the DASH SSRF guard.

``dash_proxy._is_safe_dash_url_async`` resolves target hostnames to reject private IPs.
Uncached lookups cost 50ms-1s (measured 86ms for ``upos-sz-mirrorcosov``,
18-30ms for the other mirrors) and run on the DASH critical path.

Entries are served for ``DNS_CACHE_TTL`` seconds (default 120s) and then
stale-while-revalidate for up to ``DNS_STALE_GRACE`` more: the stale answer
goes back immediately and a background lookup refreshes it, so the DNS cost
never lands inline on a fragment. Past the grace the lookup blocks again, so
a hostname that has been re-pointed is not served from memory indefinitely.
Concurrent lookups for the same hostname coalesce (singleflight).
``proxy.is_safe_proxy_url`` bypasses this cache so each approval checks a
fresh resolution. Neither path pins the connection to the checked address.
"""

import asyncio
import socket
import time

DNS_CACHE_TTL = 120.0
# How far past the TTL a stale answer may be served while it revalidates.
# Only allowlisted Bilibili CDN domains reach this cache, so staleness is a
# freshness question, not an SSRF one — but the window stays bounded.
DNS_STALE_GRACE = 300.0

_cache: dict[str, tuple[float, list]] = {}
# hostname -> [future, waiter_count]; the future is settled only when someone
# waits on it, so unobserved failures never log "exception was never retrieved".
_inflight: dict[str, list] = {}
# hostname -> background revalidation task (one per host at a time).
_refreshing: dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()


def _settle(hostname: str, ok: bool, payload):
    """Settle joiners of *hostname*; synchronous so it also runs under cancellation.

    The pop needs no lock: asyncio is single-threaded, so dict mutation is
    atomic — the lock only serializes the check-and-register sequence above.
    """
    entry = _inflight.pop(hostname, None)
    if entry is not None:
        fut, waiters = entry
        if waiters and not fut.done():
            if ok:
                fut.set_result(payload)
            else:
                fut.set_exception(payload)


async def resolve_host(hostname: str):
    """Return ``getaddrinfo`` results for *hostname*.

    Fresh entries return immediately; expired-but-within-grace entries return
    stale and revalidate in the background; anything colder waits for a real
    lookup (coalesced with any other waiter for the same hostname).
    """
    now = time.monotonic()
    hit = _cache.get(hostname)
    if hit is not None:
        age = now - hit[0]
        if age < DNS_CACHE_TTL:
            return hit[1]
        if age < DNS_CACHE_TTL + DNS_STALE_GRACE:
            _schedule_refresh(hostname)
            return hit[1]

    return await _lookup(hostname)


def _schedule_refresh(hostname: str) -> None:
    """Start a background revalidation for *hostname* unless one is running."""
    task = _refreshing.get(hostname)
    if task is not None and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync caller): the next await will block instead
    _refreshing[hostname] = loop.create_task(_refresh(hostname))


async def _refresh(hostname: str) -> None:
    try:
        await _lookup(hostname)
    except Exception:
        # Stale-but-served is the point: a failed revalidation leaves the
        # previous answer in place until the next attempt.
        pass
    finally:
        if _refreshing.get(hostname) is asyncio.current_task():
            _refreshing.pop(hostname, None)


async def _lookup(hostname: str):
    """Resolve *hostname* now, coalescing concurrent callers behind one lookup."""
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
        # Shield: a cancelled joiner must not cancel the shared future for
        # the remaining joiners (Task.cancel propagates to the awaited fut).
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            # No longer waiting: stop counting so a later failure doesn't
            # set an exception nobody observes. Best-effort — the entry may
            # already be settled and popped.
            entry = _inflight.get(hostname)
            if entry is not None and entry[0] is fut and entry[1] > 0:
                entry[1] -= 1
            raise

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except asyncio.CancelledError:
        # A disconnected client cancels the owner: settle joiners with a
        # retryable error (their guards treat it as a failed lookup) instead
        # of leaving them blocked on a dead future forever.
        _settle(hostname, False, RuntimeError("dns lookup cancelled"))
        raise
    except Exception as e:
        _settle(hostname, False, e)
        raise

    async with _lock:
        _cache[hostname] = (time.monotonic(), infos)
    _settle(hostname, True, infos)
    return infos


def clear_cache():
    """Drop all cached entries (tests)."""
    _cache.clear()
    for task in list(_refreshing.values()):
        task.cancel()
    _refreshing.clear()
