"""SQLite-backed cache for web search results.

Keyed by an arbitrary string (usually "<provider>::<query>::<k>"),
stores JSON-serializable values with a TTL.
"""

import json
import sqlite3
import time
from pathlib import Path


class Cache:
    def __init__(self, path: str, ttl_hours: float):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False allows use from the process pool's workers.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)"
        )
        self.conn.commit()
        self.ttl = float(ttl_hours) * 3600.0

    def get(self, key: str):
        row = self.conn.execute(
            "SELECT v, ts FROM kv WHERE k=?", (key,)
        ).fetchone()
        if not row:
            return None
        v, ts = row
        if time.time() - ts > self.ttl:
            self.conn.execute("DELETE FROM kv WHERE k=?", (key,))
            self.conn.commit()
            return None
        try:
            return json.loads(v)
        except Exception:
            return None

    def put(self, key: str, value):
        try:
            payload = json.dumps(value, ensure_ascii=False)
        except Exception:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO kv VALUES (?, ?, ?)",
            (key, payload, time.time()),
        )
        self.conn.commit()