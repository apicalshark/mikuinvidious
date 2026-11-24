# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Redis connection management with fast-fail safeguards."""

from __future__ import annotations

import os
from typing import Any

import redis.asyncio as redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

# Invariant: Redis calls must fail fast (1.0s timeout, 0 retries) to prevent
# stalling streaming/playback pipelines when Redis is unresponsive.
REDIS_SOCKET_TIMEOUT = 1.0
REDIS_POOL_MAX_CONNECTIONS = 100
REDIS_POOL_TIMEOUT = 0.5
REDIS_HEALTH_CHECK_INTERVAL = 30
REDIS_RETRY = Retry(NoBackoff(), 0)


def create_redis_client(conf: dict[str, Any] | None = None) -> redis.Redis:
    """Create and return an async Redis client configured with fast-fail safeguards."""
    redis_conf = (conf or {}).get("redis", {})
    redis_url = redis_conf.get("redis_url") or os.environ.get("REDIS_URL")
    pool_kwargs: dict[str, Any] = {
        "decode_responses": True,
        "max_connections": REDIS_POOL_MAX_CONNECTIONS,
        "timeout": REDIS_POOL_TIMEOUT,
        "socket_timeout": REDIS_SOCKET_TIMEOUT,
        "socket_connect_timeout": REDIS_SOCKET_TIMEOUT,
        "socket_keepalive": True,
        "health_check_interval": REDIS_HEALTH_CHECK_INTERVAL,
        "driver_info": None,
        "retry": REDIS_RETRY,
    }

    if redis_url:
        pool = redis.BlockingConnectionPool.from_url(redis_url, **pool_kwargs)
        pool.connection_kwargs["socket_timeout"] = REDIS_SOCKET_TIMEOUT
        pool.connection_kwargs["socket_connect_timeout"] = REDIS_SOCKET_TIMEOUT
        pool.connection_kwargs["retry"] = REDIS_RETRY
        return redis.Redis(connection_pool=pool)

    pool = redis.BlockingConnectionPool(
        host=redis_conf.get("host", "localhost"),
        port=redis_conf.get("port", 6379),
        username=redis_conf.get("username"),
        password=redis_conf.get("password") or os.environ.get("REDIS_PASSWORD"),
        **pool_kwargs,
    )
    return redis.Redis(connection_pool=pool)
