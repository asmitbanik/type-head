"""Trending tracker — sliding-window recency scoring for enhanced ranking."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from typing import DefaultDict

from app.config import (
    TRENDING_BUCKET_SECONDS,
    TRENDING_HALF_LIFE_SECONDS,
    TRENDING_RECENCY_WEIGHT,
    TRENDING_WINDOW_SECONDS,
    TOP_K,
)


class TrendingTracker:
    """
    Tracks recent search activity in time buckets with exponential decay.
    Used for enhanced suggestion ranking and /trending endpoint.
    """

    def __init__(
        self,
        window_seconds: int = TRENDING_WINDOW_SECONDS,
        bucket_seconds: int = TRENDING_BUCKET_SECONDS,
        half_life_seconds: float = TRENDING_HALF_LIFE_SECONDS,
        recency_weight: float = TRENDING_RECENCY_WEIGHT,
    ) -> None:
        self.window_seconds = window_seconds
        self.bucket_seconds = bucket_seconds
        self.half_life_seconds = half_life_seconds
        self.recency_weight = recency_weight
        self._buckets: DefaultDict[str, dict[int, int]] = defaultdict(dict)
        self._lock = threading.Lock()

    def _current_bucket(self) -> int:
        return int(time.time()) // self.bucket_seconds

    def _prune(self, query: str) -> None:
        buckets = self._buckets.get(query)
        if not buckets:
            return
        cutoff = self._current_bucket() - (self.window_seconds // self.bucket_seconds)
        stale = [b for b in buckets if b < cutoff]
        for b in stale:
            del buckets[b]
        if not buckets:
            del self._buckets[query]

    def record(self, query: str) -> None:
        with self._lock:
            bucket = self._current_bucket()
            self._buckets[query][bucket] = self._buckets[query].get(bucket, 0) + 1
            self._prune(query)

    def _decay_weight(self, bucket: int, now_bucket: int) -> float:
        age_seconds = (now_bucket - bucket) * self.bucket_seconds
        return math.pow(0.5, age_seconds / self.half_life_seconds)

    def recency_score(self, query: str) -> float:
        """Decayed, sqrt-damped, consistency-aware recency score."""
        with self._lock:
            buckets = dict(self._buckets.get(query, {}))
        if not buckets:
            return 0.0

        now_bucket = self._current_bucket()
        raw = sum(
            count * self._decay_weight(b, now_bucket) for b, count in buckets.items()
        )
        # sqrt damper — volume is sublinear
        damped = math.sqrt(raw)
        # consistency factor — reward spread across buckets
        active_buckets = len(buckets)
        total_count = sum(buckets.values())
        consistency = active_buckets / max(1, math.log1p(total_count))
        return damped * min(1.0, consistency)

    def enhanced_score(self, query: str, all_time_count: int) -> float:
        """
        Combine historical popularity (log-scaled) with recency.
        log10 keeps evergreen queries competitive; recency_weight lets surges climb.
        """
        historical = math.log10(1 + all_time_count)
        recency = self.recency_score(query)
        return historical + self.recency_weight * recency

    def top_trending(self, n: int = TOP_K) -> list[tuple[str, float]]:
        with self._lock:
            queries = list(self._buckets.keys())
        scored = [(q, self.recency_score(q)) for q in queries]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:n]

    def matching_trending(self, prefix: str, n: int = TOP_K) -> list[tuple[str, float]]:
        trending = self.top_trending(n=100)
        return [(q, s) for q, s in trending if q.startswith(prefix)][:n]

    def stats(self) -> dict:
        with self._lock:
            tracked = len(self._buckets)
        return {
            "tracked_queries": tracked,
            "window_seconds": self.window_seconds,
            "bucket_seconds": self.bucket_seconds,
            "half_life_seconds": self.half_life_seconds,
            "recency_weight": self.recency_weight,
        }
