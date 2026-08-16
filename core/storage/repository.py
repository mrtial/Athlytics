import sqlite3
from datetime import date, datetime, timezone

from core.storage.models import MetricReading, MetricSummary, Report


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


def list_metric_summaries(conn: sqlite3.Connection) -> list[MetricSummary]:
    rows = conn.execute(
        """
        SELECT metric_type, MIN(date(timestamp)), MAX(date(timestamp)), COUNT(*), unit
        FROM metric_reading
        GROUP BY metric_type
        ORDER BY metric_type ASC
        """
    ).fetchall()
    return [
        MetricSummary(
            metric_type=row[0],
            earliest_date=date.fromisoformat(row[1]),
            latest_date=date.fromisoformat(row[2]),
            reading_count=row[3],
            unit=row[4],
        )
        for row in rows
    ]


def save_report(
    conn: sqlite3.Connection, title: str, content: str, created_at: datetime | None = None
) -> int:
    created_at = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
    cursor = conn.execute(
        "INSERT INTO report (created_at, title, content) VALUES (?, ?, ?)",
        (created_at.isoformat(), title, content),
    )
    conn.commit()
    return cursor.lastrowid


def get_report(conn: sqlite3.Connection, report_id: int) -> Report | None:
    row = conn.execute(
        "SELECT id, created_at, title, content FROM report WHERE id = ?",
        (report_id,),
    ).fetchone()
    if row is None:
        return None
    return Report(
        id=row[0],
        created_at=datetime.fromisoformat(row[1]),
        title=row[2],
        content=row[3],
    )
