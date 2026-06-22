"""Distributed cache cluster backed by consistent hashing."""

from __future__ import annotations

import threading
from typing import Any, Optional

from app.cache.consistent_hash import HashRing
from app.cache.node import CacheNode
from app.config import CACHE_MAX_ENTRIES, CACHE_TTL_SECONDS, CACHE_VNODES_PER_NODE


class CacheCluster:
    """Routes cache keys to nodes via consistent-hash ring."""

    def __init__(
        self,
        node_count: int = 4,
        vnodes: int = CACHE_VNODES_PER_NODE,
        max_entries: int = CACHE_MAX_ENTRIES,
        ttl_seconds: float = CACHE_TTL_SECONDS,
    ) -> None:
        self._ring = HashRing(vnodes_per_node=vnodes)
        self._nodes: dict[str, CacheNode] = {}
        self._lock = threading.Lock()
        self.rebalance_log: list[dict] = []
        for i in range(node_count):
            self.add_node(f"cache-{i}", max_entries=max_entries, ttl_seconds=ttl_seconds)

    def add_node(
        self,
        name: str,
        *,
        max_entries: int = CACHE_MAX_ENTRIES,
        ttl_seconds: float = CACHE_TTL_SECONDS,
    ) -> dict:
        with self._lock:
            if name in self._nodes:
                return {"added": False, "node": name, "remaps": 0}
            node = CacheNode(name, max_entries=max_entries, ttl_seconds=ttl_seconds)
            remaps = self._ring.add_node(name)
            self._nodes[name] = node
            entry = {
                "action": "add",
                "node": name,
                "remap_count": len(remaps),
                "sample_remaps": remaps[:5],
            }
            self.rebalance_log.append(entry)
            return entry

    def remove_node(self, name: str) -> dict:
        with self._lock:
            if name not in self._nodes:
                return {"removed": False, "node": name, "remaps": 0}
            remaps = self._ring.remove_node(name)
            del self._nodes[name]
            entry = {
                "action": "remove",
                "node": name,
                "remap_count": len(remaps),
                "sample_remaps": remaps[:5],
            }
            self.rebalance_log.append(entry)
            return entry

    def _cache_key(self, mode: str, prefix: str) -> str:
        return f"s:{mode}:{prefix}"

    def get(self, mode: str, prefix: str) -> tuple[Optional[Any], str, bool]:
        """
        Returns (value, owning_node, is_hit).
        is_hit is True when value is not None.
        """
        key = self._cache_key(mode, prefix)
        node_name = self._ring.get_node(key)
        if not node_name or node_name not in self._nodes:
            return None, node_name or "none", False
        value = self._nodes[node_name].get(key)
        return value, node_name, value is not None

    def set(self, mode: str, prefix: str, value: Any) -> str:
        key = self._cache_key(mode, prefix)
        node_name = self._ring.get_node(key)
        if node_name and node_name in self._nodes:
            self._nodes[node_name].set(key, value)
        return node_name or "none"

    def invalidate_query(self, query: str, modes: tuple[str, ...] = ("basic", "enhanced")) -> int:
        """Invalidate all prefix caches that could include this query."""
        total = 0
        for i in range(len(query) + 1):
            prefix = query[:i]
            for mode in modes:
                key = self._cache_key(mode, prefix)
                node_name = self._ring.get_node(key)
                if node_name and node_name in self._nodes:
                    if self._nodes[node_name].delete(key):
                        total += 1
        return total

    def route(self, prefix: str, mode: str = "basic") -> dict:
        key = self._cache_key(mode, prefix)
        node_name = self._ring.get_node(key) or "none"
        _, _, is_hit = self.get(mode, prefix)
        return {
            "key": key,
            "prefix": prefix,
            "mode": mode,
            "node": node_name,
            "hit": is_hit,
        }

    def stats(self) -> dict:
        nodes_stats = {}
        total_hits = 0
        total_misses = 0
        for name, node in self._nodes.items():
            nodes_stats[name] = {
                "entries": node.size(),
                "hits": node.hits,
                "misses": node.misses,
                "hit_rate": round(node.hit_rate, 4),
            }
            total_hits += node.hits
            total_misses += node.misses
        total = total_hits + total_misses
        return {
            "node_count": len(self._nodes),
            "ring_nodes": self._ring.nodes,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "hit_rate": round(total_hits / total, 4) if total else 0.0,
            "nodes": nodes_stats,
            "rebalance_log": self.rebalance_log[-10:],
        }

    def sample_key_distribution(self, sample_keys: list[str]) -> dict[str, int]:
        dist: dict[str, int] = {}
        for key in sample_keys:
            node = self._ring.get_node(key) or "none"
            dist[node] = dist.get(node, 0) + 1
        return dist
