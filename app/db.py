import sqlite3

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS admin_user (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    token TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_run_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_run_at TEXT NOT NULL,
    auth_error TEXT
);

CREATE TABLE IF NOT EXISTS sync_metric_status (
    metric_type TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_app_schema(conn: sqlite3.Connection) -> None:
    """Add this app's tables to a connection already opened by
    core.storage.db.connect() -- same SQLite file as core's metric_reading/
    sync_checkpoint tables, never a second database (design doc Deployment
    section: "SQLite on a mounted volume", singular). Idempotent.
    """
    conn.executescript(SCHEMA)
    conn.commit()
