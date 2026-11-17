# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Per-IP in-flight slot accounting for long-lived streaming responses.

Request-rate limits (``rate_limit.py``) count *arrivals*; a streaming
response is cheap to start but holds an upstream socket for its whole life,
so a handful of requests per minute can still pin dozens of sockets. This
counts *concurrently open* streams instead.

Isolation is strictly per client IP — there is deliberately no global
ceiling. A shared ceiling is a choke point: an attacker spread over many
IPs could fill it and lock every legitimate user out. Per-IP slots confine
each IP (or NAT) to its own allowance.

Single event loop, no awaits inside acquire/release, so no lock is needed.
"""


class IpSlot:
    """Idempotent lease for one in-flight stream; release exactly once."""

    __slots__ = ("_ip", "_limiter", "_released")

    def __init__(self, limiter: PerIpSlotLimiter, ip: str):
        self._limiter = limiter
        self._ip = ip
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._limiter._release(self._ip)


class PerIpSlotLimiter:
    def __init__(self, max_per_ip: int):
        self.max_per_ip = max_per_ip
        self.active_by_ip: dict[str, int] = {}

    def try_acquire(self, ip: str) -> IpSlot | None:
        key = ip or "unknown"
        cur = self.active_by_ip.get(key, 0)
        if cur >= self.max_per_ip:
            return None
        self.active_by_ip[key] = cur + 1
        return IpSlot(self, key)

    def active(self, ip: str) -> int:
        return self.active_by_ip.get(ip or "unknown", 0)

    def _release(self, key: str) -> None:
        cur = self.active_by_ip.get(key, 0)
        if cur <= 1:
            self.active_by_ip.pop(key, None)
        else:
            self.active_by_ip[key] = cur - 1

    def reset(self) -> None:
        self.active_by_ip.clear()
