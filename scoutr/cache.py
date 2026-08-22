"""SQLite-Persistenz: Response-Cache (TTL) und Recherche-Verlauf.

Bewusst klein gehalten -- zwei Tabellen, keine ORM-Schicht.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    label      TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS cache_expires_idx ON cache(expires_at);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS history_session_idx ON history(session_id);
"""


def cache_key(kind: str, *parts: Any) -> str:
    """Stabiler Schluessel aus Art und beliebigen Bestandteilen."""
    raw = "\x1f".join([kind, *(str(part) for part in parts)])
    return f"{kind}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


@dataclass(slots=True)
class HistoryEntry:
    """Ein abgeschlossener Frage/Antwort-Durchlauf."""

    id: int
    session_id: str
    created_at: float
    question: str
    answer: str
    meta: dict[str, Any]


class Cache:
    """Schmaler Wrapper um eine SQLite-Datei."""

    def __init__(self, db_path: Path | str, ttl_hours: int = 24) -> None:
        self.db_path = Path(db_path)
        self.ttl_seconds = max(0, int(ttl_hours)) * 3600
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- Cache ------------------------------------------------------------
    def get(self, key: str) -> Any | None:
        """Gibt den gecachten Wert zurueck oder `None`, wenn abgelaufen/unbekannt."""
        now = time.time()
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute("SELECT payload, expires_at FROM cache WHERE key = ?", (key,))
            row = cur.fetchone()
            if row is None:
                return None
            if row["expires_at"] < now:
                cur.execute("DELETE FROM cache WHERE key = ?", (key,))
                return None
            try:
                return json.loads(row["payload"])
            except json.JSONDecodeError:
                return None

    def set(
        self,
        key: str,
        value: Any,
        *,
        kind: str = "",
        label: str = "",
        ttl: int | None = None,
    ) -> None:
        """Legt *value* (JSON-serialisierbar) unter *key* ab."""
        now = time.time()
        ttl_seconds = self.ttl_seconds if ttl is None else max(0, ttl)
        payload = json.dumps(value, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, kind, label, payload, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (key, kind or key.split(":", 1)[0], label, payload, now, now + ttl_seconds),
            )

    def purge_expired(self) -> int:
        """Loescht abgelaufene Eintraege, gibt deren Anzahl zurueck."""
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
            return cur.rowcount

    def clear(self, kind: str | None = None) -> int:
        """Leert den Cache (optional nur eine Art)."""
        with self._connect() as conn, closing(conn.cursor()) as cur:
            if kind:
                cur.execute("DELETE FROM cache WHERE kind = ?", (kind,))
            else:
                cur.execute("DELETE FROM cache")
            return cur.rowcount

    def stats(self) -> dict[str, int]:
        """Anzahl gueltiger Eintraege je Art."""
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "SELECT kind, COUNT(*) AS n FROM cache WHERE expires_at >= ? GROUP BY kind",
                (time.time(),),
            )
            return {row["kind"]: row["n"] for row in cur.fetchall()}

    # -- Verlauf ----------------------------------------------------------
    def add_history(
        self,
        session_id: str,
        question: str,
        answer: str,
        meta: dict[str, Any] | None = None,
    ) -> int:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO history (session_id, created_at, question, answer, meta)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    time.time(),
                    question,
                    answer,
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid or 0)

    def recent_history(self, limit: int = 20, session_id: str | None = None) -> list[HistoryEntry]:
        query = "SELECT * FROM history"
        params: list[Any] = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [
            HistoryEntry(
                id=row["id"],
                session_id=row["session_id"],
                created_at=row["created_at"],
                question=row["question"],
                answer=row["answer"],
                meta=json.loads(row["meta"] or "{}"),
            )
            for row in reversed(rows)
        ]
