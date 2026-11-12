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

import httpx

from miku.api.client import request_settings

# Concurrency limiter for outbound image proxy requests
image_limiter = asyncio.Semaphore(50)


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
        if cls._sync_client is None:
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
    if Network._async_client and not Network._async_client.is_closed:
        await Network._async_client.aclose()
        print("[Shutdown] Global async client closed.")
    if Network._image_client and not Network._image_client.is_closed:
        await Network._image_client.aclose()
        print("[Shutdown] Global image async client closed.")


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
