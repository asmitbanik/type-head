#!/usr/bin/env python3
"""Benchmark latency, cache hit rate, write reduction, and rebalance."""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8000"

SUGGEST_PASSES = 5000
SEARCH_COUNT = 2000
HOT_QUERIES = [
    "the", "of", "and", "to", "in", "iphone", "java", "python",
    "laptop", "charger",
]


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def bench_suggest(client: httpx.Client, warm: bool) -> dict:
    latencies: list[float] = []
    prefixes = ["a", "ap", "app", "se", "th", "java", "iph", "the", ""]

  # warm cache
    if warm:
        for p in prefixes:
            for _ in range(50):
                client.get(f"{BASE}/suggest", params={"q": p})

    for _ in range(SUGGEST_PASSES):
        p = random.choice(prefixes)
        t0 = time.perf_counter()
        client.get(f"{BASE}/suggest", params={"q": p})
        latencies.append((time.perf_counter() - t0) * 1000)

    return {
        "p50_ms": round(percentile(latencies, 50), 3),
        "p95_ms": round(percentile(latencies, 95), 3),
        "p99_ms": round(percentile(latencies, 99), 3),
        "requests": len(latencies),
    }


def bench_searches(client: httpx.Client) -> dict:
    stats_before = client.get(f"{BASE}/stats").json()
    writes_before = stats_before["store"]["write_count"]
    buffered_before = stats_before["batch"]["searches_buffered"]

    for _ in range(SEARCH_COUNT):
        q = random.choice(HOT_QUERIES)
        client.post(f"{BASE}/search", json={"query": q})

    client.post(f"{BASE}/flush")
    stats_after = client.get(f"{BASE}/stats").json()
    writes_after = stats_after["store"]["write_count"]
    rows_written = writes_after - writes_before

    return {
        "searches": SEARCH_COUNT,
        "db_writes": rows_written,
        "write_reduction": round(1 - rows_written / SEARCH_COUNT, 4),
        "batch": stats_after["batch"],
    }


def bench_rebalance(client: httpx.Client) -> dict:
    sample_keys = [f"s:basic:{p}" for p in ["a", "ap", "app", "se", "th", "java", "iphone", "the"]]
    dist_before = {}
    for key in sample_keys:
        r = client.get(f"{BASE}/cache/route", params={"q": key.split(":")[-1]}).json()
        dist_before[key] = r["node"]

    add_result = client.post(f"{BASE}/cache/nodes/cache-4").json()

    moved = 0
    for key in sample_keys:
        r = client.get(f"{BASE}/cache/route", params={"q": key.split(":")[-1]}).json()
        if dist_before.get(key) != r["node"]:
            moved += 1

    client.delete(f"{BASE}/cache/nodes/cache-4")

    return {
        "sample_keys": len(sample_keys),
        "keys_moved_on_add": moved,
        "add_remap_count": add_result.get("remap_count", 0),
    }


def main() -> None:
    try:
        client = httpx.Client(timeout=30.0)
        health = client.get(f"{BASE}/healthz")
        health.raise_for_status()
    except Exception as e:
        print(f"Server not reachable at {BASE}: {e}")
        print("Start with: uvicorn app.main:app --port 8000")
        sys.exit(1)

    print("Benchmarking /suggest (cold)...")
    suggest_cold = bench_suggest(client, warm=False)

    print("Benchmarking /suggest (warm)...")
    suggest_warm = bench_suggest(client, warm=True)

    print("Benchmarking batch writes...")
    search_bench = bench_searches(client)

    print("Benchmarking cache rebalance...")
    rebalance = bench_rebalance(client)

    stats = client.get(f"{BASE}/stats").json()

    results = {
        "suggest_cold": suggest_cold,
        "suggest_warm": suggest_warm,
        "cache_hit_rate": stats.get("suggest_cache_hit_rate"),
        "cache_cluster_hit_rate": stats["cache"]["hit_rate"],
        "search_batch": search_bench,
        "rebalance": rebalance,
        "terms": stats["terms_indexed"],
    }

    out = ROOT / "bench_results.json"
    out.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
