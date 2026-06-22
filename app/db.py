"""SQLite primary store — source of truth for query counts."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterator

from app.config import DB_PATH


def normalize_query(query: str) -> str:
    """Trim, collapse whitespace, lowercase."""
    return " ".join(query.strip().split()).lower()


class QueryStore:
    """Thread-safe SQLite store with read/write counters."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self.read_count = 0
        self.write_count = 0
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS queries (
                        query TEXT PRIMARY KEY,
                        count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_queries_count ON queries(count DESC)"
                )
                conn.commit()
            finally:
                conn.close()

    def bulk_upsert(self, rows: list[tuple[str, int]]) -> int:
        """Insert or add counts. Returns rows affected."""
        if not rows:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT INTO queries (query, count) VALUES (?, ?)
                    ON CONFLICT(query) DO UPDATE SET count = count + excluded.count
                    """,
                    rows,
                )
                conn.commit()
                self.write_count += len(rows)
                return len(rows)
            finally:
                conn.close()

    def batch_increment(self, increments: dict[str, int]) -> int:
        """Apply aggregated increments in one transaction."""
        rows = [(q, c) for q, c in increments.items() if c > 0]
        return self.bulk_upsert(rows)

    def get_count(self, query: str) -> int:
        normalized = normalize_query(query)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT count FROM queries WHERE query = ?", (normalized,)
                ).fetchone()
                self.read_count += 1
                return int(row["count"]) if row else 0
            finally:
                conn.close()

    def get_all(self) -> list[tuple[str, int]]:
        """Load all queries for trie rebuild."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT query, count FROM queries ORDER BY count DESC"
                ).fetchall()
                self.read_count += 1
                return [(r["query"], int(r["count"])) for r in rows]
            finally:
                conn.close()

    def get_top_global(self, limit: int = 10) -> list[tuple[str, int]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT query, count FROM queries ORDER BY count DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                self.read_count += 1
                return [(r["query"], int(r["count"])) for r in rows]
            finally:
                conn.close()

    def count_terms(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS c FROM queries").fetchone()
                self.read_count += 1
                return int(row["c"])
            finally:
                conn.close()

    def iter_prefix_matches(
        self, prefix: str, limit: int = 100
    ) -> Iterator[tuple[str, int]]:
        """Fallback prefix scan (used in tests / cold paths)."""
        normalized = normalize_query(prefix)
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT query, count FROM queries
                    WHERE query LIKE ? ESCAPE '\\'
                    ORDER BY count DESC
                    LIMIT ?
                    """,
                    (f"{normalized}%", limit),
                ).fetchall()
                self.read_count += 1
                for r in rows:
                    yield r["query"], int(r["count"])
            finally:
                conn.close()
