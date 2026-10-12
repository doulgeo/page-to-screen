"""Tiny SQLite key/value cache for the Wikidata snapshot."""

import json
import sqlite3
import time
from pathlib import Path


class Cache:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, payload TEXT NOT NULL)"
        )
        self._db.commit()

    def get(self, key: str):
        """Return (payload, fetched_at), or None if the key was never stored."""
        row = self._db.execute(
            "SELECT payload, fetched_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), row[1]

    def set(self, key: str, payload, fetched_at: float | None = None) -> float:
        fetched_at = time.time() if fetched_at is None else fetched_at
        self._db.execute(
            "INSERT OR REPLACE INTO cache (key, fetched_at, payload) VALUES (?, ?, ?)",
            (key, fetched_at, json.dumps(payload, ensure_ascii=False)),
        )
        self._db.commit()
        return fetched_at

    def close(self) -> None:
        self._db.close()
