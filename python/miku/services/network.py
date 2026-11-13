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
"""HTTP client pooling and proxy connection management."""

from __future__ import annotations

import asyncio
import time
from collections import deque

import h2.connection
import h2.errors
import h2.exceptions
import h2.settings
import httpx

from miku.api.client import request_settings

# Concurrency limiter for outbound image proxy requests
image_limiter = asyncio.Semaphore(50)

_HTTP2_PATCHED = False


class _H2StreamLimiter:
    """Cancellation-safe dynamic stream limiter for httpcore AsyncHTTP2Connection.

    Replaces httpcore's AsyncSemaphore(100) + 99-acquire init loop, which can
    desync on task cancellation during init or deadlock inside _read_lock when
    remote MAX_CONCURRENT_STREAMS decreases, raising
    ``ValueError: semaphore released too many times``.
    """

    def __init__(self, max_streams: int = 1) -> None:
        self.max_streams = max_streams
        self.active_streams = 0
        self._closed = False
        self._waiters: deque[asyncio.Future[None]] = deque()

    def set_max_streams(self, new_max: int) -> None:
        self.max_streams = new_max
        self._wake_waiters()

    def _wake_waiters(self) -> None:
        while not self._closed and self._waiters and self.active_streams < self.max_streams:
            fut = self._waiters.popleft()
            if not fut.done():
                # Reserve the slot before resuming the waiter so concurrent
                # acquire() calls cannot exceed max_streams.
                self.active_streams += 1
                fut.set_result(None)

    async def acquire(self) -> None:
        from httpcore._exceptions import ConnectionNotAvailable

        if self._closed:
            raise ConnectionNotAvailable()

        while self.active_streams >= self.max_streams:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[None] = loop.create_future()
            self._waiters.append(fut)
            try:
                await fut
                return
            except BaseException:
                try:
                    self._waiters.remove(fut)
                except ValueError:
                    pass
                if fut.done() and not fut.cancelled() and fut.exception() is None:
                    self.release()
                raise

        self.active_streams += 1

    def release(self) -> None:
        if self.active_streams > 0:
            self.active_streams -= 1
        self._wake_waiters()

    def close(self) -> None:
        from httpcore._exceptions import ConnectionNotAvailable

        self._closed = True
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_exception(ConnectionNotAvailable())


def _ensure_safe_httpcore_http2() -> None:
    """Patch httpcore's AsyncHTTP2Connection once to avoid semaphore desyncs."""
    global _HTTP2_PATCHED
    if _HTTP2_PATCHED:
        return

    import httpcore
    import httpcore._async
    import httpcore._async.http2 as h2_mod
    from httpcore._exceptions import (
        ConnectionNotAvailable,
        LocalProtocolError,
        RemoteProtocolError,
    )
    from httpcore._synchronization import AsyncShieldCancellation
    from httpcore._trace import Trace

    base_cls = h2_mod.AsyncHTTP2Connection
    if getattr(base_cls, "_miku_safe_http2", False):
        _HTTP2_PATCHED = True
        return

    class SafeAsyncHTTP2Connection(base_cls):
        _miku_safe_http2 = True

        async def handle_async_request(self, request: h2_mod.Request) -> h2_mod.Response:
            if not self.can_handle_request(request.url.origin):
                raise RuntimeError(
                    f"Attempted to send request to {request.url.origin} on connection "
                    f"to {self._origin}"
                )

            async with self._state_lock:
                if self._state in (
                    h2_mod.HTTPConnectionState.ACTIVE,
                    h2_mod.HTTPConnectionState.IDLE,
                ):
                    self._request_count += 1
                    self._expire_at = None
                    self._state = h2_mod.HTTPConnectionState.ACTIVE
                else:
                    raise ConnectionNotAvailable()

            async with self._init_lock:
                if not self._sent_connection_init:
                    try:
                        sci_kwargs = {"request": request}
                        async with Trace(
                            "send_connection_init", h2_mod.logger, request, sci_kwargs
                        ):
                            await self._send_connection_init(**sci_kwargs)
                        self._max_streams = 1
                        self._max_streams_semaphore = _H2StreamLimiter(self._max_streams)
                        self._sent_connection_init = True
                    except BaseException as exc:
                        with AsyncShieldCancellation():
                            await self.aclose()
                        raise exc

            await self._max_streams_semaphore.acquire()
            if (
                self._state == h2_mod.HTTPConnectionState.CLOSED
                or self._connection_error
            ):
                self._max_streams_semaphore.release()
                raise ConnectionNotAvailable()

            try:
                stream_id = self._h2_state.get_next_available_stream_id()
                self._events[stream_id] = []
            except h2.exceptions.NoAvailableStreamIDError as exc:  # pragma: nocover
                self._used_all_stream_ids = True
                self._request_count -= 1
                self._max_streams_semaphore.release()
                raise ConnectionNotAvailable() from exc

            try:
                kwargs = {"request": request, "stream_id": stream_id}
                async with Trace(
                    "send_request_headers", h2_mod.logger, request, kwargs
                ):
                    await self._send_request_headers(
                        request=request, stream_id=stream_id
                    )
                async with Trace(
                    "send_request_body", h2_mod.logger, request, kwargs
                ):
                    await self._send_request_body(request=request, stream_id=stream_id)
                async with Trace(
                    "receive_response_headers", h2_mod.logger, request, kwargs
                ) as trace:
                    status, headers = await self._receive_response(
                        request=request, stream_id=stream_id
                    )
                    trace.return_value = (status, headers)

                return h2_mod.Response(
                    status=status,
                    headers=headers,
                    content=h2_mod.HTTP2ConnectionByteStream(
                        self, request, stream_id=stream_id
                    ),
                    extensions={
                        "http_version": b"HTTP/2",
                        "network_stream": self._network_stream,
                        "stream_id": stream_id,
                    },
                )
            except BaseException as exc:  # noqa: PIE786
                with AsyncShieldCancellation():
                    kwargs = {"stream_id": stream_id}
                    async with Trace(
                        "response_closed", h2_mod.logger, request, kwargs
                    ):
                        await self._response_closed(stream_id=stream_id)

                if isinstance(exc, h2.exceptions.ProtocolError):
                    if self._connection_terminated:  # pragma: nocover
                        raise RemoteProtocolError(self._connection_terminated) from exc
                    raise LocalProtocolError(exc) from exc  # pragma: nocover

                raise exc

        async def _receive_remote_settings_change(
            self, event: h2.events.RemoteSettingsChanged
        ) -> None:
            max_concurrent_streams = event.changed_settings.get(
                h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS
            )
            if max_concurrent_streams:
                new_max_streams = min(
                    max_concurrent_streams.new_value,
                    self._h2_state.local_settings.max_concurrent_streams,
                )
                if new_max_streams and new_max_streams != self._max_streams:
                    self._max_streams = new_max_streams
                    self._max_streams_semaphore.set_max_streams(new_max_streams)

        async def _response_closed(self, stream_id: int) -> None:
            if (
                self._h2_state.state_machine.state
                != h2.connection.ConnectionState.CLOSED
            ):
                stream = self._h2_state.streams.get(stream_id)
                if stream is not None and not stream.closed:
                    try:
                        self._h2_state.reset_stream(
                            stream_id, error_code=h2.errors.ErrorCodes.CANCEL
                        )
                    except h2.exceptions.ProtocolError:
                        pass

            async with self._state_lock:
                removed = self._events.pop(stream_id, None)
                if removed is not None:
                    self._max_streams_semaphore.release()

                if self._connection_terminated and not self._events:
                    await self.aclose()
                elif (
                    self._state == h2_mod.HTTPConnectionState.ACTIVE
                    and not self._events
                ):
                    self._state = h2_mod.HTTPConnectionState.IDLE
                    if self._keepalive_expiry is not None:
                        now = time.monotonic()
                        self._expire_at = now + self._keepalive_expiry
                    if self._used_all_stream_ids:  # pragma: nocover
                        await self.aclose()

        async def aclose(self) -> None:
            await super().aclose()
            limiter = getattr(self, "_max_streams_semaphore", None)
            if isinstance(limiter, _H2StreamLimiter):
                limiter.close()

    SafeAsyncHTTP2Connection.__name__ = base_cls.__name__
    SafeAsyncHTTP2Connection.__qualname__ = base_cls.__qualname__
    h2_mod.AsyncHTTP2Connection = SafeAsyncHTTP2Connection
    httpcore._async.AsyncHTTP2Connection = SafeAsyncHTTP2Connection
    httpcore.AsyncHTTP2Connection = SafeAsyncHTTP2Connection
    _HTTP2_PATCHED = True


class Network:
    """Connection pool manager for outbound HTTP requests."""

    _async_client: httpx.AsyncClient | None = None
    _image_client: httpx.AsyncClient | None = None
    _sync_client: httpx.Client | None = None
    _async_lock = asyncio.Lock()
    _image_lock = asyncio.Lock()
    _proxy_url: str | None = None

    @classmethod
    def set_proxy(cls, proxy_url: str | None) -> None:
        """Set the active proxy URL for client instances."""
        cls._proxy_url = proxy_url

    @classmethod
    def get_proxy(cls) -> str | None:
        """Return the active proxy URL."""
        if cls._proxy_url is not None:
            return cls._proxy_url or None
        import miku.state as shared

        return shared.appconf.get("proxy", {}).get("proxy_url") or None

    @classmethod
    async def get_async_client(cls) -> httpx.AsyncClient:
        """Return the shared HTTPX async client (HTTP/1.1 for standard upstream APIs)."""
        if cls._async_client is None or cls._async_client.is_closed:
            async with cls._async_lock:
                if cls._async_client is None or cls._async_client.is_closed:
                    cls._async_client = httpx.AsyncClient(
                        proxy=cls.get_proxy(),
                        trust_env=False,
                        http2=False,
                        timeout=httpx.Timeout(None, connect=15.0, pool=30.0, read=30.0),
                        limits=httpx.Limits(max_connections=100, max_keepalive_connections=30),
                        follow_redirects=False,
                    )
        return cls._async_client

    @classmethod
    async def get_image_client(cls) -> httpx.AsyncClient:
        """Return the shared HTTPX async client for image proxying (HTTP/2 enabled)."""
        if cls._image_client is None or cls._image_client.is_closed:
            async with cls._image_lock:
                if cls._image_client is None or cls._image_client.is_closed:
                    _ensure_safe_httpcore_http2()
                    cls._image_client = httpx.AsyncClient(
                        proxy=cls.get_proxy(),
                        trust_env=False,
                        http2=True,
                        timeout=httpx.Timeout(None, connect=15.0, pool=30.0, read=30.0),
                        limits=httpx.Limits(max_connections=150, max_keepalive_connections=50),
                        follow_redirects=False,
                    )
        return cls._image_client

    @classmethod
    def get_sync_client(cls) -> httpx.Client:
        """Return the shared synchronous HTTPX client."""
        if cls._sync_client is None or cls._sync_client.is_closed:
            cls._sync_client = httpx.Client(
                proxy=cls.get_proxy(),
                trust_env=False,
                http2=False,
                timeout=10.0,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                follow_redirects=False,
            )
        return cls._sync_client


async def close_global_client() -> None:
    """Cleanup global HTTP resources on application shutdown."""
    if Network._async_client is not None:
        client = Network._async_client
        Network._async_client = None
        try:
            if not client.is_closed:
                await client.aclose()
                print("[Shutdown] Global async client closed.")
        except Exception as e:
            print(f"[Shutdown] Error closing global async client: {e}")

    if Network._image_client is not None:
        client = Network._image_client
        Network._image_client = None
        try:
            if not client.is_closed:
                await client.aclose()
                print("[Shutdown] Global image async client closed.")
        except Exception as e:
            print(f"[Shutdown] Error closing global image async client: {e}")

    if Network._sync_client is not None:
        client = Network._sync_client
        Network._sync_client = None
        try:
            if not client.is_closed:
                client.close()
                print("[Shutdown] Global sync client closed.")
        except Exception as e:
            print(f"[Shutdown] Error closing global sync client: {e}")


def configure_bilibili_api_proxy(proxy_url: str | None) -> None:
    """Configure global proxying for bilibili_api requests."""
    if proxy_url:
        from urllib.parse import urlparse

        parsed = urlparse(proxy_url)
        host_port = parsed.hostname or "<configured>"
        if parsed.port:
            host_port = f"{host_port}:{parsed.port}"
        scheme = f"{parsed.scheme}://" if parsed.scheme else ""
        print(f"[Init] Setting global proxy for bilibili_api: {scheme}{host_port}")
        request_settings.set_proxy(proxy_url)
    else:
        print("[Init] No proxy URL found in config.toml or env vars, using direct connection.")
