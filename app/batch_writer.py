"""Batch writer — aggregate search submissions before flushing to SQLite."""

from __future__ import annotations

import atexit
import logging
import threading
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Callable, Optional

from app.config import BATCH_FLUSH_INTERVAL, BATCH_MAX_SIZE

if TYPE_CHECKING:
    from app.db import QueryStore
    from app.trie import PrefixTrie
    from app.cache.cluster import CacheCluster
    from app.trending import TrendingTracker

logger = logging.getLogger(__name__)


class BatchWriter:
    """
    Buffers search-count increments and flushes periodically or by size.
    Reduces primary-store writes by aggregating repeated queries.
    """

    def __init__(
        self,
        store: "QueryStore",
        trie: "PrefixTrie",
        cache: "CacheCluster",
        flush_interval: float = BATCH_FLUSH_INTERVAL,
        max_size: int = BATCH_MAX_SIZE,
    ) -> None:
        self.store = store
        self.trie = trie
        self.cache = cache
        self.flush_interval = flush_interval
        self.max_size = max_size
        self._buffer: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.flush_count = 0
        self.searches_buffered = 0
        self.rows_written = 0
        self._on_flush_callbacks: list[Callable[[dict[str, int]], None]] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()
        atexit.register(self.shutdown)

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.flush()

    def add_callback(self, cb: Callable[[dict[str, int]], None]) -> None:
        self._on_flush_callbacks.append(cb)

    def add(self, query: str) -> int:
        """Buffer one search for query. Returns buffered count for query."""
        with self._lock:
            self._buffer[query] += 1
            self.searches_buffered += 1
            count = self._buffer[query]
            should_flush = len(self._buffer) >= self.max_size
        if should_flush:
            self.flush()
        return count

    def pending_count(self, query: str) -> int:
        with self._lock:
            return self._buffer.get(query, 0)

    def pending_total(self) -> int:
        with self._lock:
            return sum(self._buffer.values())

    def flush(self) -> int:
        """Drain buffer to SQLite. Returns rows written."""
        with self._lock:
            if not self._buffer:
                return 0
            snapshot = dict(self._buffer)
            self._buffer.clear()

        rows = self.store.batch_increment(snapshot)
        self.rows_written += rows
        self.flush_count += 1

        for query, delta in snapshot.items():
            current = self.trie.get_count(query)
            new_count = current + delta if current else delta
            if current:
                self.trie.update_count(query, new_count)
            else:
                self.trie.add_or_update(query, new_count)
            self.cache.invalidate_query(query)

        for cb in self._on_flush_callbacks:
            try:
                cb(snapshot)
            except Exception:
                logger.exception("flush callback error")

        logger.info("Flushed %d queries (%d row writes)", len(snapshot), rows)
        return rows

    def _flush_loop(self) -> None:
        while not self._stop.wait(self.flush_interval):
            self.flush()

    def stats(self) -> dict:
        with self._lock:
            buffer_size = len(self._buffer)
            pending = sum(self._buffer.values())
        reduction = 0.0
        if self.searches_buffered > 0:
            reduction = 1.0 - (self.rows_written / self.searches_buffered)
        return {
            "buffer_distinct_queries": buffer_size,
            "buffer_pending_searches": pending,
            "searches_buffered": self.searches_buffered,
            "flush_count": self.flush_count,
            "rows_written": self.rows_written,
            "write_reduction": round(reduction, 4),
            "flush_interval_seconds": self.flush_interval,
            "max_batch_size": self.max_size,
        }
