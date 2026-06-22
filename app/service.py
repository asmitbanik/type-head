"""Suggestion service — ties trie, cache, batch writer, and trending together."""

from __future__ import annotations

import logging
import time
from typing import Literal

from app.batch_writer import BatchWriter
from app.cache.cluster import CacheCluster
from app.config import CACHE_NODE_COUNT, TOP_K
from app.db import QueryStore, normalize_query
from app.trie import PrefixTrie
from app.trending import TrendingTracker

logger = logging.getLogger(__name__)

Mode = Literal["basic", "enhanced"]


class SuggestionService:
    """Core orchestration for suggestions, search, and trending."""

    def __init__(self) -> None:
        self.store = QueryStore()
        self.trie = PrefixTrie(top_k=TOP_K)
        self.cache = CacheCluster(node_count=CACHE_NODE_COUNT)
        self.trending = TrendingTracker()
        self.batch = BatchWriter(self.store, self.trie, self.cache)
        self._index_build_ms = 0.0
        self._suggest_calls = 0
        self._cache_hits = 0

    def initialize(self) -> None:
        t0 = time.perf_counter()
        rows = self.store.get_all()
        self.trie.build(rows)
        self._index_build_ms = (time.perf_counter() - t0) * 1000
        self.batch.start()
        logger.info(
            "Indexed %d terms in %.1f ms", self.trie.size, self._index_build_ms
        )

    def shutdown(self) -> None:
        self.batch.shutdown()

    def _effective_count(self, query: str) -> int:
        base = self.trie.get_count(query)
        pending = self.batch.pending_count(query)
        return base + pending

    def suggest(self, raw_prefix: str, mode: Mode = "basic") -> list[dict]:
        prefix = normalize_query(raw_prefix) if raw_prefix else ""
        self._suggest_calls += 1

        cached, _, hit = self.cache.get(mode, prefix)
        if hit and cached is not None:
            self._cache_hits += 1
            return cached

        if mode == "basic":
            results = self._suggest_basic(prefix)
        else:
            results = self._suggest_enhanced(prefix)

        # Add pending buffer counts for display freshness
        enriched = [
            {"query": q, "count": self._effective_count(q)} for q, _ in results
        ]
        self.cache.set(mode, prefix, enriched)
        return enriched

    def _suggest_basic(self, prefix: str) -> list[tuple[str, int]]:
        if not prefix:
            return self.trie.suggest("")
        return self.trie.suggest(prefix)

    def _suggest_enhanced(self, prefix: str) -> list[tuple[str, int]]:
        basic = self._suggest_basic(prefix)
        candidate_map: dict[str, int] = {q: c for q, c in basic}

        # Include surging queries not yet in all-time top
        for q, _ in self.trending.matching_trending(prefix, n=TOP_K * 3):
            if q not in candidate_map:
                candidate_map[q] = self._effective_count(q)

        scored = [
            (
                q,
                self.trending.enhanced_score(q, self._effective_count(q)),
            )
            for q in candidate_map
        ]
        scored.sort(key=lambda x: (-x[1], x[0]))
        # Return with effective counts for display
        return [(q, self._effective_count(q)) for q, _ in scored[:TOP_K]]

    def search(self, raw_query: str) -> dict:
        query = normalize_query(raw_query)
        if not query:
            raise ValueError("Query cannot be empty")

        self.batch.add(query)
        self.trending.record(query)
        count = self._effective_count(query)

        # Invalidate cache for affected prefixes
        self.cache.invalidate_query(query)

        return {
            "status": "Searched",
            "message": "Searched",
            "query": query,
            "count": count,
        }

    def get_trending(self, n: int = TOP_K) -> list[dict]:
        items = self.trending.top_trending(n)
        return [
            {
                "query": q,
                "score": round(s, 4),
                "count": self._effective_count(q),
            }
            for q, s in items
        ]

    def flush(self) -> dict:
        rows = self.batch.flush()
        return {"flushed_rows": rows}

    def stats(self) -> dict:
        total = self._suggest_calls
        return {
            "terms_indexed": self.trie.size,
            "index_build_ms": round(self._index_build_ms, 2),
            "suggest_calls": self._suggest_calls,
            "cache_hits": self._cache_hits,
            "suggest_cache_hit_rate": round(
                self._cache_hits / total, 4
            ) if total else 0.0,
            "store": {
                "read_count": self.store.read_count,
                "write_count": self.store.write_count,
                "term_count": self.store.count_terms(),
            },
            "batch": self.batch.stats(),
            "cache": self.cache.stats(),
            "trending": self.trending.stats(),
        }
