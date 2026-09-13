"""Tiny in-memory TTL cache. One process, one dashboard user -- no need for
anything heavier than a dict behind a lock. Two roles:
  1. Normal case: avoid re-hitting every upstream API on every page load
     within a short window (CACHE_TTL_SECONDS in orchestrator.py).
  2. Graceful degrade: if a refresh produces nothing usable (every source
     down), the orchestrator falls back to the last good payload here,
     however old, rather than showing an empty dashboard.
"""
from __future__ import annotations

import threading
import time


class TTLCache:
    def __init__(self, ttl_seconds: float):
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple[object, float]] = {}

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._store.get(key)
        if not entry:
            return None
        value, ts = entry
        if time.time() - ts > self.ttl:
            return None
        return value

    def get_stale(self, key: str) -> tuple[object, float] | None:
        with self._lock:
            return self._store.get(key)

    def set(self, key: str, value: object) -> None:
        with self._lock:
            self._store[key] = (value, time.time())
