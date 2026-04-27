"""SQLite-backed deduplication store for already-seen jobs."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    url        TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    source     TEXT NOT NULL,
    score      INTEGER NOT NULL DEFAULT 0,
    notified   INTEGER NOT NULL DEFAULT 0,
    seen_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_score ON seen(score);
CREATE INDEX IF NOT EXISTS idx_seen_source ON seen(source);
"""


class SeenJobsDB:
    """Tracks which job URLs have already been processed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path)
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def is_seen(self, url: str) -> bool:
        with self._connect() as con:
            cur = con.execute("SELECT 1 FROM seen WHERE url = ? LIMIT 1", (url,))
            return cur.fetchone() is not None

    def mark_seen(
        self,
        url: str,
        title: str,
        source: str,
        score: int,
        notified: bool,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO seen (url, title, source, score, notified, seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    url,
                    title,
                    source,
                    int(score),
                    1 if notified else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def stats(self) -> dict[str, int]:
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*), SUM(notified), MAX(score) FROM seen"
            ).fetchone()
        total, notified, top_score = row or (0, 0, 0)
        return {
            "total_seen": total or 0,
            "total_notified": notified or 0,
            "top_score": top_score or 0,
        }

    def purge_old_unnotified(self, days: int = 30) -> int:
        """Delete unnotified entries older than `days` so refreshed postings get re-evaluated.

        Jobs we already notified the user about are kept forever to prevent duplicate alerts.
        Returns the number of rows deleted.
        """
        with self._connect() as con:
            cur = con.execute(
                """
                DELETE FROM seen
                WHERE notified = 0
                  AND seen_at < datetime('now', ?)
                """,
                (f"-{days} days",),
            )
            return cur.rowcount

    def reset(self) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM seen")
