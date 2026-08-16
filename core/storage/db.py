import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_reading (
    source TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    PRIMARY KEY (source, metric_type, timestamp)
);

CREATE TABLE IF NOT EXISTS sync_checkpoint (
    source TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    last_synced_date TEXT NOT NULL,
    PRIMARY KEY (source, metric_type)
);

CREATE TABLE IF NOT EXISTS report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
