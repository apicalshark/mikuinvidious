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

import ipaddress
import time
from functools import wraps

from quart import Response, request
from shared import appconf, appredis


def _trusted_networks():
    nets = []
    for raw in (appconf["rate_limit"].get("trusted_proxies") or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print("[RateLimit] Ignoring invalid TRUSTED_PROXIES entry")
    return nets


def _peer_is_trusted_proxy(peer: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_private:
        return True
    try:
        return any(addr in net for net in _trusted_networks())
    except TypeError:
        # IPv4 peer vs IPv6 network (or vice versa) never matches.
        return False


def get_client_ip(req=None) -> str:
    req = req or request
    peer = req.remote_addr or ""
    if _peer_is_trusted_proxy(peer):
        candidates = [req.headers.get("X-Real-IP", ""), *req.headers.get("X-Forwarded-For", "").split(",")]
        for raw in candidates:
            candidate = raw.strip()
            if not candidate:
                continue
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return "127.0.0.1"


class RateLimiter:
    """Redis-based sliding window rate limiter."""

    def __init__(self, redis_client, prefix="ratelimit:"):
        self.redis = redis_client
        self.prefix = prefix

    async def is_allowed(self, key: str, limit: int, window: int) -> tuple[bool, dict]:
        """
        Check if request is allowed under rate limit.
        Returns (allowed, info_dict) where info_dict has 'remaining', 'reset', 'limit'.
        """
        now = int(time.time())
        window_start = now - window
        redis_key = f"{self.prefix}{key}"
        member = f"{now}:{time.time_ns()}"

        # Use Redis sorted set for sliding window - atomic pipeline
        pipe = self.redis.pipeline()
        # Add current request first
        pipe.zadd(redis_key, {member: now})
        # Remove old entries
        pipe.zremrangebyscore(redis_key, 0, window_start)
        # Count current requests
        pipe.zcard(redis_key)
        # Set expiry
        pipe.expire(redis_key, window + 1)
        results = await pipe.execute()

        current_count = results[2]

        if current_count > limit:
            # Remove the blocked request to prevent permanent lockout
            await self.redis.zrem(redis_key, member)
            # Get oldest entry to calculate reset time
            oldest = await self.redis.zrange(redis_key, 0, 0, withscores=True)
            reset_time = int(oldest[0][1]) + window if oldest else now + window
            return False, {"remaining": 0, "reset": reset_time, "limit": limit, "retry_after": reset_time - now}

        return True, {"remaining": limit - current_count, "reset": now + window, "limit": limit}

    async def get_current_usage(self, key: str, window: int) -> int:
        """Get current request count for a key."""
        now = int(time.time())
        window_start = now - window
        redis_key = f"{self.prefix}{key}"
        await self.redis.zremrangebyscore(redis_key, 0, window_start)
        return await self.redis.zcard(redis_key)


# Global rate limiter instance
_rate_limiter: RateLimiter = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(appredis)
    return _rate_limiter


def rate_limit(limit: int = 60, window: int = 60, key_func=None, exempt_when=None):
    """
    Rate limiting decorator for Quart routes.

    Args:
        limit: Maximum requests allowed in the window
        window: Time window in seconds
        key_func: Function to generate rate limit key from request (default: IP-based)
        exempt_when: Callable that returns True to exempt from rate limiting
    """

    def decorator(f):
        @wraps(f)
        async def wrapped(*args, **kwargs):
            if not appconf["rate_limit"]["enabled"]:
                return await f(*args, **kwargs)

            # Check exemption
            if exempt_when and await exempt_when(request):
                return await f(*args, **kwargs)

            # Generate rate limit key
            if key_func:
                key = await key_func(request)
            else:
                key = f"{get_client_ip(request)}:{request.path}"

            limiter = get_rate_limiter()
            allowed, info = await limiter.is_allowed(key, limit, window)

            # Add rate limit headers
            from quart import g

            g.rate_limit_info = info

            if not allowed:
                resp = Response(f"Rate limit exceeded. Try again in {info['retry_after']} seconds.", status=429)
                resp.headers["Retry-After"] = str(max(info["retry_after"], 0))
                return resp

            return await f(*args, **kwargs)

        return wrapped

    return decorator


async def add_rate_limit_headers(response):
    """Add rate limit headers to response."""
    if not appconf["rate_limit"]["enabled"]:
        return response
    from quart import g

    if hasattr(g, "rate_limit_info"):
        info = g.rate_limit_info
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(info["reset"])
    return response


# Predefined rate limit configs for different endpoint types
RATE_LIMITS = {
    "strict": {"limit": 10, "window": 60},  # 10 req/min - for sensitive endpoints
    "normal": {"limit": 60, "window": 60},  # 60 req/min - for normal API endpoints
    "loose": {"limit": 300, "window": 60},  # 300 req/min - for static content
    "search": {"limit": 20, "window": 60},  # 20 req/min - for search
    "proxy": {"limit": 100, "window": 60},  # 100 req/min - for media proxy
    "auth": {"limit": 5, "window": 300},  # 5 req/5min - for auth endpoints
}
