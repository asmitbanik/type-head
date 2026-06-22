# Architecture & Design Notes

Design write-up for the search typeahead system: components, data flow, and trade-offs for each graded area.

## 1. Big picture

```
                              ┌─────────────────────────────────────────────┐
   browser (static/)          │                  FastAPI                     │
   ┌───────────────┐  GET /suggest   ┌──────────────────────────────────┐    │
   │ search box    │ ───────────────▶│ SuggestionService.suggest()      │    │
   │ dropdown      │                 │   1. cache.get(prefix)  ──────────┼──┐ │
   │ trending      │ ◀─ suggestions ─│   2. miss → trie.suggest(prefix)  │  │ │
   └───────────────┘                 │   3. cache.set(prefix, result)    │  │ │
          │                          └──────────────────────────────────┘  │ │
          │ POST /search                       │                            │ │
          ▼                                     ▼                           │ │
   ┌──────────────┐   add()    ┌──────────────────────┐  flush()  ┌─────────▼─▼──┐
   │ BatchWriter  │ ─────────▶ │ buffer {query:+N}    │ ────────▶ │ SQLite       │
   │ (aggregates) │            │ flush by size/interval│           │ (truth)      │
   └──────────────┘            └──────────────────────┘           └──────┬───────┘
          │ record()                                                     │ on flush
          ▼                                              trie.update() + cache.invalidate()
   ┌──────────────┐                                                       │
   │ Trending     │  recency-aware ranking (enhanced mode, /trending)     │
   └──────────────┘                                                       ▼
                              ┌───────────────── Distributed cache ──────────────┐
                              │ HashRing(vnodes) → cache-0 cache-1 cache-2 cache-3│
                              │ each node = LRU + TTL                             │
                              └──────────────────────────────────────────────────┘
```

Three layers, each with one job:

| Layer | Role |
|-------|------|
| **SQLite** | Source of truth. One table `queries(query, count)`. Read/write counters back performance claims. |
| **Trie** | Fast in-memory index. O(len(prefix)) suggestions via cached top-K per node. |
| **Distributed cache** | Rendered-result cache per prefix, routed by consistent-hash ring. |

Writes never touch SQLite synchronously — they go through the batch writer.

## 2. Data model and suggestion path

```sql
queries(query TEXT PRIMARY KEY, count INTEGER)
CREATE INDEX idx_queries_count ON queries(count DESC);
```

Query text is normalised once (`trim → collapse whitespace → lowercase`) so ingestion, search, cache keys, and trie all agree.

**Why trie with cached top-K** (not `LIKE 'pre%' ORDER BY count` per request): typeahead fires a request per keystroke. Each trie node caches the top-10 queries in its subtree. `/suggest` walks the prefix and reads the cached list — O(prefix length), no sort per request.

**Keeping top-K exact without full rebuilds:** counts only increase. On update, `Trie._refresh_path` re-offers the query along its path. A rising query can only enter or climb a node's top-K.

**Concurrency:** batch-writer thread mutates trie while request threads read. Cached lists are swapped as immutable tuples — readers never see half-updated state.

## 3. Distributed cache + consistent hashing

Cache keys: `s:<mode>:<prefix>`. Each entry has TTL (default 30s).

**Why consistent hashing:** `hash(key) % N` remaps almost every key when N changes. A ring with virtual nodes (150/node) means adding/removing a node moves only ~1/N keys on average.

`GET /cache/debug?prefix=` exposes owning node and hit/miss. `POST/DELETE /cache/nodes/{name}` add/remove nodes and log remap counts under `/stats`.

**Local-run note:** cache nodes are in-process `CacheNode` objects (LRU + TTL), not separate Redis processes — zero external deps. Swapping each node for a Redis client is a drop-in change behind `CacheCluster`.

## 4. Search writes and batching

`POST /search` does **not** write to SQLite directly:

1. `BatchWriter.add(query)` — buffer increment
2. `TrendingTracker.record(query)` — bump recency window
3. Return `{"message": "Searched", ...}` with best-known count (trie + buffer)
4. Targeted cache invalidation for affected prefixes

Buffer flushes when distinct queries ≥ `BATCH_MAX_SIZE` (100) or every `BATCH_FLUSH_INTERVAL` (2s).

**Write reduction:** 2,000 searches across 10 hot queries → ~20 row writes = **99% fewer** primary writes.

**Failure trade-off:** buffer contents lost on crash (at most a few seconds of counts). Acceptable for approximate popularity counters. Upgrade path: append-only WAL in front of buffer for durability.

## 5. Trending (recency-aware ranking)

| Question | Answer |
|----------|--------|
| How recent searches are tracked | Sparse `{time-bucket → count}` per query. 60 one-minute buckets over a 1-hour window. |
| How recency affects ranking | `enhanced_score = log10(1 + all_time_count) + recency_weight * recency_score` |
| Avoiding permanent over-ranking | Sliding window drops old buckets; exponential decay (15 min half-life) shrinks stale activity. |
| Anti-gaming | sqrt damper (volume sublinear) + consistency factor (spread across buckets beats single spike) |
| Cache invalidation | Search invalidates prefix keys immediately; TTL bounds staleness on enhanced rankings |

**Demo:** surge `sequoia` under prefix `se` — `mode=basic` unchanged; `mode=enhanced` surfaces it at #1 and it tops `/trending`.

## 6. Edge cases

| Case | Behaviour |
|------|-----------|
| Empty / missing prefix | Global most-popular queries |
| Mixed case / whitespace | Normalised before lookup |
| Unknown prefix | `[]` |
| Empty search submission | HTTP 400 |
| Brand-new query | Inserted with count 1 after flush; visible in buffer immediately |

## 7. Scaling beyond one box

1. Replace in-process cache nodes with Redis behind the same ring.
2. Move batch buffer behind Kafka for durable multi-instance aggregation.
3. Shard trie by first character across app instances.
4. Persist trending buckets to survive restarts.
