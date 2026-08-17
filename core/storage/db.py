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

CREATE TABLE IF NOT EXISTS target (
    id TEXT PRIMARY KEY,
    metric_type TEXT NOT NULL,
    target_value REAL NOT NULL,
    operator TEXT NOT NULL CHECK(operator IN ('gte', 'lte', 'eq')),
    target_window TEXT NOT NULL CHECK(target_window IN ('daily', 'weekly_sum', 'weekly_avg', 'by_date')),
    start_date TEXT NOT NULL,
    end_date TEXT,
    status TEXT NOT NULL CHECK(status IN ('active', 'completed', 'abandoned')),
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_status ON target(status);
CREATE INDEX IF NOT EXISTS idx_target_metric ON target(metric_type);

CREATE TABLE IF NOT EXISTS training_plan (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal_description TEXT,
    start_date TEXT NOT NULL,
    target_date TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'paused', 'completed', 'archived')),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plan_status ON training_plan(status);

CREATE TABLE IF NOT EXISTS coach_note (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('injury', 'nutrition', 'feeling', 'gear', 'milestone', 'general')),
    note TEXT NOT NULL,
    tags_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coach_note_date ON coach_note(date);

CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    activity_id TEXT NOT NULL,
    activity_name TEXT,
    activity_type TEXT NOT NULL,
    sport_type TEXT,
    start_time TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    distance_meters REAL,
    calories REAL,
    avg_hr REAL,
    max_hr REAL,
    avg_speed REAL,
    max_speed REAL,
    elevation_gain REAL,
    elevation_loss REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_start_time ON activity(start_time);
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity(activity_type);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

