"""SQLite-backed metrics store.

Schema
──────
metrics(id, ts TEXT, host TEXT, probe TEXT, data TEXT)
rollup(host, probe, bucket, n, nfail, first_ts, last_ts, sums, fails)
rollup_meta(host, probe, upto)

*ts* is always stored as a naive UTC ISO-8601 string.
*data* is a JSON blob of the probe result dict.

*rollup* holds one row per closed hour of ping/tcp samples (counts, per-field
sums, failure timestamps).  Wide chart windows are served from it instead of
scanning raw rows, and raw rows older than the retention period can be pruned
once they have been rolled up.  *rollup_meta.upto* is the exclusive hour
boundary up to which a (host, probe) pair has been rolled up.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "pinger.db"

# One writer connection (guarded by _lock).  WAL mode lets any number of
# readers run concurrently with it, so reads use a separate small pool of
# read-only connections and never wait for the write lock.
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

_POOL_MAX = 8
_pool: list[tuple[Path, sqlite3.Connection]] = []
_pool_lock = threading.Lock()

ROLLUP_PROBES = ("ping", "tcp")
ROLLUP_FIELDS = (
    "avg", "min", "max", "jitter", "loss", "p95", "rtt",
    "download_mbps", "upload_mbps",
)
_ROLLUP_GRACE = timedelta(minutes=5)  # let in-flight writes land before closing an hour
_MAX_FAILS_PER_BUCKET = 20
_MAX_FAILURE_MARKERS = 500
_RAW_TAIL_LIMIT = 50_000


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


@contextmanager
def _read():
    """Borrow a read-only connection from the pool."""
    path = DB_PATH
    conn = None
    with _pool_lock:
        while _pool:
            pooled_path, pooled = _pool.pop()
            if pooled_path == path:
                conn = pooled
                break
            pooled.close()
    if conn is None:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        with _pool_lock:
            keep = len(_pool) < _POOL_MAX and DB_PATH == path
            if keep:
                _pool.append((path, conn))
        if not keep:
            conn.close()


def close_all() -> None:
    """Close the writer and every pooled reader (used on shutdown and in tests)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
    with _pool_lock:
        while _pool:
            _pool.pop()[1].close()


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rollup (
                host     TEXT    NOT NULL,
                probe    TEXT    NOT NULL,
                bucket   TEXT    NOT NULL,
                n        INTEGER NOT NULL,
                nfail    INTEGER NOT NULL,
                first_ts TEXT    NOT NULL,
                last_ts  TEXT    NOT NULL,
                sums     TEXT    NOT NULL,
                fails    TEXT    NOT NULL,
                PRIMARY KEY (host, probe, bucket)
            ) WITHOUT ROWID
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rollup_meta (
                host  TEXT NOT NULL,
                probe TEXT NOT NULL,
                upto  TEXT NOT NULL,
                PRIMARY KEY (host, probe)
            ) WITHOUT ROWID
        """)
        conn.commit()


def save(host: str, probe: str, data: dict) -> tuple[str, str]:
    """Persist a sample; returns the (ts, json payload) that was stored."""
    ts = datetime.utcnow().isoformat()
    payload = json.dumps(data)
    with _lock:
        conn = _db()
        conn.execute(
            "INSERT INTO metrics (ts, host, probe, data) VALUES (?, ?, ?, ?)",
            (ts, host, probe, payload),
        )
        conn.commit()
    return ts, payload


def recent_payloads(host: str, probe: str, since: str) -> list[tuple[str, str]]:
    """Raw (ts, json payload) rows with ts >= *since*, oldest first."""
    with _read() as conn:
        return conn.execute(
            "SELECT ts, data FROM metrics WHERE host=? AND probe=? AND ts >= ? ORDER BY ts",
            (host, probe, since),
        ).fetchall()


def recent(host: str, probe: str, limit: int = 20,
           since: str | None = None, until: str | None = None) -> list[dict]:
    """Return up to *limit* most recent rows for (host, probe), newest first.

    If *since* is given (naive UTC ISO prefix), only rows with ts >= since
    are returned.  If *until* is given, only rows with ts <= until are
    returned.  The string comparison works because timestamps are stored
    as ISO-8601 and sort lexicographically.
    """
    sql = "SELECT ts, data FROM metrics WHERE host=? AND probe=?"
    params: list = [host, probe]
    if since:
        sql += " AND ts >= ?"
        params.append(since)
    if until:
        sql += " AND ts <= ?"
        params.append(until)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with _read() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"ts": row[0], **json.loads(row[1])} for row in rows]


def latest(host: str, probe: str) -> dict | None:
    rows = recent(host, probe, limit=1)
    return rows[0] if rows else None


# ── Hourly rollups ────────────────────────────────────────────────────────────

def _hour_key(ts: str) -> str:
    return ts[:13] + ":00:00"


def _new_bucket() -> dict:
    return {"n": 0, "nfail": 0, "first": None, "last": None, "sums": {}, "fails": []}


def _add_sample(bucket: dict, ts: str, data: dict) -> None:
    bucket["n"] += 1
    if bucket["first"] is None or ts < bucket["first"]:
        bucket["first"] = ts
    if bucket["last"] is None or ts > bucket["last"]:
        bucket["last"] = ts
    if data.get("success") is False:
        bucket["nfail"] += 1
        if len(bucket["fails"]) < _MAX_FAILS_PER_BUCKET:
            bucket["fails"].append(ts)
    for field in ROLLUP_FIELDS:
        value = data.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            acc = bucket["sums"].setdefault(field, [0.0, 0])
            acc[0] += value
            acc[1] += 1


def _merge_bucket(dst: dict, src: dict) -> None:
    dst["n"] += src["n"]
    dst["nfail"] += src["nfail"]
    if dst["first"] is None or src["first"] < dst["first"]:
        dst["first"] = src["first"]
    if dst["last"] is None or src["last"] > dst["last"]:
        dst["last"] = src["last"]
    for field, (total, count) in src["sums"].items():
        acc = dst["sums"].setdefault(field, [0.0, 0])
        acc[0] += total
        acc[1] += count
    room = _MAX_FAILS_PER_BUCKET - len(dst["fails"])
    if room > 0:
        dst["fails"].extend(src["fails"][:room])


def _midpoint(bucket: dict) -> datetime:
    first = datetime.fromisoformat(bucket["first"])
    last = datetime.fromisoformat(bucket["last"])
    return first + (last - first) / 2


def _bucket_row(bucket: dict) -> dict:
    """Chart row for a bucket, shaped like the client-side aggregator's output."""
    row = {
        "ts": _midpoint(bucket).isoformat() + "Z",
        "firstTs": bucket["first"] + "Z",
        "lastTs": bucket["last"] + "Z",
        "success": True,
        "n": bucket["n"],
    }
    for field, (total, count) in bucket["sums"].items():
        if count:
            row[field] = total / count
    return row


def _downsample(items: list[tuple[datetime, dict]], base: datetime,
                until: datetime, max_points: int, min_step_s: int = 1) -> list[dict]:
    """Merge time-ordered (start, bucket) pairs into at most *max_points* buckets."""
    if len(items) <= max_points:
        return [b for _, b in items]
    span_s = max((until - base).total_seconds(), 1)
    step_s = math.ceil(span_s / max_points)
    step_s = max(min_step_s, math.ceil(step_s / min_step_s) * min_step_s)
    merged: dict[int, dict] = {}
    for start, bucket in items:
        idx = int((start - base).total_seconds() // step_s)
        _merge_bucket(merged.setdefault(idx, _new_bucket()), bucket)
    return [merged[i] for i in sorted(merged)]


def rollup_hours(host: str, probe: str, now: datetime | None = None) -> int:
    """Roll closed hours of raw ping/tcp rows into the rollup table.

    Returns the number of hourly buckets written.  Safe to call repeatedly;
    only hours not yet rolled up are processed.
    """
    if probe not in ROLLUP_PROBES:
        return 0
    boundary = (
        (now or datetime.utcnow()) - _ROLLUP_GRACE
    ).replace(minute=0, second=0, microsecond=0).isoformat()
    buckets: dict[str, dict] = {}
    with _read() as conn:
        meta = conn.execute(
            "SELECT upto FROM rollup_meta WHERE host=? AND probe=?", (host, probe)
        ).fetchone()
        if meta:
            start = meta[0]
        else:
            first = conn.execute(
                "SELECT ts FROM metrics WHERE host=? AND probe=? ORDER BY ts LIMIT 1",
                (host, probe),
            ).fetchone()
            if not first:
                return 0
            start = _hour_key(first[0])
        if start >= boundary:
            return 0
        cursor = conn.execute(
            "SELECT ts, data FROM metrics "
            "WHERE host=? AND probe=? AND ts >= ? AND ts < ? ORDER BY ts",
            (host, probe, start, boundary),
        )
        for ts, raw in cursor:
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            _add_sample(buckets.setdefault(_hour_key(ts), _new_bucket()), ts, data)
    rows = [
        (host, probe, key, b["n"], b["nfail"], b["first"], b["last"],
         json.dumps(b["sums"]), json.dumps(b["fails"]))
        for key, b in sorted(buckets.items())
    ]
    with _lock:
        conn = _db()
        conn.executemany(
            "INSERT OR REPLACE INTO rollup "
            "(host, probe, bucket, n, nfail, first_ts, last_ts, sums, fails) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO rollup_meta (host, probe, upto) VALUES (?, ?, ?)",
            (host, probe, boundary),
        )
        conn.commit()
    return len(rows)


def prune_raw(host: str, probe: str, cutoff: str, batch: int = 5000) -> int:
    """Delete raw rows older than *cutoff*, but only ones already rolled up.

    Returns the number of rows deleted.  Deletes in small batches so the
    write lock is never held for long.
    """
    if probe not in ROLLUP_PROBES:
        return 0
    with _read() as conn:
        meta = conn.execute(
            "SELECT upto FROM rollup_meta WHERE host=? AND probe=?", (host, probe)
        ).fetchone()
    if not meta:
        return 0
    limit_ts = min(cutoff, meta[0])
    total = 0
    while True:
        with _lock:
            conn = _db()
            cur = conn.execute(
                "DELETE FROM metrics WHERE id IN ("
                "SELECT id FROM metrics WHERE host=? AND probe=? AND ts < ? LIMIT ?)",
                (host, probe, limit_ts, batch),
            )
            conn.commit()
        total += cur.rowcount
        if cur.rowcount < batch:
            return total
        time.sleep(0.02)


def rolled_series(host: str, probe: str, since: str, until: str,
                  max_points: int = 150) -> tuple[list[dict], list[dict]]:
    """Downsampled history for a wide window.

    Closed hours come from the rollup table; the not-yet-rolled tail (and
    everything, if no rollups exist yet) comes from raw rows.  Returns
    (rows, failures): rows are newest first, at most *max_points* long and
    shaped like raw rows plus ``firstTs``/``lastTs``; failures are
    ``{"ts", "count"}`` entries for failed samples.
    """
    since_dt = datetime.fromisoformat(since)
    until_dt = datetime.fromisoformat(until)
    base = since_dt.replace(minute=0, second=0, microsecond=0)
    buckets: dict[str, dict] = {}
    with _read() as conn:
        meta = conn.execute(
            "SELECT upto FROM rollup_meta WHERE host=? AND probe=?", (host, probe)
        ).fetchone()
        upto = meta[0] if meta else None
        raw_from = since
        if upto and upto > since:
            cursor = conn.execute(
                "SELECT bucket, n, nfail, first_ts, last_ts, sums, fails FROM rollup "
                "WHERE host=? AND probe=? AND bucket >= ? AND bucket < ? ORDER BY bucket",
                (host, probe, base.isoformat(), min(until, upto)),
            )
            for key, n, nfail, first, last, sums, fails in cursor:
                buckets[key] = {
                    "n": n, "nfail": nfail, "first": first, "last": last,
                    "sums": json.loads(sums), "fails": json.loads(fails),
                }
            raw_from = max(since, upto)
        tail = conn.execute(
            "SELECT ts, data FROM metrics "
            "WHERE host=? AND probe=? AND ts >= ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT ?",
            (host, probe, raw_from, until, _RAW_TAIL_LIMIT),
        ).fetchall()
    for ts, raw in tail:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        _add_sample(buckets.setdefault(_hour_key(ts), _new_bucket()), ts, data)

    hourly = sorted(buckets.items())
    failures: list[dict] = []
    for _, b in reversed(hourly):
        if not b["nfail"]:
            continue
        failures.extend({"ts": ts, "count": 1} for ts in reversed(b["fails"]))
        extra = b["nfail"] - len(b["fails"])
        if extra > 0:
            failures.append({"ts": _midpoint(b).isoformat(), "count": extra})
    items = [(datetime.fromisoformat(key), b) for key, b in hourly]
    merged = _downsample(items, base, until_dt, max_points, min_step_s=3600)
    if len(failures) > _MAX_FAILURE_MARKERS:
        # Too many to draw individually: one marker per output bucket instead.
        failures = [
            {"ts": _midpoint(b).isoformat(), "count": b["nfail"]}
            for b in reversed(merged) if b["nfail"]
        ]
    return [_bucket_row(b) for b in reversed(merged)], failures


def downsample_rows(rows: list[dict], since: str, until: str,
                    max_points: int = 150) -> list[dict]:
    """Bucket newest-first raw rows into at most *max_points* rows.

    Rows are returned untouched when there are already few enough.
    """
    if len(rows) <= max_points:
        return rows
    base = datetime.fromisoformat(since)
    items = []
    for row in sorted(rows, key=lambda r: r["ts"]):
        bucket = _new_bucket()
        _add_sample(bucket, row["ts"], row)
        items.append((datetime.fromisoformat(row["ts"]), bucket))
    merged = _downsample(items, base, datetime.fromisoformat(until), max_points)
    return [_bucket_row(b) for b in reversed(merged)]
