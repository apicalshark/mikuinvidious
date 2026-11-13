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
REDIS_RETRY = Retry(NoBackoff(), 0)


def create_redis_client(conf: dict[str, Any] | None = None) -> redis.Redis:
    """Create and return an async Redis client configured with fast-fail safeguards."""
    redis_conf = (conf or {}).get("redis", {})
    redis_url = redis_conf.get("redis_url") or os.environ.get("REDIS_URL")

    if redis_url:
        pool = redis.ConnectionPool.from_url(redis_url, decode_responses=True)
        pool.connection_kwargs["socket_timeout"] = REDIS_SOCKET_TIMEOUT
        pool.connection_kwargs["socket_connect_timeout"] = REDIS_SOCKET_TIMEOUT
        pool.connection_kwargs["retry"] = REDIS_RETRY
        return redis.Redis(connection_pool=pool)

    return redis.Redis(
        host=redis_conf.get("host", "localhost"),
        port=redis_conf.get("port", 6379),
        username=redis_conf.get("username"),
        password=redis_conf.get("password") or os.environ.get("REDIS_PASSWORD"),
        decode_responses=True,
        socket_timeout=REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
        retry=REDIS_RETRY,
    )
