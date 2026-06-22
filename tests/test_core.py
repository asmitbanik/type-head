"""Unit tests for core components."""

import time

import pytest

from app.db import QueryStore, normalize_query
from app.trie import PrefixTrie
from app.cache.consistent_hash import HashRing
from app.cache.node import CacheNode
from app.cache.cluster import CacheCluster
from app.trending import TrendingTracker
from app.batch_writer import BatchWriter


@pytest.fixture
def store(tmp_path):
    return QueryStore(db_path=tmp_path / "test.db")


@pytest.fixture
def trie():
    return PrefixTrie(top_k=10)


class TestNormalize:
    def test_lowercase(self):
        assert normalize_query("iPhone") == "iphone"

    def test_whitespace(self):
        assert normalize_query("  java   tutorial  ") == "java tutorial"


class TestTrie:
    def test_prefix_match(self, trie):
        trie.build([
            ("iphone", 100),
            ("iphone 15", 80),
            ("ipad", 50),
        ])
        results = trie.suggest("iph")
        assert len(results) <= 10
        assert all(q.startswith("iph") for q, _ in results)
        assert results[0][0] == "iphone"
        assert results[0][1] == 100

    def test_empty_prefix_returns_top(self, trie):
        trie.build([("the", 1000), ("of", 900), ("and", 800)])
        results = trie.suggest("")
        assert len(results) == 3
        assert results[0][1] >= results[1][1]

    def test_no_match(self, trie):
        trie.build([("java", 100)])
        assert trie.suggest("zzz") == []

    def test_update_count(self, trie):
        trie.build([("java", 100)])
        trie.update_count("java", 200)
        assert trie.get_count("java") == 200


class TestConsistentHash:
    def test_add_node_remaps_fraction(self):
        ring = HashRing(vnodes_per_node=50)
        for i in range(4):
            ring.add_node(f"node-{i}")
        sample = [f"s:basic:{p}" for p in ["a", "ap", "app", "se", "java", "the"]]
        before = {k: ring.get_node(k) for k in sample}
        ring.add_node("node-4")
        after = {k: ring.get_node(k) for k in sample}
        moved = sum(1 for k in sample if before[k] != after[k])
        assert moved < len(sample)  # not all keys move

    def test_same_key_same_node(self):
        ring = HashRing()
        ring.add_node("a")
        ring.add_node("b")
        assert ring.get_node("test-key") == ring.get_node("test-key")


class TestCacheNode:
    def test_ttl_expiry(self):
        node = CacheNode("test", ttl_seconds=0.05)
        node.set("k", "v")
        assert node.get("k") == "v"
        time.sleep(0.1)
        assert node.get("k") is None

    def test_lru_eviction(self):
        node = CacheNode("test", max_entries=2, ttl_seconds=60)
        node.set("a", 1)
        node.set("b", 2)
        node.set("c", 3)
        assert node.get("a") is None


class TestTrending:
    def test_recency_scores_higher_for_recent(self):
        t = TrendingTracker(bucket_seconds=1, window_seconds=60, half_life_seconds=5)
        t.record("oldquery")
        old_score = t.recency_score("oldquery")
        time.sleep(0.05)
        t.record("newquery")
        new_score = t.recency_score("newquery")
        assert new_score >= old_score

    def test_sqrt_damper(self):
        t = TrendingTracker(bucket_seconds=1)
        for _ in range(10):
            t.record("spike")
        score_10 = t.recency_score("spike")
        t2 = TrendingTracker(bucket_seconds=1)
        for _ in range(100):
            t2.record("bigspike")
        score_100 = t2.recency_score("bigspike")
        assert score_100 < score_10 * 20  # sublinear


class TestBatchWriter:
    def test_aggregation(self, store, trie, tmp_path):
        cache = CacheCluster(node_count=2)
        store.bulk_upsert([("test", 10)])
        trie.build([("test", 10)])
        writer = BatchWriter(store, trie, cache, flush_interval=60, max_size=1000)
        for _ in range(50):
            writer.add("test")
        assert writer.pending_count("test") == 50
        rows = writer.flush()
        assert rows == 1
        assert store.get_count("test") == 60
