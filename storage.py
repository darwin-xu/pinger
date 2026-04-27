"""SQLite-backed metrics store.

Schema
──────
metrics(id, ts TEXT, host TEXT, probe TEXT, data TEXT)

*ts* is always stored as a naive UTC ISO-8601 string.
*data* is a JSON blob of the probe result dict.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "pinger.db"

# Single persistent connection shared across threads.
# WAL mode allows concurrent readers alongside the writer.
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db() -> None:
    with _lock:
        conn = _db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    TEXT    NOT NULL,
                host  TEXT    NOT NULL,
                probe TEXT    NOT NULL,
                data  TEXT    NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_host_probe_ts "
            "ON metrics (host, probe, ts)"
        )
        conn.commit()


def save(host: str, probe: str, data: dict) -> None:
    with _lock:
        conn = _db()
        conn.execute(
            "INSERT INTO metrics (ts, host, probe, data) VALUES (?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), host, probe, json.dumps(data)),
        )
        conn.commit()


def recent(host: str, probe: str, limit: int = 20,
           since: str | None = None, until: str | None = None) -> list[dict]:
    """Return up to *limit* most recent rows for (host, probe), newest first.

    If *since* is given (naive UTC ISO prefix), only rows with ts >= since
    are returned.  If *until* is given, only rows with ts <= until are
    returned.  The string comparison works because timestamps are stored
    as ISO-8601 and sort lexicographically.
    """
    with _lock:
        if since and until:
            rows = _db().execute(
                "SELECT ts, data FROM metrics "
                "WHERE host=? AND probe=? AND ts >= ? AND ts <= ? "
                "ORDER BY ts DESC LIMIT ?",
                (host, probe, since, until, limit),
            ).fetchall()
        elif since:
            rows = _db().execute(
                "SELECT ts, data FROM metrics "
                "WHERE host=? AND probe=? AND ts >= ? "
                "ORDER BY ts DESC LIMIT ?",
                (host, probe, since, limit),
            ).fetchall()
        elif until:
            rows = _db().execute(
                "SELECT ts, data FROM metrics "
                "WHERE host=? AND probe=? AND ts <= ? "
                "ORDER BY ts DESC LIMIT ?",
                (host, probe, until, limit),
            ).fetchall()
        else:
            rows = _db().execute(
                "SELECT ts, data FROM metrics "
                "WHERE host=? AND probe=? "
                "ORDER BY ts DESC LIMIT ?",
                (host, probe, limit),
            ).fetchall()
    return [{"ts": row[0], **json.loads(row[1])} for row in rows]


def latest(host: str, probe: str) -> dict | None:
    rows = recent(host, probe, limit=1)
    return rows[0] if rows else None
