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

CREATE TABLE IF NOT EXISTS metric_source_priority (
    metric_type TEXT PRIMARY KEY,
    preferred_source TEXT NOT NULL CHECK (preferred_source IN ('garmin', 'apple_health'))
);

CREATE TABLE IF NOT EXISTS strength_set (
    id TEXT PRIMARY KEY,              -- f"{source}:{activity_id}:{set_index}"
    activity_id TEXT NOT NULL,        -- FK-by-convention to activity.id (f"{source}:{activity_id}")
    movement_id TEXT NOT NULL,
    movement_name TEXT,
    set_index INTEGER NOT NULL,       -- ordering within the workout
    is_warm_up INTEGER NOT NULL DEFAULT 0,
    reps INTEGER,
    weight_lbs REAL,
    volume_lbs REAL,
    one_rep_max REAL,
    max_power_watts REAL,
    rom_inches REAL,
    struggling_score REAL,
    side TEXT,                        -- 'Left' | 'Right' | 'Both'
    created_at TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT ''  -- real workout/set timestamp; created_at is write-time bookkeeping only
);

CREATE INDEX IF NOT EXISTS idx_strength_set_activity ON strength_set(activity_id);
CREATE INDEX IF NOT EXISTS idx_strength_set_movement ON strength_set(movement_id);

CREATE TABLE IF NOT EXISTS strength_set_muscle_group (
    strength_set_id TEXT NOT NULL REFERENCES strength_set(id),
    muscle_group TEXT NOT NULL,
    PRIMARY KEY (strength_set_id, muscle_group)
);

CREATE INDEX IF NOT EXISTS idx_ssmg_muscle_group ON strength_set_muscle_group(muscle_group);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent migrations for columns added after a table's
    initial CREATE TABLE IF NOT EXISTS -- that statement no-ops on a table
    that already exists, so a newly added column needs an explicit ALTER
    TABLE here instead. Safe to run on every connect(); checks
    PRAGMA table_info before altering so it's a no-op once applied."""
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(strength_set)").fetchall()}
    if "occurred_at" not in existing_columns:
        conn.execute("ALTER TABLE strength_set ADD COLUMN occurred_at TEXT NOT NULL DEFAULT ''")
        # Best-effort backfill for rows written before this column existed:
        # fall back to the parent activity's start_time (workout-level, not
        # true per-set time, but far better than an empty string). A legacy
        # row whose activity_id has no matching activity row (an orphan --
        # activity rows only exist for date ranges a sync actually covered)
        # falls back further to the row's own created_at bookkeeping
        # timestamp, so no row is ever left with an unparseable empty string
        # (datetime.fromisoformat('') raises in _row_to_strength_set).
        conn.execute(
            """
            UPDATE strength_set
            SET occurred_at = COALESCE(
                (SELECT activity.start_time FROM activity WHERE activity.id = strength_set.activity_id),
                created_at
            )
            WHERE occurred_at = ''
            """
        )
    # Create the index unconditionally; CREATE INDEX IF NOT EXISTS is idempotent
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strength_set_movement_occurred ON strength_set(movement_id, occurred_at)")

