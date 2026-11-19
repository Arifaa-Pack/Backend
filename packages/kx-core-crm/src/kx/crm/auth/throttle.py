"""M3: what staff sign-in and password recovery need to slow an attacker down.

The app wires the Redis version (`arifaa_pack.adapters.RedisAuthThrottle`: automation's token buckets, shared
by every API process). `MemoryThrottle` is the in-process version: the default for tests and one-process
runs, and the app's fallback while Redis is unreachable (sign-in stays protected and available).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final, Protocol, runtime_checkable

from kx.core.identity import sha256_hex
from kx.core.settings import RateLimit

MAX_KEYS: Final = 50_000  # an in-process throttle forgets everything above this (memory bound)


@runtime_checkable
class AuthThrottle(Protocol):
    async def take(self, scope: str, key: str, limit: RateLimit) -> bool:
        """One request from bucket `scope:key`; False = over the limit."""
        ...

    async def count(self, key: str) -> int: ...

    async def bump(self, key: str, ttl_seconds: int) -> int:
        """Add one failure; the counter expires `ttl_seconds` after the latest one. Returns the new count."""
        ...

    async def clear(self, key: str) -> None: ...


def ip_key(ip: str | None) -> str:
    return ip or "unknown"


def account_key(email: str) -> str:
    """Per-account keys come from the typed address, so an unknown address is limited exactly like a real
    one (the answers never tell which addresses exist)."""
    return sha256_hex(email.strip().lower().encode())[:32]


class MemoryThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # tokens, last refill
        self._counters: dict[str, tuple[int, float]] = {}  # count, expires at

    def _bound(self) -> None:
        if len(self._buckets) + len(self._counters) > MAX_KEYS:
            self._buckets.clear()
            self._counters.clear()

    async def take(self, scope: str, key: str, limit: RateLimit) -> bool:
        self._bound()
        now, k = self._clock(), f"{scope}:{key}"
        tokens, at = self._buckets.get(k, (float(limit.burst), now))
        tokens = min(float(limit.burst), tokens + max(0.0, now - at) * limit.per_minute / 60)
        allowed = tokens >= 1
        self._buckets[k] = (tokens - 1 if allowed else tokens, now)
        return allowed

    async def count(self, key: str) -> int:
        n, expires = self._counters.get(key, (0, 0.0))
        return n if expires > self._clock() else 0

    async def bump(self, key: str, ttl_seconds: int) -> int:
        self._bound()
        n = await self.count(key) + 1
        self._counters[key] = (n, self._clock() + ttl_seconds)
        return n

    async def clear(self, key: str) -> None:
        self._counters.pop(key, None)
