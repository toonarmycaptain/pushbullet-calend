"""SQLite storage for tracking sent SMS messages."""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    instance_start TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    message_hash TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    UNIQUE(event_id, instance_start, phone_number, message_hash)
)
"""


def message_hash(text: str) -> str:
    """Return a short hash of *text* for dedup purposes."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class SentStore:
    """Tracks which SMS messages have already been sent."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def was_sent(
        self,
        event_id: str,
        instance_start: str,
        phone_number: str,
        message: str,
    ) -> bool:
        """Return True if this exact message was already sent."""
        row = self._conn.execute(
            "SELECT 1 FROM sent_messages"
            " WHERE event_id = ? AND instance_start = ?"
            " AND phone_number = ? AND message_hash = ?",
            (event_id, instance_start, phone_number, message_hash(message)),
        ).fetchone()
        return row is not None

    def record_sent(
        self,
        event_id: str,
        instance_start: str,
        phone_number: str,
        message: str,
        *,
        status: str = "sent",
    ) -> None:
        """Record that a message was sent (or failed)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO sent_messages"
            " (event_id, instance_start, phone_number, message_hash, sent_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                event_id,
                instance_start,
                phone_number,
                message_hash(message),
                datetime.now().isoformat(),
                status,
            ),
        )
        self._conn.commit()
