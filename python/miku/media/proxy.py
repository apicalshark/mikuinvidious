# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import contextlib
import inspect
import math
import re
import time
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import ParseResult, urljoin, urlparse, urlunparse

import httpcore
import httpx
from quart import Blueprint, Response, has_request_context, request

from miku import state as shared
from miku.api.live import ScreenResolution
from miku.core.dns_cache import is_safe_public_url
from miku.core.ip_slots import PerIpSlotLimiter
from miku.core.rate_limit import RATE_LIMITS, get_client_ip, rate_limit
from miku.core.singleflight import BoundedNegativeCache, SingleflightGroup
from miku.core.url_signing import verify_signed_pic_subpath
from miku.media.image_cache import image_cache
from miku.media.live_manager import live_manager
from miku.media.stream import CdnConnection, CdnProtocolError, CdnTimeoutError
from miku.services.credential import apply_cdn_cred_headers, get_web_api_cookies
from miku.state import (
    UPSTREAM_RESPONSE_BLACKLIST,
    Network,
    TicketManager,
    appconf,
    appredis,
    get_common_headers,
    image_limiter,
)

proxy_bp = Blueprint("proxy", __name__)

MAX_IMAGE_SIZE = 15 * 1024 * 1024  # 15 MB maximum proxy image size
PROXY_READ_TIMEOUT = 30.0
_discard_close_tasks: set[asyncio.Task] = set()


async def _quiet_close_conn(conn) -> None:
    try:
        await conn.close()
    except Exception:
        pass


def _schedule_discarded_conn_close(loop: asyncio.AbstractEventLoop, conn) -> None:
    if conn is None:
        return
    if loop.is_closed():
        writer = getattr(conn, "writer", None)
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        task = loop.create_task(_quiet_close_conn(conn))
        _discard_close_tasks.add(task)
        task.add_done_callback(_discard_close_tasks.discard)
    else:
        try:
            loop.call_soon_threadsafe(_schedule_discarded_conn_close, loop, conn)
        except RuntimeError:
            pass


async def _cdn_handshake(target_url: str, headers: dict, proxy_url: str | None, label: str):
    """Connect + send request + read headers; (conn, headers) or (None, None)."""
    c = CdnConnection(target_url, headers=headers, proxy_url=proxy_url, read_timeout=PROXY_READ_TIMEOUT)
    try:
        await c.connect()
        await c.send_request()
        rh = await c.read_response_headers()
        return c, rh
    except Exception as e:
        print(f"[Proxy] {label} handshake failed: {e}")
        await c.close()
        return None, None


async def _establish_cdn_connection(url: str, headers: dict, proxy_url: str | None):
    """Handshake with retries: 3 attempts with backoff. (conn, headers) or (None, None).

    A single WARP/SOCKS blip must not 502 the request.
    """
    for attempt in range(3):
        conn, resp_headers = await _cdn_handshake(url, headers, proxy_url, f"primary attempt {attempt + 1}/3")
        if conn is not None:
            return conn, resp_headers
        await asyncio.sleep(0.5 * (attempt + 1))
    return None, None


async def _try_backup_url(vid, vidx, vqn, headers: dict, proxy_url: str | None):
    """Try the cached backup CDN URL; (conn, headers) or (None, None)."""
    bak_url = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}_bak")
    if not bak_url:
        return None, None
    if isinstance(bak_url, bytes):
        bak_url = bak_url.decode()
    if not await is_safe_proxy_url(bak_url):
        return None, None
    return await _establish_cdn_connection(bak_url, headers, proxy_url)


_ALLOWED_PROXY_DOMAINS = (
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
)

# Verified fast HTTP/2 BFS image CDN mirrors (~290-540ms p50 on 30-image bursts):
#   i0.hdslb.com   - Baidu (bdydns.com) / Wangsu (cdn20.com), HTTP/2
#   i1.hdslb.com   - Wangsu (cdn20.com), HTTP/2
#   i2.hdslb.com   - Wangsu (cdn20.com) / Baidu (bdydns.com), HTTP/2
#   s2.hdslb.com   - Alibaba (kunlunar.com), HTTP/2
#   i2.biliimg.com - Tencent Overseas (ovscdns.com) / Baidu (bdydns.com), HTTP/2
#
# Other working BFS mirrors (excluded from shard pool due to higher latency):
#   s1.hdslb.com        - Alibaba (kunlunar.com), HTTP/2 (~1.2-1.4s p50)
#   s3.hdslb.com        - Alibaba (kunlunar.com), HTTP/2 (IPv6-only)
#   i0.biliimg.com      - Tencent Overseas / Baidu, HTTP/2 (~2.2s p50, 4-5.5s max)
#   i1.biliimg.com      - Zenlayer, HTTP/1.1 only (~1.6-1.7s p50, 3.2-4.9s max)
#   i3.biliimg.com      - Zenlayer, HTTP/1.1 only
#   archive.biliimg.com - Baidu (bdydns.com), HTTP/2 (spikes to 5.5s on cold URLs)
#
# Broken / non-existent hosts (do not use):
#   i3.hdslb.com (kunlunpi.com, ReadTimeout), i4.hdslb.com (kunlunhuf.com, 403 HTML),
#   bstatic.hdslb.com (TLS handshake failure), i5-i9/s0/s4.hdslb.com (NXDOMAIN).
_BFS_IMAGE_CDNS = (
    "i0.hdslb.com",
    "i2.biliimg.com",
    "i1.hdslb.com",
    "s2.hdslb.com",
    "i2.hdslb.com",
)
_BARE_IMAGE_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "avif": "image/avif",
}
_ALLOWED_IMAGE_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/avif",
    }
)


_NEGATIVE_CACHE_TTL_SECONDS = 60.0
_NEGATIVE_CACHE_MAX_ENTRIES = 4096
_DEFINITIVE_MISSING_STATUSES = frozenset({404, 410})
_DEFINITIVE_CLIENT_STATUSES = frozenset({400, 413, 414, 451})
_WAVE_COALESCE_SECONDS = 0.04
_MAX_FALLBACK_WAVES = 2
_FALLBACK_MIRROR_ORDER = (
    "i2.biliimg.com",
    "i1.hdslb.com",
    "s2.hdslb.com",
    "i2.hdslb.com",
    "i0.hdslb.com",
)


@dataclass(frozen=True, slots=True)
class _ImageResult:
    status: int
    body: bytes
    content_type: str
    cache_control: str | None = None
    x_cache: str | None = None
    neg_expires_at: float | None = None

    def to_response(self, x_cache_override: str | None = None) -> Response:
        resp = Response(self.body, status=self.status, content_type=self.content_type)
        if self.neg_expires_at is not None:
            remaining = math.ceil(self.neg_expires_at - time.monotonic())
            ttl_cap = int(_NEGATIVE_CACHE_TTL_SECONDS)
            if remaining > 0:
                resp.headers["Cache-Control"] = f"public, max-age={min(ttl_cap, remaining)}"
            else:
                resp.headers["Cache-Control"] = "no-store"
        elif self.cache_control:
            resp.headers["Cache-Control"] = self.cache_control
        x_cache = x_cache_override if x_cache_override is not None else self.x_cache
        if x_cache:
            resp.headers["X-Cache"] = x_cache
        return resp


@dataclass(slots=True)
class _RetryItem:
    original_url: str
    parsed: ParseResult
    headers: dict
    client: object
    tried_hosts: set[str]
    future: asyncio.Future[_ImageResult]


@dataclass(slots=True)
class _WaveBucket:
    items: list[_RetryItem] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None
    active_batches: int = 0


def _select_client_transport(client, target_url: str):
    url_obj = httpx.URL(target_url)
    selector = getattr(client, "_transport_for_url", None)
    if callable(selector) and not inspect.iscoroutinefunction(selector):
        try:
            selected = selector(url_obj)
            if inspect.iscoroutine(selected):
                selected.close()
                return getattr(client, "_transport", None)
            if selected is not None:
                return selected
        except Exception:
            pass
    mounts = getattr(client, "_mounts", None)
    if isinstance(mounts, dict):
        for pattern, transport in mounts.items():
            if transport is None:
                continue
            matches = getattr(pattern, "matches", None)
            if pattern is None or not callable(matches) or matches(url_obj):
                return transport
    return getattr(client, "_transport", None)


def _has_ready_h2_connection(client, target_url: str) -> bool:
    """Return True if the pool already has a warm connection (or no pool exists to guard)."""
    pool = getattr(_select_client_transport(client, target_url), "_pool", None)
    if pool is None or not hasattr(pool, "_connections"):
        return True

    parsed = urlparse(target_url)
    host = (parsed.hostname or "").lower()
    if not host:
        return True
    scheme = parsed.scheme.lower() or "https"
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return True
    origin = httpcore.Origin(scheme.encode("ascii"), host.encode("ascii"), port)

    for conn in list(getattr(pool, "_connections", ())):
        if not conn.can_handle_request(origin) or conn.is_closed():
            continue
        if hasattr(conn, "has_expired") and conn.has_expired():
            continue
        if hasattr(conn, "_connected") and not getattr(conn, "_connected", False):
            continue
        inner = getattr(conn, "_connection", None)
        if inner is None:
            continue
        # If the origin negotiated HTTP/1.1, don't serialize requests behind one socket.
        if not hasattr(inner, "_h2_state"):
            return True
        if conn.is_available():
            return True
    return False


def _select_wave_assignments(items: list[_RetryItem]) -> dict[str, list[_RetryItem]]:
    """Assign a unified mirror host for the wave batch, grouping outliers onto a shared fallback."""
    remaining = list(items)
    groups: dict[str, list[_RetryItem]] = {}
    while remaining:
        best_host = None
        best_score = -1
        for candidate in _FALLBACK_MIRROR_ORDER:
            score = sum(1 for item in remaining if candidate not in item.tried_hosts)
            if score > best_score:
                best_score = score
                best_host = candidate
        if best_host is None or best_score <= 0:
            for item in remaining:
                if not item.future.done():
                    item.future.set_result(_ImageResult(502, b"Upstream error", "text/plain; charset=utf-8"))
            break
        matched = [item for item in remaining if best_host not in item.tried_hosts]
        remaining = [item for item in remaining if best_host in item.tried_hosts]
        groups.setdefault(best_host, []).extend(matched)
    return groups


class _WaveRetryCoordinator:
    """Groups retryable image failures into synchronized mirror waves over shared H2 connections."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        origin_locks: weakref.WeakKeyDictionary[object, dict[str, asyncio.Lock]],
        window_seconds: float = _WAVE_COALESCE_SECONDS,
        max_waves: int = _MAX_FALLBACK_WAVES,
    ):
        self.loop = loop
        self._origin_locks = origin_locks
        self.window_seconds = window_seconds
        self.max_waves = max_waves
        self.active_primary = 0
        self._buckets: dict[int, _WaveBucket] = {}
        self._wave_tasks: set[asyncio.Task] = set()
        self._closed = False

    def _bucket(self, wave_idx: int) -> _WaveBucket:
        bucket = self._buckets.get(wave_idx)
        if bucket is None:
            bucket = _WaveBucket()
            self._buckets[wave_idx] = bucket
        return bucket

    def _prev_round_active(self, wave_idx: int) -> int:
        if wave_idx <= 1:
            return self.active_primary
        prev = self._buckets.get(wave_idx - 1)
        if prev is None:
            return 0
        return len(prev.items) + prev.active_batches

    def enter_primary(self) -> None:
        self.active_primary += 1

    def leave_primary(self) -> None:
        if self.active_primary > 0:
            self.active_primary -= 1
        if self.active_primary == 0:
            bucket = self._buckets.get(1)
            if bucket is not None and bucket.items:
                self._schedule_immediate_flush(1)

    def submit(self, wave_idx: int, item: _RetryItem) -> None:
        if self._closed:
            if not item.future.done():
                item.future.set_result(_ImageResult(502, b"Upstream error", "text/plain; charset=utf-8"))
            return
        if wave_idx > self.max_waves:
            if not item.future.done():
                item.future.set_result(_ImageResult(502, b"Upstream error", "text/plain; charset=utf-8"))
            return

        bucket = self._bucket(wave_idx)
        bucket.items.append(item)

        if self._prev_round_active(wave_idx) == 0:
            self._schedule_immediate_flush(wave_idx)
        elif bucket.timer is None:
            bucket.timer = self.loop.call_later(self.window_seconds, self._flush_wave, wave_idx)

    def _schedule_immediate_flush(self, wave_idx: int) -> None:
        bucket = self._buckets.get(wave_idx)
        if bucket is None or not bucket.items:
            return
        if bucket.timer is not None:
            bucket.timer.cancel()
            bucket.timer = None
        # Defer to next tick so synchronous gather siblings can finish entering primary/submitting.
        bucket.timer = self.loop.call_soon(self._flush_wave, wave_idx)

    def _flush_wave(self, wave_idx: int) -> None:
        bucket = self._buckets.get(wave_idx)
        if bucket is None:
            return
        bucket.timer = None
        if not bucket.items:
            return
        batch = [item for item in bucket.items if not item.future.done()]
        bucket.items.clear()
        if not batch:
            return
        task = self.loop.create_task(self._dispatch_wave(wave_idx, batch))
        self._wave_tasks.add(task)
        task.add_done_callback(self._wave_tasks.discard)

    async def _attempt_item(self, wave_idx: int, item: _RetryItem, target_host: str) -> None:
        if item.future.done():
            return
        candidate_url = urlunparse(item.parsed._replace(netloc=target_host))
        item.tried_hosts.add(target_host)
        has_more_mirrors = wave_idx < self.max_waves and any(
            cdn not in item.tried_hosts for cdn in _FALLBACK_MIRROR_ORDER
        )
        result, should_retry = await _fetch_with_origin_guard(
            item.client,
            candidate_url,
            item.original_url,
            item.headers,
            can_retry=has_more_mirrors,
            origin_locks=self._origin_locks,
        )
        if not should_retry and result is not None:
            if not item.future.done():
                item.future.set_result(result)
            return
        if has_more_mirrors:
            self.submit(wave_idx + 1, item)
        elif not item.future.done():
            item.future.set_result(_ImageResult(502, b"Upstream error", "text/plain; charset=utf-8"))

    async def _dispatch_wave(self, wave_idx: int, batch: list[_RetryItem]) -> None:
        bucket = self._bucket(wave_idx)
        bucket.active_batches += 1
        try:
            groups = _select_wave_assignments(batch)
            attempts = [
                self._attempt_item(wave_idx, item, target_host)
                for target_host, group_items in groups.items()
                for item in group_items
            ]
            if attempts:
                await asyncio.gather(*attempts, return_exceptions=False)
        except asyncio.CancelledError:
            for item in batch:
                if not item.future.done():
                    item.future.cancel()
            raise
        except Exception as exc:
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(exc)
        finally:
            bucket.active_batches -= 1
            if bucket.active_batches == 0 and not bucket.items:
                next_bucket = self._buckets.get(wave_idx + 1)
                if next_bucket is not None and next_bucket.items:
                    self._schedule_immediate_flush(wave_idx + 1)

    async def close(self) -> None:
        self._closed = True
        for bucket in self._buckets.values():
            if bucket.timer is not None:
                bucket.timer.cancel()
                bucket.timer = None
            for item in bucket.items:
                if not item.future.done():
                    item.future.cancel()
            bucket.items.clear()
        tasks = list(self._wave_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._wave_tasks.clear()


@dataclass(slots=True)
class _LoopImageState:
    origin_locks: weakref.WeakKeyDictionary[object, dict[str, asyncio.Lock]]
    coordinator: _WaveRetryCoordinator


_negative_cache: BoundedNegativeCache[_ImageResult] = BoundedNegativeCache(
    max_entries=_NEGATIVE_CACHE_MAX_ENTRIES,
    ttl_seconds=_NEGATIVE_CACHE_TTL_SECONDS,
)
_pic_singleflight: SingleflightGroup[_ImageResult] = SingleflightGroup()
_loop_states: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopImageState] = weakref.WeakKeyDictionary()


def _get_loop_state() -> _LoopImageState:
    for lp in [k for k in list(_loop_states) if k.is_closed()]:
        _loop_states.pop(lp, None)
    loop = asyncio.get_running_loop()
    state = _loop_states.get(loop)
    if state is None:
        origin_locks: weakref.WeakKeyDictionary[object, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()
        state = _LoopImageState(
            origin_locks=origin_locks,
            coordinator=_WaveRetryCoordinator(loop, origin_locks),
        )
        _loop_states[loop] = state
    return state


async def close_pic_proxy() -> None:
    """Cancel and drain any pending wave retry tasks on application shutdown."""
    running_loop = asyncio.get_running_loop()
    state = _loop_states.pop(running_loop, None)
    if state is not None:
        await state.coordinator.close()
    await _pic_singleflight.drain_current_loop()


def _get_image_mirror_candidates(url: str) -> list[str]:
    """Return [original, fallback_1, fallback_2] for /bfs/ image URLs, else [url]."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in _BFS_IMAGE_CDNS or not parsed.path.startswith("/bfs/"):
        return [url]
    fallbacks = [urlunparse(parsed._replace(netloc=cdn)) for cdn in _BFS_IMAGE_CDNS if cdn != host][:2]
    return [url, *fallbacks]


async def is_safe_proxy_url(url: str) -> bool:
    """Validate that a URL is safe to proxy (not pointing to internal/private IPs)."""
    return await is_safe_public_url(url, _ALLOWED_PROXY_DOMAINS, use_dns_cache=False)


async def _fetch_with_origin_guard(
    client,
    target_url: str,
    original_url: str,
    headers: dict,
    *,
    can_retry: bool,
    origin_locks: weakref.WeakKeyDictionary[object, dict[str, asyncio.Lock]],
) -> tuple[_ImageResult | None, bool]:
    """Fetch an image while serializing only cold-origin H2 handshake setup."""
    parsed = urlparse(target_url)
    try:
        _ = parsed.port
    except ValueError:
        return _ImageResult(502, b"Upstream error", "text/plain; charset=utf-8"), False

    origin_key = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    handshake_lock: asyncio.Lock | None = None
    if not _has_ready_h2_connection(client, target_url):
        client_locks = origin_locks.get(client)
        if client_locks is None:
            client_locks = {}
            with contextlib.suppress(TypeError):
                origin_locks[client] = client_locks
        handshake_lock = client_locks.get(origin_key)
        if handshake_lock is None:
            handshake_lock = asyncio.Lock()
            client_locks[origin_key] = handshake_lock

    locked = False

    def release_handshake() -> None:
        nonlocal locked
        if locked and handshake_lock is not None:
            locked = False
            handshake_lock.release()

    if handshake_lock is not None:
        await handshake_lock.acquire()
        if _has_ready_h2_connection(client, target_url):
            handshake_lock.release()
        else:
            locked = True

    try:
        async with image_limiter:
            return await _fetch_image_once(
                client,
                target_url,
                original_url,
                headers,
                can_retry=can_retry,
                release_handshake=release_handshake if locked else None,
            )
    finally:
        release_handshake()


async def _fetch_image_once(
    client,
    target_url: str,
    original_url: str,
    headers: dict,
    *,
    can_retry: bool,
    release_handshake: Callable[[], None] | None = None,
) -> tuple[_ImageResult | None, bool]:
    """Fetch an image from one mirror. Returns (result_or_none, should_retry)."""
    resp = None
    current_url = target_url
    redirect_count = 0
    MAX_REDIRECTS = 3
    try:
        while True:
            req = client.build_request("GET", current_url, headers=headers)
            # Headers are validated before reading body; release handshake_lock
            # immediately once the initial connection/TLS/H2 handshake finishes.
            try:
                resp = await client.send(req, stream=True, follow_redirects=False)
            finally:
                if release_handshake is not None:
                    release_handshake()
                    release_handshake = None

            if resp.is_redirect and "location" in resp.headers:
                if redirect_count >= MAX_REDIRECTS:
                    await resp.aclose()
                    return _ImageResult(403, b"Forbidden: Too many redirects", "text/plain; charset=utf-8"), False
                redirect_count += 1
                loc = resp.headers["location"]
                await resp.aclose()
                next_url = urljoin(current_url, loc)
                if not await is_safe_proxy_url(next_url):
                    print(f"[Proxy] Forbidden redirect destination: {next_url}")
                    return _ImageResult(403, b"Forbidden: Invalid redirect target", "text/plain; charset=utf-8"), False
                current_url = next_url
                continue
            break

        if resp.status_code in _DEFINITIVE_MISSING_STATUSES:
            status_code = resp.status_code
            await resp.aclose()
            neg_result = _ImageResult(
                status=status_code,
                body=b"Not Found" if status_code == 404 else b"Gone",
                content_type="text/plain; charset=utf-8",
                x_cache="MISS",
                neg_expires_at=time.monotonic() + _NEGATIVE_CACHE_TTL_SECONDS,
            )
            return _negative_cache.set(original_url, neg_result), False

        if resp.status_code in _DEFINITIVE_CLIENT_STATUSES:
            status_code = resp.status_code
            await resp.aclose()
            msg = b"Payload Too Large" if status_code == 413 else b"Bad Request"
            return _ImageResult(status_code, msg, "text/plain; charset=utf-8"), False

        if resp.status_code != 200:
            status_code = resp.status_code
            await resp.aclose()
            if can_retry:
                print(f"[Proxy] Image upstream returned {status_code} from {current_url}, retrying alternate mirror")
                return None, True
            final_status = 502 if status_code >= 500 else status_code
            return _ImageResult(final_status, b"Upstream error", "text/plain; charset=utf-8"), False

        content_type = resp.headers.get("content-type", "").lower().split(";")[0].strip()
        # Bilibili's image CDN sometimes returns a bare extension
        # ("jpg") instead of a MIME type. Normalize those so covers
        # load; anything else non-image is still rejected below.
        content_type = _BARE_IMAGE_TYPES.get(content_type, content_type)
        if content_type not in _ALLOWED_IMAGE_TYPES:
            await resp.aclose()
            if can_retry:
                print(f"[Proxy] Invalid Content-Type ({content_type}) from {current_url}, retrying alternate mirror")
                return None, True
            print(f"[Proxy] Invalid Content-Type for image: {content_type}")
            return _ImageResult(403, b"Forbidden: Invalid content type", "text/plain; charset=utf-8"), False

        # Reject oversized payloads early if declared
        cl_header = resp.headers.get("content-length")
        if cl_header and cl_header.isdigit() and int(cl_header) > MAX_IMAGE_SIZE:
            await resp.aclose()
            return _ImageResult(413, b"Payload Too Large", "text/plain; charset=utf-8"), False

        # Read the image content into memory and immediately close the upstream
        # connection, returning it to the HTTPX connection pool while still inside
        # the image_limiter semaphore. Streaming the response to slow client
        # browsers held pool connections open indefinitely, causing PoolTimeout (502)
        # when concurrent client requests exceeded max_connections.
        chunks = []
        total_size = 0
        try:
            async for chunk in resp.aiter_bytes(65536):
                total_size += len(chunk)
                if total_size > MAX_IMAGE_SIZE:
                    return _ImageResult(413, b"Payload Too Large", "text/plain; charset=utf-8"), False
                chunks.append(chunk)
        finally:
            await resp.aclose()

        content = b"".join(chunks)

        # Store to disk cache under the original request URL key
        await image_cache.set(original_url, content, content_type)

        return (
            _ImageResult(
                status=200,
                body=content,
                content_type=content_type,
                cache_control="public, max-age=86400",
                x_cache="MISS",
            ),
            False,
        )
    except Exception as e:
        if release_handshake is not None:
            release_handshake()
            release_handshake = None
        if resp is not None:
            with contextlib.suppress(Exception):
                await resp.aclose()
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        if can_retry:
            print(f"[Proxy] Image fetch error from {current_url} ({err}), retrying alternate mirror")
            return None, True
        print(f"[Proxy] Error in render_proxy_pic for {original_url}: {err}")
        return _ImageResult(502, b"Upstream error", "text/plain; charset=utf-8"), False


async def _run_singleflight_fetch(url: str, state: _LoopImageState) -> _ImageResult:
    cached = await image_cache.get(url)
    if cached is not None:
        content, content_type = cached
        return _ImageResult(
            status=200,
            body=content,
            content_type=content_type,
            cache_control="public, max-age=86400",
            x_cache="HIT",
        )

    neg = _negative_cache.get(url)
    if neg is not None:
        return _ImageResult(
            status=neg.status,
            body=neg.body,
            content_type=neg.content_type,
            cache_control=neg.cache_control,
            x_cache="NEGATIVE-HIT",
            neg_expires_at=neg.neg_expires_at,
        )

    headers = get_common_headers(appconf["bili"]).copy()
    client = await Network.get_image_client()
    parsed = urlparse(url)
    primary_host = parsed.netloc.lower()
    bfs_eligible = primary_host in _BFS_IMAGE_CDNS and parsed.path.startswith("/bfs/")

    coordinator = state.coordinator
    coordinator.enter_primary()
    try:
        result, should_retry = await _fetch_with_origin_guard(
            client,
            url,
            url,
            headers,
            can_retry=bfs_eligible,
            origin_locks=state.origin_locks,
        )
    finally:
        coordinator.leave_primary()

    if not should_retry and result is not None:
        return result

    if not bfs_eligible:
        return _ImageResult(502, b"Upstream error", "text/plain; charset=utf-8")

    retry_future = coordinator.loop.create_future()
    item = _RetryItem(
        original_url=url,
        parsed=parsed,
        headers=headers,
        client=client,
        tried_hosts={primary_host},
        future=retry_future,
    )
    coordinator.submit(1, item)
    try:
        return await retry_future
    except BaseException:
        if not retry_future.done():
            retry_future.cancel()
        raise


def _verify_pic_request_url(req_path: str) -> str | None:
    if not req_path.startswith("/proxy/pic/"):
        return None
    subpath = req_path[11:]

    if has_request_context():
        qs = request.query_string.decode("latin-1") if request.query_string else ""
    elif "?" in subpath:
        subpath, qs = subpath.split("?", 1)
    else:
        qs = ""

    verified_target = verify_signed_pic_subpath(subpath, qs)
    if verified_target is None:
        return None

    if ".." in verified_target or verified_target.startswith("/"):
        return None

    url = f"https://{verified_target}"
    if qs:
        url = f"{url}?{qs}"
    if any(ch in url for ch in ("\r", "\n", "\x00")):
        return None
    return url


async def _serve_verified_pic(url: str):
    if not await is_safe_proxy_url(url):
        return Response("Forbidden", status=403)

    # 1. Check local disk cache (fast path, bypasses semaphore and network)
    cached = await image_cache.get(url)
    if cached is not None:
        content, content_type = cached
        proxy_resp = Response(content, status=200, content_type=content_type)
        proxy_resp.headers["Cache-Control"] = "public, max-age=86400"
        proxy_resp.headers["X-Cache"] = "HIT"
        return proxy_resp

    neg = _negative_cache.get(url)
    if neg is not None:
        return neg.to_response(x_cache_override="NEGATIVE-HIT")

    state = _get_loop_state()
    result = await _pic_singleflight.do(url, lambda: _run_singleflight_fetch(url, state))
    return result.to_response()


async def render_proxy_pic(req_path):
    url = _verify_pic_request_url(req_path)
    if url is None:
        return Response("Forbidden", status=403)
    return await _serve_verified_pic(url)


async def _image_rate_key(req) -> str:
    """One image bucket per client IP.

    The default key is ``ip:path``, which gives every distinct cover URL its
    own allowance — a gallery flood across unique URLs never hits a limit and
    each image costs a socket. Keying on the route caps images per IP.
    """
    return f"{get_client_ip(req)}:image"


@rate_limit(**RATE_LIMITS["loose"], key_func=_image_rate_key)
async def _serve_verified_pic_limited(url: str):
    return await _serve_verified_pic(url)


@proxy_bp.route("/proxy/pic/<path:subpath>")
async def proxy_pic(subpath):
    raw_path = request.scope.get("raw_path")
    req_path = raw_path.decode("latin-1") if isinstance(raw_path, (bytes, bytearray)) else ""
    if not req_path.startswith("/proxy/pic/"):
        req_path = f"/proxy/pic/{subpath}"
    url = _verify_pic_request_url(req_path)
    if url is None:
        return Response("Forbidden", status=403)
    return await _serve_verified_pic_limited(url)


async def _proxy_ip_key(req) -> str:
    """One coarse bucket per client IP across every ``/proxy/<path>`` URL.

    The default key is ``ip:path``; every vid/quality/room is its own path,
    so a crawler walking renditions got a fresh allowance per URL and the
    limit never bound. The per-path limit stays (it throttles hammering one
    URL); this one caps the IP as a whole.
    """
    return f"{get_client_ip(req)}:proxy"


# Concurrently open streams per client IP — strictly per-IP, no global
# ceiling (see ip_slots.py). A browser tab plays one progressive stream
# (two briefly while a seek replaces the old request); HLS playlist polls
# are short-lived. Live FLV holds one long connection per watched room.
PROXY_MAX_DIRECT_STREAMS_PER_IP = 6
PROXY_MAX_LIVE_STREAMS_PER_IP = 4
direct_stream_slots = PerIpSlotLimiter(PROXY_MAX_DIRECT_STREAMS_PER_IP)
live_stream_slots = PerIpSlotLimiter(PROXY_MAX_LIVE_STREAMS_PER_IP)

# /proxy/video/<vid>_<idx>_<qn>[.ext] — vid is a BV/av id, or an audio
# au/am id (bare digits too: views_audio keys raw route params).
_PROGRESSIVE_PATH_RE = re.compile(
    r"^(BV[A-Za-z0-9]{10}|(?:av|au|am)?\d{1,20})_(\d{1,4})_(\d{1,6})(?:\.[A-Za-z0-9]{1,5})?$"
)
# /proxy/live/<room_id>[_<qn>] — digits only. room_id used to reach an
# appredis.keys() glob unvalidated ("*" scanned the whole keyspace).
_LIVE_PATH_RE = re.compile(r"^(\d{1,20})(?:_(\d{1,6}))?$")
# Exactly one byte-range: ``bytes=N-``, ``bytes=N-M`` or suffix ``bytes=-N``.
# Multi-range lists are the CVE-2011-3192 ("Apache Killer") amplification
# shape and no player sends them. Open-ended ranges stay legal: native
# <video> opens progressive files with ``bytes=0-``.
_SINGLE_RANGE_RE = re.compile(r"^bytes=(?:(\d{1,19})-(\d{0,19})|-(\d{1,19}))$")


def _is_acceptable_progressive_range(raw: str | None) -> bool:
    raw = (raw or "").strip()
    if not raw:
        return True
    m = _SINGLE_RANGE_RE.match(raw)
    if not m:
        return False
    start, end = m.group(1), m.group(2)
    return not (start is not None and end and int(end) < int(start))


def _too_many_streams() -> Response:
    return Response(
        "Too Many Requests: too many concurrent streams from this address",
        status=429,
        headers={"Retry-After": "1", "Access-Control-Allow-Origin": "*"},
    )


async def _first_live_quality_url(room_id: str):
    """Fallback lookup for ``/proxy/live/<room>`` without a quality.

    Bounded ``SCAN`` instead of ``KEYS``: KEYS is O(keyspace) and blocks
    Redis for every caller while it runs.
    """
    cursor = 0
    truncated = False
    for _ in range(8):
        cursor, keys = await appredis.scan(cursor=cursor, match=f"miku_live_{room_id}_*", count=100)
        # Redis treats COUNT as a hint, so cap follow-up GETs explicitly.
        truncated |= len(keys) > 100
        for key in keys[:100]:
            url = await appredis.get(key)
            if url:
                return url
        if not cursor:
            break
    # SCAN may not reach a matching legacy key in a large database. Known
    # qualities can also be checked directly without traversing the keyspace.
    if cursor or truncated:
        for quality in ScreenResolution:
            url = await appredis.get(f"miku_live_{room_id}_{quality.value}")
            if url:
                return url
    return None


def _is_live_hls_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if ".m3u8" in path:
        return True
    if ".flv" in path:
        return False
    return "master" in path or "hls" in path


@proxy_bp.route("/proxy/<path:subpath>")
@rate_limit(limit=300, window=60, key_func=_proxy_ip_key)
@rate_limit(**RATE_LIMITS["proxy"])
async def proxy_main(subpath):
    req_path = f"/proxy/{subpath}"

    if req_path.startswith("/proxy/video/") or req_path.startswith("/proxy/live/"):
        is_live = "/proxy/live/" in req_path
        if is_live:
            m = _LIVE_PATH_RE.match(req_path.removeprefix("/proxy/live/"))
            if not m:
                return Response("Bad Request", status=400)
            room_id = m.group(1)
            vqn = m.group(2) or "default"
        else:
            m = _PROGRESSIVE_PATH_RE.match(req_path.removeprefix("/proxy/video/"))
            if not m:
                return Response("Bad Request", status=400)
            vid, vidx, vqn = m.group(1), m.group(2), m.group(3)
            if not _is_acceptable_progressive_range(request.headers.get("Range")):
                return Response(
                    "Range Not Satisfiable: a single byte range is required",
                    status=416,
                    headers={"Accept-Ranges": "bytes", "Access-Control-Allow-Origin": "*"},
                )

        if is_live:
            redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
            url = await appredis.get(redis_key)
            if not url and vqn == "default":
                url = await _first_live_quality_url(room_id)
        else:
            url = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}")

        if not url:
            return Response("Not Found", status=404)

        if isinstance(url, bytes):
            url = url.decode()

        if not await is_safe_proxy_url(url):
            return Response("Forbidden: Invalid proxy target", status=403)

        if is_live:
            headers = get_common_headers(appconf["bili"]).copy()
        else:
            # VOD progressive must use the web-Chrome CDN set, NOT the
            # Android app UA in get_common_headers(): Bilibili's CDN edge
            # (upos mirrors, akamaized, .bilivideo.com) 403s the BiliDroid
            # UA — same verified gotcha the DASH path already handles via
            # _build_dash_cdn_headers(). Sent the wrong UA, every durl-only
            # video 403s on primary+backup, keys get deleted, and all later
            # hits 404 (e.g. bangumi ep98604).
            from miku.core.fingerprints import get_cdn_headers

            headers = get_cdn_headers(referer=appconf["bili"].get("referer", "https://www.bilibili.com"))

        # Add Bili-Ticket, dynamic session/trace IDs, and active credentials
        ticket = await TicketManager.get_ticket()
        headers["session_id"] = TicketManager._generate_session_id()
        headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
        apply_cdn_cred_headers(headers, shared.appcred, ticket)

        # Forward headers from client
        for k, v in request.headers.items():
            if k.lower() in ["range", "if-range", "x-playback-session-id"]:
                headers[k.lower()] = v

        is_hls_live = is_live and (_is_live_hls_url(url) or request.args.get("format") == "hls")

        if is_live and not is_hls_live:
            live_slot = live_stream_slots.try_acquire(get_client_ip(request))
            if live_slot is None:
                return _too_many_streams()
            client_id = request.args.get("cid") or str(uuid.uuid4())
            cookie_jar = get_web_api_cookies(shared.appcred)
            try:
                stream, q = await live_manager.subscribe(url, headers, cookie_jar, client_id)
            except BaseException:
                live_slot.release()
                raise
            if not q:
                live_slot.release()
                return Response("Upstream Error", status=502)

            started = False

            async def generate_from_manager():
                nonlocal started
                started = True
                reason = "Stream ended"
                try:
                    while True:
                        try:
                            # Use a reasonable timeout (15s) for keep-alive
                            async with asyncio.timeout(15.0):
                                chunk = await q.get()
                            if chunk is None:
                                break
                            yield chunk
                        except TimeoutError:
                            # Send a minimal valid FLV Script Data tag if we have already sent headers
                            if stream.header_ready.is_set():
                                # Type 18, Size 0, Time 0, PrevSize 11
                                yield b"\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b"
                            else:
                                # Still waiting for upstream? Send nothing to avoid corruption
                                continue
                except asyncio.CancelledError, GeneratorExit:
                    reason = "Client disconnected"
                    raise
                except Exception as e:
                    reason = f"Generator error: {e}"
                finally:
                    stream.remove_client(client_id, reason=reason)
                    live_slot.release()

            live_gen = generate_from_manager()
            # A body that is never iterated never runs its finally: free the
            # slot (and the subscriber) when the generator is collected.
            weakref.finalize(live_gen, live_slot.release)

            def _discard_cleanup():
                if not started:
                    stream.remove_client(client_id, "Response discarded")

            weakref.finalize(live_gen, _discard_cleanup)
            proxy_resp = Response(live_gen, status=stream.status_code or 200)
            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
            proxy_resp.headers["Content-Type"] = "video/x-flv"
            proxy_resp.headers["X-Miku-Proxy"] = "LiveManager"
            proxy_resp.headers["X-Accel-Buffering"] = "no"
            return proxy_resp

        # DIRECT PROXY FOR VOD AND HLS LIVE (via raw socket CdnConnection)
        slot = direct_stream_slots.try_acquire(get_client_ip(request))
        if slot is None:
            return _too_many_streams()
        handed_off = False
        try:
            proxy_url = Network.get_proxy()

            conn, resp_headers = await _establish_cdn_connection(url, headers, proxy_url)
            if conn is None and not is_live:
                # Primary unreachable (transport-level, not URL staleness — so the
                # backup key is kept) — try the backup URL before 502.
                conn, resp_headers = await _try_backup_url(vid, vidx, vqn, headers, proxy_url)
            if conn is None:
                return Response("Upstream error", status=502)

            try:
                print(f"[Proxy] Connected: {url[:50]}... Status: {resp_headers.status_code}")

                if resp_headers.status_code in [403, 412, 514]:
                    # Read response body for debugging
                    try:
                        debug_body = await conn.read_debug_body()
                        print(f"[Proxy] CDN {resp_headers.status_code} body: {debug_body[:500]}")
                    except Exception:
                        pass
                    print(f"[Proxy] CDN returned {resp_headers.status_code}. Refreshing ticket and retrying...")
                    await conn.close()
                    ticket = await TicketManager.get_ticket(force_refresh=True)
                    if ticket:
                        headers["x-bili-ticket"] = ticket
                    else:
                        headers.pop("x-bili-ticket", None)
                    headers["session_id"] = TicketManager._generate_session_id()
                    headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
                    conn, resp_headers = await _cdn_handshake(url, headers, proxy_url, "ticket-refresh retry")
                    if conn is None:
                        return Response("Upstream error", status=502)
                    print(f"[Proxy] Retry status: {resp_headers.status_code}")

                # If still 403 after retry, try backup URL, then lower qualities, then expire cache
                if resp_headers.status_code in [403, 412, 514] and not is_live:
                    await conn.close()

                    async def try_url(target_url: str, label: str) -> tuple:
                        if not await is_safe_proxy_url(target_url):
                            print(f"[Proxy] {label} rejected unsafe fallback URL")
                            return None, None
                        c = CdnConnection(
                            target_url, headers=headers, proxy_url=proxy_url, read_timeout=PROXY_READ_TIMEOUT
                        )
                        try:
                            await c.connect()
                            await c.send_request()
                            rh = await c.read_response_headers()
                            if rh.status_code not in [403, 412, 514]:
                                print(f"[Proxy] {label} succeeded ({rh.status_code})")
                                return c, rh
                            await c.close()
                        except Exception as e:
                            print(f"[Proxy] {label} failed: {e}")
                            await c.close()
                        return None, None

                    # CDN URL is stale — delete it so the backend re-fetches
                    await appredis.delete(f"mikuinv_{vid}_{vidx}_{vqn}")
                    print(f"[Proxy] Deleted stale CDN URL for qn={vqn}")

                    # 1) Try backup URL for current quality
                    bak_key = f"mikuinv_{vid}_{vidx}_{vqn}_bak"
                    bak_url = await appredis.get(bak_key)
                    if bak_url:
                        bak_url = bak_url.decode() if isinstance(bak_url, bytes) else bak_url
                        bak_conn, bak_rh = await try_url(bak_url, f"backup qn={vqn}")
                        if bak_conn:
                            conn, resp_headers = bak_conn, bak_rh
                        else:
                            await appredis.delete(bak_key)

                    # 2) If backup also failed, try lower quality URLs from Redis
                    if resp_headers.status_code in [403, 412, 514]:
                        print("[Proxy] Backup also failed, trying lower qualities...")
                        QUALITY_ORDER = [64, 32, 16]
                        try:
                            current_qn = int(vqn)
                        except ValueError, TypeError:
                            print(f"[Proxy] Bad vqn: {vqn!r}")
                            return Response("Bad quality parameter", status=502)
                        fallback_url = None
                        for fq in QUALITY_ORDER:
                            if fq >= current_qn:
                                continue
                            fu = await appredis.get(f"mikuinv_{vid}_{vidx}_{fq}")
                            if not fu:
                                continue
                            fu = fu.decode() if isinstance(fu, bytes) else fu
                            fq_conn, fq_rh = await try_url(fu, f"qn={fq} primary")
                            if fq_conn:
                                conn, resp_headers = fq_conn, fq_rh
                                fallback_url = fu
                                break
                            # Try backup URL for this quality
                            fq_bak = await appredis.get(f"mikuinv_{vid}_{vidx}_{fq}_bak")
                            if fq_bak:
                                fq_bak = fq_bak.decode() if isinstance(fq_bak, bytes) else fq_bak
                                fq_conn2, fq_rh2 = await try_url(fq_bak, f"qn={fq} backup")
                                if fq_conn2:
                                    conn, resp_headers = fq_conn2, fq_rh2
                                    fallback_url = fq_bak
                                    break
                        if not fallback_url:
                            print(f"[Proxy] All quality fallbacks failed for {vid}_{vidx}")
                            return Response("Upstream returned 403 and no fallback available", status=502)

                if is_hls_live and resp_headers.status_code in (301, 302, 303, 307, 308):
                    await conn.close()
                    location = resp_headers.headers.get("location")
                    if not location:
                        return Response("Upstream redirect missing Location", status=502)
                    try:
                        target_url = urljoin(url, location)
                    except ValueError:
                        return Response("Forbidden: Invalid redirect target", status=403)
                    if not await is_safe_proxy_url(target_url):
                        return Response("Forbidden: Invalid redirect target", status=403)
                    return Response(
                        status=resp_headers.status_code,
                        headers={
                            "Location": target_url,
                            "Access-Control-Allow-Origin": "*",
                            "Cache-Control": "no-store",
                        },
                    )

                gen_started = False

                async def generate():
                    nonlocal gen_started
                    gen_started = True
                    try:
                        async for chunk in conn.iter_chunks():
                            yield chunk
                    except asyncio.CancelledError, GeneratorExit:
                        # Client went away mid-stream: close upstream and let
                        # cancellation propagate (no error log — this is normal).
                        raise
                    except (CdnProtocolError, CdnTimeoutError, TimeoutError, OSError) as e:
                        # Mid-body cut: end the stream early WITHOUT raising only when
                        # downstream Content-Length was declared, so the short body
                        # surfaces as a length mismatch and the client/player can detect
                        # truncation. If Content-Length was omitted (unframed/chunked/close-delimited),
                        # propagate the read failure so the transfer does not appear complete.
                        cl = resp_headers.headers.get("content-length")
                        has_content_length = False
                        if cl is not None and str(cl).strip():
                            try:
                                has_content_length = int(str(cl).strip()) >= 0
                            except ValueError:
                                has_content_length = False
                        if not has_content_length:
                            raise
                        print(f"[Proxy] Mid-body cut for {url[:80]}...: {e}")
                        return
                    finally:
                        await conn.close()
                        slot.release()

                gen = generate()
                loop = asyncio.get_running_loop()

                def _on_gen_discard(c=conn, s=slot, lp=loop):
                    s.release()
                    if not gen_started:
                        _schedule_discarded_conn_close(lp, c)

                # Never-iterated bodies skip the finally: release and close on collection.
                weakref.finalize(gen, _on_gen_discard)
                handed_off = True
                proxy_resp = Response(
                    gen,
                    status=resp_headers.status_code,
                )
                proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
                proxy_resp.headers["X-Accel-Buffering"] = "no"

                if request.args.get("dl") == "1" and not is_live:
                    safe_vid = "".join(c for c in vid if c.isalnum() or c == "_")
                    proxy_resp.headers["Content-Disposition"] = (
                        f'attachment; filename="miku_{safe_vid}_p{vidx}_{vqn}.mp4"'
                    )

                if is_live:
                    if is_hls_live:
                        proxy_resp.headers["Content-Type"] = "application/vnd.apple.mpegurl"
                        proxy_resp.headers["Cache-Control"] = "no-store"
                    else:
                        proxy_resp.headers["Content-Type"] = "video/x-flv"

                for k, v in resp_headers.headers.items():
                    if k.lower() in UPSTREAM_RESPONSE_BLACKLIST:
                        continue
                    if k in [
                        "content-type",
                        "content-length",
                        "content-range",
                        "accept-ranges",
                        "etag",
                        "last-modified",
                        "cache-control",
                    ]:
                        if is_live and k in ["content-type", "connection"]:
                            continue
                        if is_hls_live and k == "cache-control":
                            continue
                        proxy_resp.headers[k] = v

                if resp_headers.status_code in [200, 206] and not is_live:
                    current_ct = proxy_resp.headers.get("Content-Type", "").lower()
                    if not current_ct or "application/octet-stream" in current_ct:
                        if ".mp4" in url.lower():
                            proxy_resp.headers["Content-Type"] = "video/mp4"
                        elif ".flv" in url.lower():
                            proxy_resp.headers["Content-Type"] = "video/x-flv"

                return proxy_resp
            except Exception as e:
                print(f"[Proxy] Error proxying {url}: {e}")
                await conn.close()
                return Response("Upstream error", status=502)
        finally:
            if not handed_off:
                slot.release()

    else:
        return Response("I'm a teapot", status=418)


@proxy_bp.route("/proxy/live/disconnect", methods=["POST"])
@rate_limit(**RATE_LIMITS["strict"])
async def proxy_live_disconnect():
    """Manual disconnect ping for live streams to clean up resources immediately."""
    from miku.core.csrf import validate_csrf_token

    token = request.headers.get("X-CSRF-Token") or request.args.get("csrf_token")
    if not token and request.mimetype == "application/x-www-form-urlencoded":
        # Unload beacons (sendBeacon) can't set headers, so player.js puts
        # the token in a form-urlencoded body instead of the query string
        # (keeps per-session tokens out of access logs).
        token = (await request.form).get("csrf_token")
    if not await validate_csrf_token(token):
        return Response("CSRF token validation failed", status=403)

    room_id = request.args.get("room_id")
    vqn = request.args.get("vqn", "default")
    client_id = request.args.get("cid")

    if not room_id or not client_id:
        return Response("Missing room_id or cid", status=400)

    redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
    url = await appredis.get(redis_key)

    if url:
        if isinstance(url, bytes):
            url = url.decode()
        stream = live_manager.streams.get(url)
        # Only short-circuit when this generation actually owns the cid;
        # otherwise fall through to the scan (a no-op remove would mask a
        # stale slot on a rotated generation as success).
        if stream is not None and client_id in stream.clients:
            stream.remove_client(client_id, reason="Client Ping")
            return Response("OK", status=200)

    # Fallback: the redis key is overwritten with a freshly-signed play URL
    # on every page load, so a delayed beacon can name a cid whose stream
    # generation no longer matches redis. cids are 128-bit random, hence
    # globally unique — scan for the owning stream directly instead of
    # leaving the stale slot to the grace-period reaper.
    for stream in list(live_manager.streams.values()):
        if client_id in stream.clients:
            stream.remove_client(client_id, reason="Client Ping (stale generation)")
            return Response("OK", status=200)

    return Response("Stream not found", status=404)
