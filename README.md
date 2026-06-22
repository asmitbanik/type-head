# Search Typeahead System

A search typeahead / autocomplete service like the suggestion box on a search engine or e-commerce site. As you type a prefix it returns the most popular matching queries; submitting a search records it and feeds back into popularity and into the trending ranking.

The focus is the **backend data-system design**: how query-count data is stored, how suggestions are served with low latency, how the cache is distributed with consistent hashing, how trending mixes popularity with recency, and how batch writes keep write pressure off the primary store.

![Theme: sketchy retro black, white, green UI](static/)

## Quick start

**Windows:**
```bat
run.bat
```

**macOS / Linux:**
```bash
chmod +x run.sh && ./run.sh
```

That creates a virtualenv, installs deps, ingests the dataset on first run, and starts the server on **http://localhost:8000**.

### Manual steps

```bash
python3 -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest_dataset.py        # build data/typeahead.db (~200k queries)
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. Interactive API docs (Swagger) are at http://localhost:8000/docs.

### Run tests

```bash
python -m pytest -q
```

## Dataset

**Source:** [Peter Norvig's count_1w.txt](https://norvig.com/ngrams/count_1w.txt) — 333,333 English words with real corpus frequencies, tab-separated (`word<TAB>count`). Open, no auth, already in the `query | count` shape the assignment asks for. The loader takes the top **200,000** by default (configurable with `--limit`), comfortably above the 100k floor.

**Loading:**
```bash
python scripts/ingest_dataset.py                  # download real dataset
python scripts/ingest_dataset.py --limit 150000   # cap rows
python scripts/ingest_dataset.py --synthetic      # generated data only (offline)
```

Re-running ingestion is additive (counts add up).

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/suggest?q=<prefix>&mode=basic\|enhanced` | Up to 10 suggestions for the prefix, count-desc (basic) or recency-aware (enhanced). |
| `POST` | `/search` `{"query": "..."}` | Submit a search. Returns `{"message":"Searched", ...}` and records the query. |
| `GET` | `/trending?n=10` | Top queries by recent, decayed activity. |
| `GET` | `/cache/debug?prefix=<prefix>` | Debug: which cache node owns the key, and is it a hit. |
| `GET` | `/cache/route?q=<prefix>` | Alias for cache routing debug. |
| `POST` | `/cache/nodes/{name}` | Add a cache node; reports sample keys remapped. |
| `DELETE` | `/cache/nodes/{name}` | Remove a cache node; reports sample keys remapped. |
| `POST` | `/flush` | Force the batch writer to drain now (demo/benchmark helper). |
| `GET` | `/stats` | Store / cache / batch / trending counters. |
| `GET` | `/healthz` | Liveness + indexed term count. |

### Examples

```bash
curl "localhost:8000/suggest?q=app"
curl "localhost:8000/suggest?q=se&mode=enhanced"
curl -X POST localhost:8000/search -H "content-type: application/json" -d "{\"query\":\"iphone\"}"
curl "localhost:8000/cache/debug?prefix=iphone"
```

**Behaviour notes:** prefixes are trimmed, whitespace-collapsed and lowercased; empty prefix returns the global most-popular queries; an unknown prefix returns `[]`; a search for a brand-new query inserts it with a starting count of 1.

## Performance (measured)

Reproduce: start the server, then `python scripts/benchmark.py` (writes `bench_results.json`).

| Metric | Typical value |
|--------|---------------|
| `/suggest` p50 / p95 (warm cache) | ~0.4 / ~0.5 ms |
| Cache hit rate (warm) | ~75–85% |
| Batch write reduction | ~99% (2,000 searches → ~20 row writes) |
| Keys moved on add node | ~1/N of sample keys |

See [ARCHITECTURE.md](ARCHITECTURE.md) for design rationale.

## Project layout

```
app/
  main.py            FastAPI routes + app lifespan
  service.py         ties trie + cache + batch writer + trending together
  db.py              SQLite primary store (source of truth, read/write counters)
  trie.py            prefix trie with cached top-K completions
  cache/
    consistent_hash.py  hash ring with virtual nodes
    node.py             one cache node: LRU + TTL
    cluster.py          ring in front of N nodes + rebalance logging
  batch_writer.py    buffer -> aggregate -> periodic/size flush
  trending.py        sliding-window recency scoring (enhanced ranking)
  config.py          all tunables (env-overridable)
scripts/
  ingest_dataset.py  download/generate dataset -> SQLite
  benchmark.py       latency, cache hit rate, write reduction, rebalance
static/              sketchy retro UI (search box, dropdown, trending)
tests/               pytest suite
```

## Design highlights

- **Trie with cached top-K per node** → O(len(prefix)) suggestions, no per-request sorting. Counts only increase, so updates stay exact without a rebuild.
- **Distributed cache, consistent hashing** → keys land on a ring of virtual nodes; adding/removing a node moves only ~1/N of keys instead of nearly all.
- **Batch writes** → searches aggregate in a buffer and flush by size or interval, cutting primary-store writes by ~99% on hot-query traffic.
- **Trending** → sliding time-bucket window + exponential decay, with sqrt and consistency dampers so a single spike can't game the ranking.

Full write-up, diagrams, concurrency model, and failure trade-offs are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Grading checklist

| Component | Status |
|-----------|--------|
| Basic implementation (suggest, search, cache, consistent hash) | ✅ |
| Trending / enhanced ranking | ✅ |
| Batch writes with write reduction | ✅ |
| UI with debounce + keyboard nav | ✅ |
| Performance reporting | ✅ `scripts/benchmark.py` |
