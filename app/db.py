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
    source TEXT PRIMARY KEY,
    last_run_at TEXT NOT NULL,
    auth_error TEXT
);

CREATE TABLE IF NOT EXISTS sync_metric_status (
    source TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, metric_type)
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
    _migrate_sync_status_tables(conn)


def _migrate_sync_status_tables(conn: sqlite3.Connection) -> None:
    """One-time migration for databases created before sync_run_status/
    sync_metric_status became source-keyed. Both tables hold only
    re-derivable sync status (never user data), so a drop-and-recreate is
    safe -- the next sync pass repopulates them."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(sync_run_status)").fetchall()]
    if cols and "source" not in cols:
        conn.execute("DROP TABLE sync_run_status")
        conn.execute("DROP TABLE sync_metric_status")
        conn.executescript(SCHEMA)
        conn.commit()
