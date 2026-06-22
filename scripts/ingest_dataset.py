#!/usr/bin/env python3
"""Download/generate dataset and bulk-upsert into SQLite."""

from __future__ import annotations

import argparse
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DATASET_LIMIT, DATASET_URL, DB_PATH  # noqa: E402
from app.db import QueryStore, normalize_query  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache"
CACHED_FILE = CACHE_DIR / "count_1w.txt"

PRODUCT_WORDS = [
    "iphone", "samsung", "laptop", "charger", "headphones", "keyboard",
    "monitor", "tablet", "camera", "speaker", "watch", "case", "cable",
    "adapter", "mouse", "desk", "chair", "lamp", "java", "python",
    "tutorial", "course", "book", "guide", "review", "best", "cheap",
    "wireless", "bluetooth", "gaming", "pro", "max", "mini", "ultra",
]


def download_dataset(url: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHED_FILE.exists():
        print(f"Using cached dataset: {CACHED_FILE}")
        return CACHED_FILE
    print(f"Downloading {url} ...")
    urllib.request.urlretrieve(url, CACHED_FILE)
    print(f"Saved to {CACHED_FILE}")
    return CACHED_FILE


def parse_norvig(path: Path, limit: int) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            word, count_str = line.split("\t", 1)
            word = normalize_query(word)
            if not word:
                continue
            try:
                count = int(count_str)
            except ValueError:
                continue
            if count <= 0:
                continue
            rows.append((word, count))
            if len(rows) >= limit:
                break
    return rows


def generate_synthetic(limit: int, seed: int = 42) -> list[tuple[str, int]]:
    """Deterministic Zipfian product-style queries for offline use."""
    rng = random.Random(seed)
    seen: set[str] = set()

    def add(q: str) -> None:
        q = normalize_query(q)
        if q:
            seen.add(q)

    modifiers = ["best", "cheap", "new", "used", "free", "online", "2024", "2025", "pro", "max"]
    for word in PRODUCT_WORDS:
        add(word)
    for w1 in PRODUCT_WORDS:
        for w2 in PRODUCT_WORDS + modifiers:
            add(f"{w1} {w2}")
            if len(seen) >= limit:
                break
        if len(seen) >= limit:
            break
    if len(seen) < limit:
        for w1 in PRODUCT_WORDS:
            for w2 in modifiers:
                for w3 in PRODUCT_WORDS[:10]:
                    add(f"{w1} {w2} {w3}")
                    if len(seen) >= limit:
                        break
                if len(seen) >= limit:
                    break
            if len(seen) >= limit:
                break
    n = 0
    while len(seen) < limit:
        add(f"{rng.choice(PRODUCT_WORDS)} item {n}")
        n += 1

    queries = sorted(seen)
    rows = [
        (q, max(1, int(1_000_000 / ((i + 1) ** 0.8))))
        for i, q in enumerate(queries)
    ]
    rows.sort(key=lambda x: -x[1])
    return rows[:limit]


def ingest(rows: list[tuple[str, int]], batch_size: int = 5000) -> None:
    store = QueryStore()
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        store.bulk_upsert(chunk)
        total += len(chunk)
        print(f"  Upserted {total}/{len(rows)}", end="\r")
    print(f"\nDone — {total} queries in {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest typeahead dataset")
    parser.add_argument("--limit", type=int, default=DATASET_LIMIT)
    parser.add_argument("--synthetic", action="store_true", help="Generate offline data")
    parser.add_argument("--url", default=DATASET_URL)
    args = parser.parse_args()

    if args.synthetic:
        print(f"Generating {args.limit} synthetic queries...")
        rows = generate_synthetic(args.limit)
    else:
        try:
            path = download_dataset(args.url)
            rows = parse_norvig(path, args.limit)
        except Exception as e:
            print(f"Download failed ({e}), falling back to synthetic data")
            rows = generate_synthetic(args.limit)

    if len(rows) < 100_000 and not args.synthetic:
        print(f"Warning: only {len(rows)} rows — consider raising --limit")

    print(f"Ingesting {len(rows)} queries into {DB_PATH}")
    ingest(rows)


if __name__ == "__main__":
    main()
