"""Key-value cache behind an interface: idempotency keys and a hot-score cache.

Default is in-memory with TTL, so the service runs and tests without Redis. The
Redis implementation is the production path (shared across service replicas, which
an in-process dict is not) and wraps ``redis-py``; it is thin enough to read at a
glance and needs a running Redis to exercise.

Two uses in the API:

* **Idempotency** — a client that retries ``POST /score`` after a timeout must not
  create a second, differently-timestamped score. The idempotency key maps to the
  first result for a short TTL.
* **Hot-score cache** — scoring the same customer+input twice in quick succession
  returns the cached result instead of recomputing SHAP.
"""

from __future__ import annotations

import time
from typing import Protocol


class KeyValueStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...
    def delete(self, key: str) -> None: ...


class InMemoryCache:
    """Process-local cache with lazy TTL expiry.

    Correct for a single process and for tests. It is NOT shared between
    replicas, so production uses Redis; that is the whole reason the interface
    exists rather than reaching for a module-level dict.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        expires_at = time.monotonic() + ttl_seconds if ttl_seconds else None
        self._store[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class RedisCache:
    """Production cache backed by Redis. Requires a running server.

    Kept deliberately thin — the interface is the contract, this is just the wire.
    """

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        import redis  # imported lazily so the dependency is optional

        self._client = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        self._client.set(key, value, ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(key)
