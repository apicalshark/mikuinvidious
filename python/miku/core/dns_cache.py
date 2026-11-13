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
import ipaddress
import socket
import time
from urllib.parse import urlparse

DNS_CACHE_TTL = 120.0
# How far past the TTL a stale answer may be served while it revalidates.
# Only allowlisted Bilibili CDN domains reach this cache, so staleness is a
# freshness question, not an SSRF one — but the window stays bounded.
DNS_STALE_GRACE = 300.0
ALLOWED_HTTP_SCHEMES = frozenset({"http", "https"})

_cache: dict[str, tuple[float, list]] = {}
# hostname -> [future, waiter_count]; the future is settled only when someone
# waits on it, so unobserved failures never log "exception was never retrieved".
_inflight: dict[str, list] = {}
# hostname -> background revalidation task (one per host at a time).
_refreshing: dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()


def is_public_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True iff *ip* is a globally routable unicast address."""
    if isinstance(ip, str):
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            return False
    elif isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        ip_obj = ip
    else:
        return False
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped
    return bool(ip_obj.is_global and not ip_obj.is_multicast)


def is_allowed_url_host(
    url: str,
    allowed_domains: tuple[str, ...] | list[str] | frozenset[str],
    *,
    allowed_schemes: frozenset[str] = ALLOWED_HTTP_SCHEMES,
) -> str | None:
    """Validate URL scheme, credentials, control chars, and domain allowlist.

    Returns the normalized lowercase hostname (trailing dot stripped) if allowed,
    or ``None`` if invalid/disallowed.
    """
    if not url or not isinstance(url, str):
        return None
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in allowed_schemes:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return None
    for domain in allowed_domains:
        norm_domain = domain.lstrip(".").rstrip(".").lower()
        if not norm_domain:
            continue
        if host == norm_domain or host.endswith(f".{norm_domain}"):
            return host
    return None


def _settle(hostname: str, fut: asyncio.Future, ok: bool, payload) -> None:
    """Settle joiners of *hostname* iff *fut* is still the live entry.

    Synchronous so it also runs safely under cancellation.
    """
    entry = _inflight.get(hostname)
    if entry is not None and entry[0] is fut:
        del _inflight[hostname]
        if entry[1] and not fut.done():
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
        except BaseException:
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
        _settle(hostname, fut, False, RuntimeError("dns lookup cancelled"))
        raise
    except BaseException as e:
        _settle(hostname, fut, False, e)
        raise
    else:
        entry = _inflight.get(hostname)
        if entry is not None and entry[0] is fut:
            _cache[hostname] = (time.monotonic(), infos)
        _settle(hostname, fut, True, infos)
        return infos


async def is_safe_public_url(
    url: str,
    allowed_domains: tuple[str, ...] | list[str] | frozenset[str],
    *,
    allowed_schemes: frozenset[str] = ALLOWED_HTTP_SCHEMES,
    use_dns_cache: bool = True,
) -> bool:
    """Return True iff *url* matches *allowed_domains* and resolves only to public IPs."""
    host = is_allowed_url_host(url, allowed_domains, allowed_schemes=allowed_schemes)
    if host is None:
        return False
    try:
        if use_dns_cache:
            addr_infos = await resolve_host(host)
        else:
            addr_infos = await asyncio.to_thread(socket.getaddrinfo, host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except Exception:
        return False
    if not addr_infos:
        return False
    for info in addr_infos:
        sockaddr = info[4] if len(info) > 4 else None
        if not sockaddr:
            return False
        if not is_public_ip(sockaddr[0]):
            return False
    return True


def clear_cache() -> None:
    """Drop all cached entries and settle pending in-flight lookups (tests/shutdown)."""
    _cache.clear()
    for task in list(_refreshing.values()):
        task.cancel()
    _refreshing.clear()
    entries = list(_inflight.items())
    _inflight.clear()
    for hostname, (fut, waiters) in entries:
        if waiters and not fut.done():
            fut.set_exception(RuntimeError(f"dns cache cleared for {hostname!r}"))
