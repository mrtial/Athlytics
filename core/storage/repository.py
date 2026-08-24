import sqlite3
from datetime import date, datetime, timedelta, timezone

from core.storage.models import Activity, CoachNote, MetricReading, MetricSummary, Report, Target, TrainingPlan


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


DEFAULT_SOURCE_PRIORITY: list[str] = ["garmin", "apple_health"]


def get_readings(conn: sqlite3.Connection, metric_type: str, start: date, end: date) -> list[MetricReading]:
    override_row = conn.execute(
        "SELECT preferred_source FROM metric_source_priority WHERE metric_type = ?",
        (metric_type,),
    ).fetchone()
    preferred_source = override_row[0] if override_row else None

    # Rank each row's source: the override (if any) ranks first, then
    # DEFAULT_SOURCE_PRIORITY in order, then anything else last. Expressed
    # as a SQL CASE so reconciliation happens in one query rather than
    # post-filtering in Python.
    priority_order = [preferred_source] if preferred_source else []
    priority_order += [s for s in DEFAULT_SOURCE_PRIORITY if s != preferred_source]

    case_clauses = " ".join(
        f"WHEN source = ? THEN {rank}" for rank, _ in enumerate(priority_order)
    )
    case_params = list(priority_order)
    fallback_rank = len(priority_order)
    rank_expr = f"(CASE {case_clauses} ELSE {fallback_rank} END)"

    # Rank each row by source priority, then find the best (lowest) rank
    # present per day via a window MIN(). Keep every row matching that best
    # rank rather than just one arbitrary row per day -- this drops rows
    # from a lower-priority source when a higher-priority source also has
    # data that day, while leaving multiple same-source, same-day readings
    # (e.g. several intraday syncs) untouched.
    rows = conn.execute(
        f"""
        SELECT source, metric_type, timestamp, value, unit
        FROM (
            SELECT *,
                {rank_expr} AS rank,
                MIN({rank_expr}) OVER (
                    PARTITION BY metric_type, date(timestamp)
                ) AS best_rank
            FROM metric_reading
            WHERE metric_type = ? AND date(timestamp) BETWEEN date(?) AND date(?)
        )
        WHERE rank = best_rank
        ORDER BY timestamp ASC
        """,
        (*case_params, *case_params, metric_type, start.isoformat(), end.isoformat()),
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


def has_synced_data(conn: sqlite3.Connection, source: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sync_checkpoint WHERE source = ? LIMIT 1",
        (source,),
    ).fetchone()
    return row is not None


def has_activities_from_source(conn: sqlite3.Connection, source: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM activity WHERE source = ? LIMIT 1",
        (source,),
    ).fetchone()
    return row is not None


def get_source_priority(conn: sqlite3.Connection, metric_type: str) -> str | None:
    row = conn.execute(
        "SELECT preferred_source FROM metric_source_priority WHERE metric_type = ?",
        (metric_type,),
    ).fetchone()
    return row[0] if row else None


def set_source_priority(conn: sqlite3.Connection, metric_type: str, source: str) -> None:
    if source not in DEFAULT_SOURCE_PRIORITY:
        raise ValueError(f"unknown source: {source!r}")
    conn.execute(
        """
        INSERT INTO metric_source_priority (metric_type, preferred_source)
        VALUES (?, ?)
        ON CONFLICT(metric_type) DO UPDATE SET preferred_source = excluded.preferred_source
        """,
        (metric_type, source),
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


def save_target(conn: sqlite3.Connection, target: Target) -> Target:
    conn.execute(
        """
        INSERT INTO target (id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            metric_type = excluded.metric_type,
            target_value = excluded.target_value,
            operator = excluded.operator,
            target_window = excluded.target_window,
            start_date = excluded.start_date,
            end_date = excluded.end_date,
            status = excluded.status,
            notes = excluded.notes
        """,
        (
            target.id,
            target.metric_type,
            target.target_value,
            target.operator,
            target.target_window,
            target.start_date.isoformat(),
            target.end_date.isoformat() if target.end_date else None,
            target.status,
            target.notes,
            target.created_at.isoformat(),
        ),
    )
    conn.commit()
    return target


def get_targets(conn: sqlite3.Connection, status: str | None = None) -> list[Target]:
    if status is not None:
        rows = conn.execute(
            """
            SELECT id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at
            FROM target
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at
            FROM target
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        Target(
            id=row[0],
            metric_type=row[1],
            target_value=row[2],
            operator=row[3],
            target_window=row[4],
            start_date=date.fromisoformat(row[5]),
            end_date=date.fromisoformat(row[6]) if row[6] else None,
            status=row[7],
            notes=row[8],
            created_at=datetime.fromisoformat(row[9]),
        )
        for row in rows
    ]


def get_target_by_id(conn: sqlite3.Connection, target_id: str) -> Target | None:
    row = conn.execute(
        """
        SELECT id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at
        FROM target
        WHERE id = ?
        """,
        (target_id,),
    ).fetchone()
    if row is None:
        return None
    return Target(
        id=row[0],
        metric_type=row[1],
        target_value=row[2],
        operator=row[3],
        target_window=row[4],
        start_date=date.fromisoformat(row[5]),
        end_date=date.fromisoformat(row[6]) if row[6] else None,
        status=row[7],
        notes=row[8],
        created_at=datetime.fromisoformat(row[9]),
    )


def delete_target(conn: sqlite3.Connection, target_id: str) -> bool:
    cursor = conn.execute("DELETE FROM target WHERE id = ?", (target_id,))
    conn.commit()
    return cursor.rowcount > 0


def save_training_plan(conn: sqlite3.Connection, plan: TrainingPlan) -> TrainingPlan:
    conn.execute(
        """
        INSERT INTO training_plan (id, title, goal_description, start_date, target_date, plan_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            goal_description = excluded.goal_description,
            start_date = excluded.start_date,
            target_date = excluded.target_date,
            plan_json = excluded.plan_json,
            status = excluded.status
        """,
        (
            plan.id,
            plan.title,
            plan.goal_description,
            plan.start_date.isoformat(),
            plan.target_date.isoformat(),
            plan.plan_json,
            plan.status,
            plan.created_at.isoformat(),
        ),
    )
    conn.commit()
    return plan


def get_training_plans(conn: sqlite3.Connection, status: str | None = None) -> list[TrainingPlan]:
    if status is not None:
        rows = conn.execute(
            """
            SELECT id, title, goal_description, start_date, target_date, plan_json, status, created_at
            FROM training_plan
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, title, goal_description, start_date, target_date, plan_json, status, created_at
            FROM training_plan
            ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, created_at DESC
            """
        ).fetchall()
    return [
        TrainingPlan(
            id=row[0],
            title=row[1],
            goal_description=row[2],
            start_date=date.fromisoformat(row[3]),
            target_date=date.fromisoformat(row[4]),
            plan_json=row[5],
            status=row[6],
            created_at=datetime.fromisoformat(row[7]),
        )
        for row in rows
    ]


def get_training_plan_by_id(conn: sqlite3.Connection, plan_id: str) -> TrainingPlan | None:
    row = conn.execute(
        """
        SELECT id, title, goal_description, start_date, target_date, plan_json, status, created_at
        FROM training_plan
        WHERE id = ?
        """,
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    return TrainingPlan(
        id=row[0],
        title=row[1],
        goal_description=row[2],
        start_date=date.fromisoformat(row[3]),
        target_date=date.fromisoformat(row[4]),
        plan_json=row[5],
        status=row[6],
        created_at=datetime.fromisoformat(row[7]),
    )


def update_plan_status(conn: sqlite3.Connection, plan_id: str, status: str) -> TrainingPlan | None:
    cursor = conn.execute(
        "UPDATE training_plan SET status = ? WHERE id = ?",
        (status, plan_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return get_training_plan_by_id(conn, plan_id)


def delete_training_plan(conn: sqlite3.Connection, plan_id: str) -> bool:
    cursor = conn.execute("DELETE FROM training_plan WHERE id = ?", (plan_id,))
    conn.commit()
    return cursor.rowcount > 0


def save_coach_note(conn: sqlite3.Connection, note: CoachNote) -> CoachNote:
    conn.execute(
        """
        INSERT INTO coach_note (id, date, category, note, tags_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            date = excluded.date,
            category = excluded.category,
            note = excluded.note,
            tags_json = excluded.tags_json
        """,
        (
            note.id,
            note.date.isoformat(),
            note.category,
            note.note,
            note.tags_json,
            note.created_at.isoformat(),
        ),
    )
    conn.commit()
    return note


def get_coach_notes(conn: sqlite3.Connection, limit: int = 10, category: str | None = None) -> list[CoachNote]:
    if category is not None:
        rows = conn.execute(
            """
            SELECT id, date, category, note, tags_json, created_at
            FROM coach_note
            WHERE category = ?
            ORDER BY date DESC, created_at DESC
            LIMIT ?
            """,
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, date, category, note, tags_json, created_at
            FROM coach_note
            ORDER BY date DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        CoachNote(
            id=row[0],
            date=date.fromisoformat(row[1]),
            category=row[2],
            note=row[3],
            tags_json=row[4],
            created_at=datetime.fromisoformat(row[5]),
        )
        for row in rows
    ]


ACTIVITY_SOURCE_PRIORITY: list[str] = ["garmin", "strava"]
ACTIVITY_DEDUP_WINDOW_MINUTES = 5


def _activity_source_rank(source: str) -> int:
    try:
        return ACTIVITY_SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(ACTIVITY_SOURCE_PRIORITY)


def upsert_activities(conn: sqlite3.Connection, activities: list[Activity]) -> int:
    """Insert/update activities, applying symmetric cross-source dedup:
    a same-activity_type, close-in-time Activity from a *different* source
    is treated as the same real-world workout. The higher-priority source
    (ACTIVITY_SOURCE_PRIORITY) always wins, regardless of which provider's
    sync ran first -- see docs/superpowers/specs/strava_provider.md §6."""
    inserted = 0
    for activity in activities:
        window_start = (activity.start_time - timedelta(minutes=ACTIVITY_DEDUP_WINDOW_MINUTES)).isoformat()
        window_end = (activity.start_time + timedelta(minutes=ACTIVITY_DEDUP_WINDOW_MINUTES)).isoformat()
        existing_rows = conn.execute(
            """
            SELECT id, source FROM activity
            WHERE source != ? AND activity_type = ? AND start_time BETWEEN ? AND ?
            """,
            (activity.source, activity.activity_type, window_start, window_end),
        ).fetchall()

        incoming_rank = _activity_source_rank(activity.source)
        skip_incoming = False
        for existing_id, existing_source in existing_rows:
            if _activity_source_rank(existing_source) <= incoming_rank:
                skip_incoming = True
            else:
                conn.execute("DELETE FROM activity WHERE id = ?", (existing_id,))

        if skip_incoming:
            continue

        conn.execute(
            """
            INSERT INTO activity (
                id, source, activity_id, activity_name, activity_type, sport_type,
                start_time, duration_seconds, distance_meters, calories,
                avg_hr, max_hr, avg_speed, max_speed, elevation_gain, elevation_loss, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                activity_name = excluded.activity_name,
                activity_type = excluded.activity_type,
                sport_type = excluded.sport_type,
                start_time = excluded.start_time,
                duration_seconds = excluded.duration_seconds,
                distance_meters = excluded.distance_meters,
                calories = excluded.calories,
                avg_hr = excluded.avg_hr,
                max_hr = excluded.max_hr,
                avg_speed = excluded.avg_speed,
                max_speed = excluded.max_speed,
                elevation_gain = excluded.elevation_gain,
                elevation_loss = excluded.elevation_loss
            """,
            (
                activity.id,
                activity.source,
                activity.activity_id,
                activity.activity_name,
                activity.activity_type,
                activity.sport_type,
                activity.start_time.isoformat(),
                activity.duration_seconds,
                activity.distance_meters,
                activity.calories,
                activity.avg_hr,
                activity.max_hr,
                activity.avg_speed,
                activity.max_speed,
                activity.elevation_gain,
                activity.elevation_loss,
                activity.created_at.isoformat(),
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


def get_activities(
    conn: sqlite3.Connection,
    start_date: date | None = None,
    end_date: date | None = None,
    activity_type: str | None = None,
    limit: int = 50,
) -> list[Activity]:
    query = """
        SELECT id, source, activity_id, activity_name, activity_type, sport_type,
               start_time, duration_seconds, distance_meters, calories,
               avg_hr, max_hr, avg_speed, max_speed, elevation_gain, elevation_loss, created_at
        FROM activity
        WHERE 1=1
    """
    params: list = []
    if start_date is not None:
        query += " AND date(start_time) >= date(?)"
        params.append(start_date.isoformat())
    if end_date is not None:
        query += " AND date(start_time) <= date(?)"
        params.append(end_date.isoformat())
    if activity_type is not None:
        query += " AND (activity_type = ? OR sport_type = ?)"
        params.extend([activity_type, activity_type])
    query += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [
        Activity(
            id=row[0],
            source=row[1],
            activity_id=row[2],
            activity_name=row[3],
            activity_type=row[4],
            sport_type=row[5],
            start_time=datetime.fromisoformat(row[6]),
            duration_seconds=row[7],
            distance_meters=row[8],
            calories=row[9],
            avg_hr=row[10],
            max_hr=row[11],
            avg_speed=row[12],
            max_speed=row[13],
            elevation_gain=row[14],
            elevation_loss=row[15],
            created_at=datetime.fromisoformat(row[16]),
        )
        for row in rows
    ]


def get_activity_by_id(conn: sqlite3.Connection, activity_id: str) -> Activity | None:
    row = conn.execute(
        """
        SELECT id, source, activity_id, activity_name, activity_type, sport_type,
               start_time, duration_seconds, distance_meters, calories,
               avg_hr, max_hr, avg_speed, max_speed, elevation_gain, elevation_loss, created_at
        FROM activity
        WHERE id = ? OR activity_id = ?
        """,
        (activity_id, activity_id),
    ).fetchone()
    if row is None:
        return None
    return Activity(
        id=row[0],
        source=row[1],
        activity_id=row[2],
        activity_name=row[3],
        activity_type=row[4],
        sport_type=row[5],
        start_time=datetime.fromisoformat(row[6]),
        duration_seconds=row[7],
        distance_meters=row[8],
        calories=row[9],
        avg_hr=row[10],
        max_hr=row[11],
        avg_speed=row[12],
        max_speed=row[13],
        elevation_gain=row[14],
        elevation_loss=row[15],
        created_at=datetime.fromisoformat(row[16]),
    )

