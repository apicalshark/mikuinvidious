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
"""Shared application state, runtime configuration, and service facades.

This module provides the central entrypoint and backward-compatibility facade
for configuration, client pools, caching, and template rendering.
"""

from __future__ import annotations

from quart import render_template, request

from miku.api.client import get_bili_ticket
from miku.core.app import create_app
from miku.core.config import (
    CACHE_SCHEMA_VERSION,
    CACHE_STALE_GRACE_SECONDS,
    UPSTREAM_RESPONSE_BLACKLIST,
    _int_env,
    compute_app_version,
    compute_asset_version,
    compute_commit_url,
    deep_update,
    load_config,
)
from miku.core.config import (
    cache_minutes as _cache_minutes,
)
from miku.core.fingerprints import get_app_headers
from miku.services.cache import (
    SimpleCache,
    safe_json_loads,
    versioned_key,
)
from miku.services.cache import (
    cache_get as _svc_cache_get,
)
from miku.services.cache import (
    cache_get_stale as _svc_cache_get_stale,
)
from miku.services.cache import (
    cache_set as _svc_cache_set,
)
from miku.services.credential import decrypt_if_encrypted, init_credentials
from miku.services.network import (
    Network,
    close_global_client,
    configure_bilibili_api_proxy,
    image_limiter,
)
from miku.services.redis import (
    REDIS_RETRY,
    REDIS_SOCKET_TIMEOUT,
    create_redis_client,
)
from miku.services.ticket import TicketManager
from miku.ui.renderer import (
    detect_locale,
    detect_theme,
    render_template_with_theme,
)

# 1. Load runtime configuration
appconf = load_config()

# Backward-compatibility alias for internal versioned key helper
_versioned_key = versioned_key


def cache_minutes(key: str, default: int = 5) -> int:
    """Read a [cache] TTL (in minutes); invalid values fall back to default."""
    return _cache_minutes(appconf, key, default)


def get_common_headers(bili_conf):
    """Get common headers for Bilibili API requests from config."""
    return get_app_headers(bili_conf)


COMMON_HEADERS = get_common_headers({})

# 2. Version and asset resolution
_compute_asset_version = compute_asset_version
_compute_app_version = compute_app_version
_compute_commit_url = compute_commit_url

ASSET_VERSION = compute_asset_version()
APP_VERSION, GIT_COMMIT = compute_app_version()

# 3. Fast-fail Redis client
appredis = create_redis_client(appconf)

# 4. View cache
appcache = SimpleCache(appredis)


async def cache_get(key: str, max_age_seconds: float):
    """Return the cached payload dict if fresh, else None."""
    return await _svc_cache_get(appredis, key, max_age_seconds)


async def cache_get_stale(key: str, max_age_seconds: float, stale_window_seconds: float = CACHE_STALE_GRACE_SECONDS):
    """Return (payload, is_stale) for fresh-or-expired entries."""
    return await _svc_cache_get_stale(appredis, key, max_age_seconds, stale_window_seconds)


async def cache_set(key: str, payload: dict, ttl_seconds: int):
    """Store a payload dict with a fetched_at stamp; failures are silent."""
    await _svc_cache_set(appredis, key, payload, ttl_seconds)


# 5. Global HTTP client backward-compatibility helper
def get_global_httpx_client(async_client: bool = True):
    return Network.get_async_client() if async_client else Network.get_sync_client()


# 6. Credentials initialization
appcred = init_credentials(appconf)

# 7. Quart application setup
app = create_app(appconf)

# 8. Configure global proxy for bilibili_api
configure_bilibili_api_proxy(Network.get_proxy())

__all__ = [
    "APP_VERSION",
    "ASSET_VERSION",
    "CACHE_SCHEMA_VERSION",
    "CACHE_STALE_GRACE_SECONDS",
    "COMMON_HEADERS",
    "GIT_COMMIT",
    "Network",
    "REDIS_RETRY",
    "REDIS_SOCKET_TIMEOUT",
    "SimpleCache",
    "TicketManager",
    "UPSTREAM_RESPONSE_BLACKLIST",
    "_compute_app_version",
    "_compute_asset_version",
    "_compute_commit_url",
    "_int_env",
    "_versioned_key",
    "app",
    "appcache",
    "appconf",
    "appcred",
    "appredis",
    "cache_get",
    "cache_get_stale",
    "cache_minutes",
    "cache_set",
    "close_global_client",
    "decrypt_if_encrypted",
    "deep_update",
    "detect_locale",
    "detect_theme",
    "get_bili_ticket",
    "get_common_headers",
    "get_global_httpx_client",
    "image_limiter",
    "render_template",
    "render_template_with_theme",
    "request",
    "safe_json_loads",
]
