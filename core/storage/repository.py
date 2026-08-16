import sqlite3
from datetime import date, datetime

from core.storage.models import MetricReading


def upsert_readings(conn: sqlite3.Connection, readings: list[MetricReading]) -> int:
    conn.executemany(
        """
        INSERT INTO metric_reading (source, metric_type, timestamp, value, unit)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source, metric_type, timestamp) DO UPDATE SET
            value = excluded.value,
            unit = excluded.unit
        """,
        [
            (r.source, r.metric_type, r.timestamp.isoformat(), r.value, r.unit)
            for r in readings
        ],
    )
    conn.commit()
    return len(readings)


def get_readings(conn: sqlite3.Connection, metric_type: str, start: date, end: date) -> list[MetricReading]:
    rows = conn.execute(
        """
        SELECT source, metric_type, timestamp, value, unit
        FROM metric_reading
        WHERE metric_type = ? AND date(timestamp) BETWEEN date(?) AND date(?)
        ORDER BY timestamp ASC
        """,
        (metric_type, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        MetricReading(
            source=row[0],
            metric_type=row[1],
            timestamp=datetime.fromisoformat(row[2]),
            value=row[3],
            unit=row[4],
        )
        for row in rows
    ]


def get_checkpoint(conn: sqlite3.Connection, source: str, metric_type: str) -> date | None:
    row = conn.execute(
        "SELECT last_synced_date FROM sync_checkpoint WHERE source = ? AND metric_type = ?",
        (source, metric_type),
    ).fetchone()
    return date.fromisoformat(row[0]) if row else None


def set_checkpoint(conn: sqlite3.Connection, source: str, metric_type: str, last_synced_date: date) -> None:
    conn.execute(
        """
        INSERT INTO sync_checkpoint (source, metric_type, last_synced_date)
        VALUES (?, ?, ?)
        ON CONFLICT(source, metric_type) DO UPDATE SET last_synced_date = excluded.last_synced_date
        """,
        (source, metric_type, last_synced_date.isoformat()),
    )
    conn.commit()
