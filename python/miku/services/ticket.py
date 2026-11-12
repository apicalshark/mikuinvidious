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
"""Bilibili ticket management for API and CDN stream requests.

Guarantees ticket isolation invariant: bili_ticket is never attached to web
API cookies and is provided exclusively as x-bili-ticket on raw CDN proxy streams.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from miku.api.client import get_bili_ticket


class TicketManager:
    """Manages Bilibili's x-bili-ticket (JWT) for API and CDN requests."""

    _ticket: str | None = None
    _expiry: int = 0
    _lock = asyncio.Lock()
    _inflight: asyncio.Future | None = None
    _fail_until: float = 0.0
    TICKET_FAIL_COOLDOWN: float = 30.0

    @classmethod
    def _generate_trace_id(cls) -> str:
        """Generate a random x-bili-trace-id (Base64 hex string)."""
        return os.urandom(32).hex()

    @classmethod
    def _generate_session_id(cls) -> str:
        """Generate a random session_id (32-character hex)."""
        return os.urandom(16).hex()

    @classmethod
    async def _fetch_new_ticket(cls, cred: Any = None, redis_client: Any = None) -> tuple[str | None, int]:
        """Fetch and cache a fresh ticket via upstream API."""
        now = int(time.time())
        if cred is None:
            import miku.state as shared

            cred = shared.appcred
        if redis_client is None:
            import miku.state as shared

            redis_client = shared.appredis

        try:
            import miku.state as shared

            fetch_fn = getattr(shared, "get_bili_ticket", get_bili_ticket)
            ticket, expiry_ts = await fetch_fn(cred)
        except Exception as e:
            print(f"[Ticket] Error fetching ticket from upstream: {e}")
            cls._fail_until = time.time() + cls.TICKET_FAIL_COOLDOWN
            return None, 0

        if not ticket:
            cls._fail_until = time.time() + cls.TICKET_FAIL_COOLDOWN
            return None, 0

        cls._fail_until = 0.0
        try:
            real_ttl = int(expiry_ts) - now
            if real_ttl > 0 and redis_client:
                pipe = redis_client.pipeline(transaction=False)
                pipe.setex("miku_bili_ticket", real_ttl, ticket)
                pipe.setex("miku_bili_ticket_expiry", real_ttl, str(expiry_ts))
                await pipe.execute()
        except Exception as e:
            print(f"[Ticket] Error caching ticket in Redis: {e}")

        return ticket, expiry_ts

    @classmethod
    async def get_ticket(
        cls,
        force_refresh: bool = False,
        cred: Any = None,
        redis_client: Any = None,
    ) -> str | None:
        """Retrieve the active bili_ticket, using local memory, Redis, or upstream refresh."""
        if cred is None:
            import miku.state as shared

            cred = shared.appcred
        if redis_client is None:
            import miku.state as shared

            redis_client = shared.appredis

        async with cls._lock:
            now = int(time.time())
            if not force_refresh:
                if cls._ticket and now < cls._expiry - 60:
                    return cls._ticket

                if time.time() < cls._fail_until:
                    return None

                try:
                    cached_ticket, cached_expiry = await redis_client.mget(
                        "miku_bili_ticket", "miku_bili_ticket_expiry"
                    )
                except Exception:
                    cached_ticket, cached_expiry = None, None

                if cached_ticket and cached_expiry and now < int(cached_expiry) - 60:
                    cls._ticket = cached_ticket
                    cls._expiry = int(cached_expiry)
                    return cls._ticket
            else:
                from miku.api.client import refresh_bili_ticket

                refresh_bili_ticket()
                cls._generation = getattr(cls, "_generation", 0) + 1
                cls._ticket = None
                cls._expiry = 0
                try:
                    await redis_client.delete("miku_bili_ticket")
                    await redis_client.delete("miku_bili_ticket_expiry")
                except Exception:
                    pass

            current_gen = getattr(cls, "_generation", 0)
            inflight_gen = getattr(cls, "_inflight_gen", 0)
            if cls._inflight is None or (force_refresh and inflight_gen != current_gen):
                fut = asyncio.get_running_loop().create_future()
                cls._inflight = fut
                cls._inflight_gen = current_gen
                fetch = True
                fetch_gen = current_gen
            else:
                fetch = False
                fut = cls._inflight

        if not fetch:
            return await asyncio.shield(fut)

        ticket: str | None = None
        expiry_ts: int = 0
        try:
            ticket, expiry_ts = await cls._fetch_new_ticket(cred, redis_client)
            return ticket
        finally:
            if cls._inflight is fut:
                cls._inflight = None
            if ticket and fetch_gen == getattr(cls, "_generation", 0):
                cls._ticket = ticket
                cls._expiry = int(expiry_ts)
            if not fut.done():
                fut.set_result(ticket)
