"""Central configuration — all tunables, overridable via environment."""

import os
from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "typeahead.db"

# Dataset ingestion
DATASET_URL = os.getenv(
    "DATASET_URL",
    "https://norvig.com/ngrams/count_1w.txt",
)
DATASET_LIMIT = int(os.getenv("DATASET_LIMIT", "200000"))

# Suggestion engine
TOP_K = int(os.getenv("TOP_K", "10"))

# Distributed cache
CACHE_NODE_COUNT = int(os.getenv("CACHE_NODE_COUNT", "4"))
CACHE_VNODES_PER_NODE = int(os.getenv("CACHE_VNODES_PER_NODE", "150"))
CACHE_MAX_ENTRIES = int(os.getenv("CACHE_MAX_ENTRIES", "5000"))
CACHE_TTL_SECONDS = float(os.getenv("CACHE_TTL_SECONDS", "30"))

# Batch writer
BATCH_MAX_SIZE = int(os.getenv("BATCH_MAX_SIZE", "100"))
BATCH_FLUSH_INTERVAL = float(os.getenv("BATCH_FLUSH_INTERVAL", "2.0"))

# Trending / enhanced ranking
TRENDING_WINDOW_SECONDS = int(os.getenv("TRENDING_WINDOW_SECONDS", "3600"))
TRENDING_BUCKET_SECONDS = int(os.getenv("TRENDING_BUCKET_SECONDS", "60"))
TRENDING_HALF_LIFE_SECONDS = float(os.getenv("TRENDING_HALF_LIFE_SECONDS", "900"))
TRENDING_RECENCY_WEIGHT = float(os.getenv("TRENDING_RECENCY_WEIGHT", "2.0"))
