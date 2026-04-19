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
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "pinger.db"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
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


def save(host: str, probe: str, data: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO metrics (ts, host, probe, data) VALUES (?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), host, probe, json.dumps(data)),
        )


def recent(host: str, probe: str, limit: int = 20) -> list[dict]:
    """Return up to *limit* most recent rows for (host, probe), newest first."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT ts, data FROM metrics "
            "WHERE host=? AND probe=? "
            "ORDER BY ts DESC LIMIT ?",
            (host, probe, limit),
        ).fetchall()
    return [{"ts": row[0], **json.loads(row[1])} for row in rows]


def latest(host: str, probe: str) -> dict | None:
    rows = recent(host, probe, limit=1)
    return rows[0] if rows else None
