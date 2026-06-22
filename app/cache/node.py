"""Single cache node: LRU eviction + TTL expiry."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class CacheNode:
    """In-process LRU cache with TTL — stands in for a Redis instance."""

    def __init__(self, name: str, max_entries: int = 5000, ttl_seconds: float = 30.0) -> None:
        self.name = name
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            if time.monotonic() > entry.expires_at:
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._data:
                del self._data[key]
            while len(self._data) >= self.max_entries:
                self._data.popitem(last=False)
            self._data[key] = _CacheEntry(
                value=value,
                expires_at=time.monotonic() + self.ttl_seconds,
            )
            self._data.move_to_end(key)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def invalidate_prefix(self, prefix_key: str) -> int:
        """Delete all keys starting with prefix_key."""
        with self._lock:
            to_del = [k for k in self._data if k.startswith(prefix_key)]
            for k in to_del:
                del self._data[k]
            return len(to_del)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
