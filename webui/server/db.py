"""SQLite connection + schema for the inventory/history/analytics backend.

WAL mode, stdlib sqlite3 only — no ORM, no separate DB server process,
per the plan's "fastest/lightest option for this hardware" decision.
"""

import sqlite3
import threading

from config import DB_PATH

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS inventory_items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    category             TEXT NOT NULL DEFAULT 'Other',
    quantity             REAL NOT NULL DEFAULT 0,
    unit                 TEXT NOT NULL DEFAULT 'pcs',
    low_stock_threshold  REAL NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
    snapshot_date TEXT NOT NULL,
    quantity      REAL NOT NULL,
    UNIQUE(item_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON inventory_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS history_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    type      TEXT NOT NULL CHECK (type IN ('detection','inventory_change','recipe_cooked','system')),
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    summary   TEXT NOT NULL,
    payload   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_history_type ON history_events(type);
CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history_events(timestamp);

CREATE TABLE IF NOT EXISTS inference_frames (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    num_detections  INTEGER NOT NULL DEFAULT 0,
    avg_confidence  REAL,
    inference_ms    REAL,
    backend         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frames_session ON inference_frames(session_id);
CREATE INDEX IF NOT EXISTS idx_frames_timestamp ON inference_frames(timestamp);

CREATE TABLE IF NOT EXISTS recipe_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    inventory_snapshot  TEXT NOT NULL,
    recipes_json        TEXT NOT NULL
);
"""

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """One connection per thread (sqlite3 connections aren't thread-safe to share)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


def log_event(type_: str, summary: str, payload: dict) -> None:
    import json as _json
    conn = get_conn()
    conn.execute(
        "INSERT INTO history_events (type, summary, payload) VALUES (?, ?, ?)",
        (type_, summary, _json.dumps(payload)),
    )
    conn.commit()


def record_snapshot(conn: sqlite3.Connection, item_name: str) -> None:
    """Upsert today's quantity for *item_name* — powers the 7-day stock trend."""
    from datetime import date
    row = conn.execute(
        "SELECT id, quantity FROM inventory_items WHERE name = ?", (item_name,)
    ).fetchone()
    if row is None:
        return
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO inventory_snapshots (item_id, snapshot_date, quantity) VALUES (?, ?, ?) "
        "ON CONFLICT(item_id, snapshot_date) DO UPDATE SET quantity = excluded.quantity",
        (row["id"], today, row["quantity"]),
    )
