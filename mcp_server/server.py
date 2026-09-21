"""Athlytics AI Coach & Actionable MCP Server.

Provides bidirectional tools (reading metrics/trends, writing targets and plans),
living dynamic context resources (athlytics://), and evidence-based workflow prompts.
"""
import copy
import dataclasses
import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mcp.server import MCPServer

from app.db import ensure_app_schema
from core.analytics import Anomaly, Trend, detect_anomalies_for_metrics, get_trend as analytics_get_trend
from core.storage import repository
from core.storage.db import connect
from core.storage.models import Activity, CoachNote, MetricReading, MetricSummary, Report, Target, TrainingPlan
from mcp_server.prompts import (
    prompt_build_tonal_program,
    prompt_build_training_plan,
    prompt_readiness_check,
    prompt_weekly_review,
)
from mcp_server.resources import (
    build_athlete_snapshot,
    build_coach_context,
    build_coach_playbook,
    build_training_current_state,
)

DB_PATH_ENV_VAR = "ATHLYTICS_DB_PATH"
DEFAULT_DB_PATH = Path.home() / ".athlytics" / "athlytics.db"
SYNC_RESYNC_GRACE_DAYS = 3  # re-walk the trailing 3 days on every sync so a
                            # provider value that arrives a day or two late
                            # (e.g. Garmin's resting_hr lag) still gets
                            # picked up instead of being skipped forever --
                            # see sync_all_metrics's resync_grace_days.

logger = logging.getLogger(__name__)

mcp = MCPServer("Athlytics")


def _db_path() -> Path:
    return Path(os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH)))


@contextmanager
def _connection():
    conn = connect(_db_path())
    try:
        # Same SQLite file the FastAPI app uses (design doc: one database,
        # never two) -- this only adds app_setting/admin_user/session/etc.
        # if they don't already exist (ensure_app_schema is idempotent), so
        # it's safe even though the web app usually creates them first.
        # athlete_snapshot needs app_setting (athlete name/DOB) now, which
        # core.storage.db.connect() alone doesn't provide.
        ensure_app_schema(conn)
        yield conn
    finally:
        conn.close()


def _with_utc_tzinfo(obj):
    """Attach UTC tzinfo to a dataclass instance's naive datetime fields.

    Storage/repository code intentionally keeps datetimes naive (see the
    timezone contract in core/storage/models.py) so SQLite's date() and
    Python's .date() agree on calendar-day boundaries. MCP clients validate
    `datetime`-typed fields against strict RFC 3339, which requires a UTC
    offset -- naive `isoformat()` output lacks one and fails validation. This
    attaches the (already-UTC) offset only at the outward-facing tool
    boundary, leaving the naive values used internally untouched.

    Some models (MetricReading, Activity) enforce naive-only timestamps in
    `__post_init__`, so the copy is built via copy.copy + object.__setattr__
    rather than dataclasses.replace, which would re-run that constructor
    validation against the very tzinfo it's designed to reject.
    """
    updates = {
        f.name: value.replace(tzinfo=timezone.utc)
        for f in dataclasses.fields(obj)
        if isinstance(value := getattr(obj, f.name), datetime) and value.tzinfo is None
    }
    if not updates:
        return obj
    new_obj = copy.copy(obj)
    for name, value in updates.items():
        object.__setattr__(new_obj, name, value)
    return new_obj


# ---------------------------------------------------------------------------
# Read Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_metrics() -> list[MetricSummary]:
    """List every metric_type with stored data, available date range, reading count, and unit."""
    with _connection() as conn:
        return repository.list_metric_summaries(conn)


@mcp.tool()
def get_metric_series(metric_type: str, start: str, end: str) -> list[MetricReading]:
    """Fetch raw daily readings for a metric across an ISO-8601 date range (e.g. start='2026-01-01', end='2026-01-31')."""
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    with _connection() as conn:
        return [_with_utc_tzinfo(r) for r in repository.get_readings(conn, metric_type, start_date, end_date)]


@mcp.tool()
def get_trend(metric_type: str, window: int = 30) -> Trend:
    """Fetch rolling average, sample count, and period-over-period delta for a metric over trailing window days."""
    with _connection() as conn:
        return analytics_get_trend(conn, metric_type, window_days=window)


@mcp.tool()
def get_anomalies(since: str | None = None) -> list[Anomaly]:
    """Fetch statistical anomaly flags (>2 standard deviations) across all stored metrics on/after optional since date."""
    since_date = date.fromisoformat(since) if since is not None else None
    with _connection() as conn:
        metric_types = [s.metric_type for s in repository.list_metric_summaries(conn)]
        return [_with_utc_tzinfo(a) for a in detect_anomalies_for_metrics(conn, metric_types, since=since_date)]


@mcp.tool()
def get_report(id: int) -> Report:
    """Fetch a previously generated stored report by integer id."""
    with _connection() as conn:
        report = repository.get_report(conn, id)
        if report is None:
            raise ValueError(f"no report found with id={id}")
        return _with_utc_tzinfo(report)


@mcp.tool()
def get_targets(status: str = "active") -> list[Target]:
    """Fetch active or historical athlete targets (status: 'active', 'completed', 'abandoned')."""
    with _connection() as conn:
        return [_with_utc_tzinfo(t) for t in repository.get_targets(conn, status=status)]


@mcp.tool()
def get_training_plans(status: str = "active") -> list[TrainingPlan]:
    """Fetch structured training plans (status: 'active', 'paused', 'completed', 'archived')."""
    with _connection() as conn:
        return [_with_utc_tzinfo(p) for p in repository.get_training_plans(conn, status=status)]


@mcp.tool()
def get_coach_notes(limit: int = 10, category: str | None = None) -> list[CoachNote]:
    """Fetch recent qualitative coach notes, injury logs, or athlete feedback."""
    with _connection() as conn:
        return [_with_utc_tzinfo(n) for n in repository.get_coach_notes(conn, limit=limit, category=category)]


@mcp.tool()
def get_activities(
    start_date: str | None = None,
    end_date: str | None = None,
    activity_type: str | None = None,
    limit: int = 20,
) -> list[Activity]:
    """Fetch structured workout activity sessions (running, cycling, swimming, strength, etc.) with duration, distance, pace/speed, and HR."""
    s_date = date.fromisoformat(start_date) if start_date else None
    e_date = date.fromisoformat(end_date) if end_date else None
    with _connection() as conn:
        activities = repository.get_activities(
            conn, start_date=s_date, end_date=e_date, activity_type=activity_type, limit=limit
        )
        return [_with_utc_tzinfo(a) for a in activities]


@mcp.tool()
def get_sleep_detail(date: str) -> dict:
    """Full per-night sleep detail for one date (stage breakdown, timing, sub-score qualifiers, stage timeline, restlessness) -- entirely from locally hydrated Garmin data, no live API call. Only dates covered by sync_garmin_data's hydration (or refetch_garmin_sleep_detail_range) are resolvable; an un-hydrated date returns {"status": "not_hydrated", "date": ...} rather than raising."""
    session_id = f"garmin:{date}"
    with _connection() as conn:
        session = repository.get_sleep_session(conn, session_id)
        if session is None:
            return {"status": "not_hydrated", "date": date}

        segments = repository.get_sleep_stage_segments(conn, session_id)
        moments = repository.get_sleep_restless_moments(conn, session_id)

    def _local_time(dt):
        return dt.strftime("%H:%M") if dt else None

    # sleep_stage_segment/sleep_restless_moment only ever store UTC
    # timestamps (that's the correct storage contract -- see
    # core/providers/garmin.py) -- but this tool's response is for
    # human/coach reading (docs/superpowers/specs/2026-09-20-garmin-sleep-detail-design.md
    # Section 5), so stage_timeline/restless_moments must report local wall-clock
    # times, not UTC, matching bedtime_local/wake_time_local above. Derive
    # the per-night UTC-to-local offset from the one pair of timestamps
    # SleepSession stores in both frames for the same instant, since the
    # DST/timezone offset itself isn't stored anywhere.
    if session.sleep_start_local is not None and session.sleep_start_utc is not None:
        local_offset = session.sleep_start_local - session.sleep_start_utc
    else:
        local_offset = timedelta(0)

    return {
        "date": date,
        "bedtime_local": _local_time(session.sleep_start_local),
        "wake_time_local": _local_time(session.sleep_end_local),
        "total_sleep_hours": round(session.total_sleep_seconds / 3600.0, 2) if session.total_sleep_seconds is not None else None,
        "nap_time_minutes": round(session.nap_time_seconds / 60.0, 1) if session.nap_time_seconds is not None else None,
        "stage_seconds": {
            "deep": session.deep_sleep_seconds, "light": session.light_sleep_seconds,
            "rem": session.rem_sleep_seconds, "awake": session.awake_sleep_seconds,
        },
        "stage_percentage": {
            "deep": session.deep_percentage, "light": session.light_percentage, "rem": session.rem_percentage,
        },
        "overall_score": session.overall_score, "overall_score_qualifier": session.overall_score_qualifier,
        "duration_qualifier": session.duration_qualifier, "stress_qualifier": session.stress_qualifier,
        "awake_count_qualifier": session.awake_count_qualifier,
        "restlessness_qualifier": session.restlessness_qualifier,
        "avg_sleep_stress": session.avg_sleep_stress, "avg_heart_rate": session.avg_heart_rate,
        "avg_overnight_hrv": session.avg_overnight_hrv, "avg_respiration": session.avg_respiration,
        "awake_count": session.awake_count, "restless_moments_count": session.restless_moments_count,
        "sleep_need_target_hours": round(session.sleep_need_baseline_minutes / 60.0, 2) if session.sleep_need_baseline_minutes is not None else None,
        "sleep_need_feedback": session.sleep_need_feedback,
        "score_feedback": session.score_feedback,
        "score_personalized_insight": session.score_personalized_insight,
        "stage_timeline": [
            {
                "stage": s.stage,
                "start_local": (s.start_utc + local_offset).isoformat(),
                "end_local": (s.end_utc + local_offset).isoformat(),
                "duration_minutes": round(s.duration_seconds / 60.0, 1),
            }
            for s in segments
        ],
        "restless_moments": [
            {"time_local": (m.occurred_at_utc + local_offset).isoformat(), "value": m.value} for m in moments
        ],
    }


def _median_clock_time_local(times: list, anchor_hour: int = 18) -> str | None:
    """Median of a list of datetime.time values that may straddle midnight
    (e.g. bedtimes), expressed as "HH:MM". Naive mean/median on raw
    datetime.time objects is wrong here: 23:50 and 00:10 would average to
    ~12:00, not ~00:00. Fix: shift each time into "minutes since anchor_hour
    today" space (wrapping forward past midnight adds 24h), where the whole
    typical range is monotonically increasing, THEN take the median, THEN
    convert back to HH:MM."""
    if not times:
        return None
    anchored_minutes = []
    for t in times:
        minutes = t.hour * 60 + t.minute
        anchor_minutes = anchor_hour * 60
        delta = minutes - anchor_minutes
        if delta < 0:
            delta += 24 * 60
        anchored_minutes.append(delta)
    anchored_minutes.sort()
    n = len(anchored_minutes)
    mid = anchored_minutes[n // 2] if n % 2 else (anchored_minutes[n // 2 - 1] + anchored_minutes[n // 2]) / 2
    real_minutes = (mid + anchor_hour * 60) % (24 * 60)
    return f"{int(real_minutes // 60):02d}:{int(real_minutes % 60):02d}"


@mcp.tool()
def get_sleep_pattern(start_date: str, end_date: str) -> dict:
    """Aggregated sleep-pattern analysis over a date range (avg/min/max duration vs. personal sleep-need target, qualifier-band counts, avg stage split, stress, bedtime/wake-time consistency, worst nights) -- entirely from locally hydrated Garmin sleep data, no live API call. The tool-ified version of manually comparing sleep_session rows; empty for any range predating hydration or never synced (returns nights_with_data: 0, not an error)."""
    from datetime import date as dt_date
    from collections import Counter

    with _connection() as conn:
        sessions = repository.get_sleep_sessions_range(
            conn, dt_date.fromisoformat(start_date), dt_date.fromisoformat(end_date)
        )

    nights_requested = (dt_date.fromisoformat(end_date) - dt_date.fromisoformat(start_date)).days + 1
    if not sessions:
        return {
            "start_date": start_date, "end_date": end_date,
            "nights_with_data": 0, "nights_requested": nights_requested,
            "avg_duration_hours": None, "min_duration_hours": None, "max_duration_hours": None,
            "avg_sleep_need_target_hours": None, "avg_deficit_hours": None,
            "duration_qualifier_counts": {}, "avg_stage_percentage": {},
            "avg_sleep_stress": None, "stress_qualifier_counts": {},
            "avg_awake_count": None, "avg_restless_moments": None,
            "bedtime_local": {}, "wake_time_local": {},
            "avg_overall_score": None, "score_qualifier_counts": {},
            "worst_nights": [],
        }

    durations = [s.total_sleep_seconds / 3600.0 for s in sessions if s.total_sleep_seconds is not None]
    needs = [s.sleep_need_baseline_minutes / 60.0 for s in sessions if s.sleep_need_baseline_minutes is not None]
    scores = [s.overall_score for s in sessions if s.overall_score is not None]
    stresses = [s.avg_sleep_stress for s in sessions if s.avg_sleep_stress is not None]
    awake_counts = [s.awake_count for s in sessions if s.awake_count is not None]
    restless_counts = [s.restless_moments_count for s in sessions if s.restless_moments_count is not None]
    rem_pcts = [s.rem_percentage for s in sessions if s.rem_percentage is not None]
    light_pcts = [s.light_percentage for s in sessions if s.light_percentage is not None]
    deep_pcts = [s.deep_percentage for s in sessions if s.deep_percentage is not None]

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    bedtimes = [s.sleep_start_local.time() for s in sessions if s.sleep_start_local]
    waketimes = [s.sleep_end_local.time() for s in sessions if s.sleep_end_local]

    avg_duration = _avg(durations)
    avg_need = _avg(needs)

    worst = sorted((s for s in sessions if s.overall_score is not None), key=lambda s: s.overall_score)[:3]

    return {
        "start_date": start_date, "end_date": end_date,
        "nights_with_data": len(sessions), "nights_requested": nights_requested,
        "avg_duration_hours": avg_duration,
        "min_duration_hours": round(min(durations), 2) if durations else None,
        "max_duration_hours": round(max(durations), 2) if durations else None,
        "avg_sleep_need_target_hours": avg_need,
        "avg_deficit_hours": round(avg_need - avg_duration, 2) if (avg_need is not None and avg_duration is not None) else None,
        "duration_qualifier_counts": dict(Counter(s.duration_qualifier for s in sessions if s.duration_qualifier)),
        "avg_stage_percentage": {"deep": _avg(deep_pcts), "light": _avg(light_pcts), "rem": _avg(rem_pcts)},
        "avg_sleep_stress": _avg(stresses),
        "stress_qualifier_counts": dict(Counter(s.stress_qualifier for s in sessions if s.stress_qualifier)),
        "avg_awake_count": _avg(awake_counts), "avg_restless_moments": _avg(restless_counts),
        "bedtime_local": {
            "earliest": min(bedtimes).strftime("%H:%M") if bedtimes else None,
            "latest": max(bedtimes).strftime("%H:%M") if bedtimes else None,
            # anchor_hour=18 (6pm): sits roughly opposite the typical bedtime
            # cluster (evening into past-midnight), so the anchor-space cut
            # falls in the middle of the day, well away from any real bedtime.
            "median": _median_clock_time_local(bedtimes),
        },
        "wake_time_local": {
            "earliest": min(waketimes).strftime("%H:%M") if waketimes else None,
            "latest": max(waketimes).strftime("%H:%M") if waketimes else None,
            # anchor_hour=12 (noon): sits roughly opposite the typical wake-time
            # cluster (early-to-mid morning). anchor_hour=0 would put the cut
            # at midnight itself, making the midnight-wrap branch this helper
            # exists for unreachable -- silently reinstating the bug it fixes.
            "median": _median_clock_time_local(waketimes, anchor_hour=12),
        },
        "avg_overall_score": _avg(scores),
        "score_qualifier_counts": dict(Counter(s.overall_score_qualifier for s in sessions if s.overall_score_qualifier)),
        "worst_nights": [
            {"date": s.calendar_date.isoformat(), "overall_score": s.overall_score,
             "duration_hours": round(s.total_sleep_seconds / 3600.0, 2) if s.total_sleep_seconds is not None else None}
            for s in worst
        ],
    }


# ---------------------------------------------------------------------------
# Action / Write Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def set_target(
    metric_type: str,
    target_value: float,
    operator: str,
    target_window: str,
    start_date: str,
    end_date: str | None = None,
    notes: str | None = None,
    target_id: str | None = None,
) -> Target:
    """Set or update an athlete target tracked on the dashboard (operator: 'gte'/'lte'/'eq', window: 'daily'/'weekly_sum'/'weekly_avg'/'by_date')."""
    if operator not in ("gte", "lte", "eq"):
        raise ValueError(f"Invalid operator '{operator}', must be 'gte', 'lte', or 'eq'")
    if target_window not in ("daily", "weekly_sum", "weekly_avg", "by_date"):
        raise ValueError(f"Invalid target_window '{target_window}'")

    t_id = target_id or f"target-{uuid.uuid4().hex[:8]}"
    s_date = date.fromisoformat(start_date)
    e_date = date.fromisoformat(end_date) if end_date else None
    target = Target(
        id=t_id,
        metric_type=metric_type,
        target_value=float(target_value),
        operator=operator,
        target_window=target_window,
        start_date=s_date,
        end_date=e_date,
        status="active",
        notes=notes,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    with _connection() as conn:
        return _with_utc_tzinfo(repository.save_target(conn, target))


@mcp.tool()
def delete_target(target_id: str) -> bool:
    """Remove or archive an active target by target_id."""
    with _connection() as conn:
        return repository.delete_target(conn, target_id)


@mcp.tool()
def save_training_plan(
    title: str,
    goal_description: str | None,
    start_date: str,
    target_date: str,
    plan_json: str,
    plan_id: str | None = None,
) -> TrainingPlan:
    """Commit a periodized training plan JSON to SQLite for dashboard visualization and progress tracking."""
    p_id = plan_id or f"plan-{uuid.uuid4().hex[:8]}"
    s_date = date.fromisoformat(start_date)
    t_date = date.fromisoformat(target_date)
    # Validate plan_json is valid JSON
    json.loads(plan_json)
    plan = TrainingPlan(
        id=p_id,
        title=title,
        goal_description=goal_description,
        start_date=s_date,
        target_date=t_date,
        plan_json=plan_json,
        status="active",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    with _connection() as conn:
        return _with_utc_tzinfo(repository.save_training_plan(conn, plan))


@mcp.tool()
def update_plan_status(plan_id: str, status: str) -> TrainingPlan:
    """Update training plan status ('active', 'paused', 'completed', 'archived')."""
    if status not in ("active", "paused", "completed", "archived"):
        raise ValueError(f"Invalid plan status '{status}'")
    with _connection() as conn:
        updated = repository.update_plan_status(conn, plan_id, status)
        if updated is None:
            raise ValueError(f"Training plan with id '{plan_id}' not found")
        return _with_utc_tzinfo(updated)


@mcp.tool()
def log_coach_note(
    date: str,
    category: str,
    note: str,
    tags: list[str] | None = None,
    note_id: str | None = None,
) -> CoachNote:
    """Log a qualitative observation, injury feedback, or coaching advice (category: 'injury'/'nutrition'/'feeling'/'gear'/'milestone'/'general')."""
    if category not in ("injury", "nutrition", "feeling", "gear", "milestone", "general"):
        raise ValueError(f"Invalid coach note category '{category}'")
    n_id = note_id or f"note-{uuid.uuid4().hex[:8]}"
    from datetime import date as dt_date
    n_date = dt_date.fromisoformat(date)
    tags_json = json.dumps(tags) if tags else None
    coach_note = CoachNote(
        id=n_id,
        date=n_date,
        category=category,
        note=note,
        tags_json=tags_json,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    with _connection() as conn:
        return _with_utc_tzinfo(repository.save_coach_note(conn, coach_note))


@mcp.tool()
def sync_garmin_data(days: int = 30, force_full_history: bool = False) -> dict[str, str]:
    """Trigger a sync from Garmin Connect to pull health and workout data into Athlytics.

    By default this is incremental: each metric_type resumes from its own
    checkpoint (the last date it was successfully synced through), so `days`
    only matters the very first time a metric_type is ever synced. Pass
    force_full_history=True to ignore checkpoints and refetch each metric_type's
    entire history from `days` ago through today -- a deliberate, slower,
    one-off resync (this can take minutes and issue many Garmin API calls),
    not something to pass on routine syncs.

    Incremental (non-force_full_history) runs also hydrate rich per-night
    sleep detail (session, stage-timeline segments, and restless-moment
    events) for nights since the last hydration checkpoint, re-walking a
    trailing grace window so a night whose data wasn't ready yet gets
    picked up on a later sync -- see get_report/get_metric_series for the
    local-data queries this enables. force_full_history runs skip sleep
    hydration entirely and leave its checkpoint untouched, so the next
    incremental sync resumes it correctly.
    """
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "garmin_credentials.enc"
    token_cache_dir = data_dir / "garmin_tokens"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Garmin credentials not found. Please connect your Garmin account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.garmin import GarminProvider
    from core.scheduler.sync import sync_all_metrics
    from datetime import date as dt_date, timedelta

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = GarminProvider(store, token_cache_dir)

    end_date = dt_date.today()
    start_date = end_date - timedelta(days=days)

    with _connection() as conn:
        results = sync_all_metrics(
            conn, provider, backfill_start=start_date, end=end_date, force_full_backfill=force_full_history,
            today=end_date, resync_grace_days=SYNC_RESYNC_GRACE_DAYS,
        )
        # provider.sync_hydration is the single shared call site for this
        # (also used by app.sync.perform_sync_pass's background/manual sync
        # pass) -- see its docstring for why hydration must run after every
        # Garmin sync, not just this one.
        results["garmin_sleep_detail"] = provider.sync_hydration(conn, start_date, end_date, force_full_history)
        return results


@mcp.tool()
def refetch_garmin_metric_range(metric_type: str, start: str, end: str) -> dict[str, str | int | list[str]]:
    """Force a direct refetch of one Garmin metric_type over an explicit date range, bypassing the sync checkpoint entirely.

    Routine syncs are incremental and never look backward once a day is
    behind the checkpoint (see sync_garmin_data) -- if a day's value
    wasn't ready yet when a routine sync walked past it (Garmin can
    compute metrics like resting_hr from overnight data with a lag, and
    the checkpoint-advance doesn't distinguish "not ready yet" from "no
    data"), it's silently skipped forever. Use this tool to check whether
    a gap found via get_metric_series or get_anomalies is a real "Garmin
    has no data for that day" gap or just a stale-checkpoint miss: call it
    on the missing range and see whether readings come back now.

    Always safe to call on already-synced history -- upserts are
    idempotent on (source, metric_type, timestamp), and this never reads
    or writes sync_checkpoint, so it can't move a routine sync's resume
    point backward or forward.
    """
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "garmin_credentials.enc"
    token_cache_dir = data_dir / "garmin_tokens"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Garmin credentials not found. Please connect your Garmin account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.garmin import GarminProvider
    from datetime import date as dt_date, timedelta

    start_date = dt_date.fromisoformat(start)
    end_date = dt_date.fromisoformat(end)
    if start_date > end_date:
        raise ValueError(f"start ({start}) must be on or before end ({end})")

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = GarminProvider(store, token_cache_dir)

    readings = provider.fetch(metric_type, start_date, end_date)

    with _connection() as conn:
        repository.upsert_readings(conn, readings)

    found_dates = {r.timestamp.date() for r in readings}
    still_missing_dates = []
    day = start_date
    while day <= end_date:
        if day not in found_dates:
            still_missing_dates.append(day.isoformat())
        day += timedelta(days=1)

    return {
        "metric_type": metric_type,
        "start": start,
        "end": end,
        "readings_found": len(readings),
        "still_missing_dates": still_missing_dates,
    }


@mcp.tool()
def refetch_garmin_sleep_detail_range(start: str, end: str) -> dict:
    """Force a direct re-hydration of Garmin sleep detail (session, stage timeline, restlessness) over an explicit date range, bypassing the sync checkpoint entirely -- same rationale as refetch_garmin_metric_range, for the sleep-detail hydration path instead of a flat metric_type.

    Always safe to call on already-hydrated nights: sleep_session is upserted and the two child tables use delete-then-insert per session, so a retry or overlapping range is idempotent. Never reads or writes sync_checkpoint, so it can't move a routine sync's resume point.
    """
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "garmin_credentials.enc"
    token_cache_dir = data_dir / "garmin_tokens"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Garmin credentials not found. Please connect your Garmin account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.garmin import GarminProvider
    from datetime import date as dt_date

    start_date = dt_date.fromisoformat(start)
    end_date = dt_date.fromisoformat(end)
    if start_date > end_date:
        raise ValueError(f"start ({start}) must be on or before end ({end})")

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = GarminProvider(store, token_cache_dir)

    with _connection() as conn:
        return provider.hydrate_recent_sleep(conn, since=start_date, until=end_date)


@mcp.tool()
def sync_strava_data(days: int = 30, force_full_history: bool = False) -> dict[str, str]:
    """Trigger a sync from Strava to pull activity data into Athlytics.

    By default this is incremental: each metric_type resumes from its own
    checkpoint (the last date it was successfully synced through), so `days`
    only matters the very first time a metric_type is ever synced. Pass
    force_full_history=True to ignore checkpoints and refetch each metric_type's
    entire history from `days` ago through today.
    """
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "strava_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Strava credentials not found. Please connect your Strava account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.strava import StravaProvider
    from core.scheduler.sync import sync_all_metrics
    from datetime import date as dt_date, timedelta

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = StravaProvider(store)

    end_date = dt_date.today()
    start_date = end_date - timedelta(days=days)

    with _connection() as conn:
        return sync_all_metrics(
            conn, provider, backfill_start=start_date, end=end_date, force_full_backfill=force_full_history,
            today=end_date, resync_grace_days=SYNC_RESYNC_GRACE_DAYS,
        )


@mcp.tool()
def sync_mi_fitness_data(days: int = 30, force_full_history: bool = False) -> dict[str, str]:
    """Trigger a sync from Mi Fitness to pull health data into Athlytics.

    By default this is incremental: each metric_type resumes from its own
    checkpoint (the last date it was successfully synced through), so `days`
    only matters the very first time a metric_type is ever synced. Pass
    force_full_history=True to ignore checkpoints and refetch each metric_type's
    entire history from `days` ago through today.
    """
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "mi_fitness_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Mi Fitness credentials not found. Please connect your Mi Fitness account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.mi_fitness import MiFitnessProvider
    from core.scheduler.sync import sync_all_metrics
    from datetime import date as dt_date, timedelta

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = MiFitnessProvider(store)

    end_date = dt_date.today()
    start_date = end_date - timedelta(days=days)

    with _connection() as conn:
        return sync_all_metrics(
            conn, provider, backfill_start=start_date, end=end_date, force_full_backfill=force_full_history,
            today=end_date, resync_grace_days=SYNC_RESYNC_GRACE_DAYS,
        )


@mcp.tool()
def sync_tonal_data(days: int = 30, force_full_history: bool = False) -> dict[str, str]:
    """Trigger a sync from Tonal to pull muscle-readiness, strength-score, and workout metrics into Athlytics.

    By default this is incremental: each metric_type resumes from its own
    checkpoint (the last date it was successfully synced through), so `days`
    only matters the very first time a metric_type is ever synced. Pass
    force_full_history=True to ignore checkpoints and refetch each metric_type's
    entire history from `days` ago through today.

    Incremental (non-force_full_history) runs also hydrate per-set strength
    detail for workouts since the last hydration -- see
    get_movement_history/get_muscle_group_volume for the local-data queries
    this enables. Each such workout also gets one extra get_workout_detail
    call (a real, non-free API cost, but bounded to just the workouts since
    the last checkpoint -- typically 0-1 per day) to pull guided-program
    metadata (program/workout title, target area, level, week/day) and
    calories into tonal_workout_meta and the activity row's name/calories,
    neither of which the cheap bulk endpoint carries. force_full_history
    runs skip hydration entirely (years of per-set data, and per-workout
    detail calls to match, is out of proportion to what a backfill needs)
    and leave the hydration checkpoint untouched, so the next incremental
    sync resumes it correctly.
    """
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "tonal_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Tonal credentials not found. Please connect your Tonal account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.tonal import TonalProvider
    from core.scheduler.sync import sync_all_metrics
    from datetime import date as dt_date, timedelta

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = TonalProvider(store)

    end_date = dt_date.today()
    start_date = end_date - timedelta(days=days)

    with _connection() as conn:
        results = sync_all_metrics(
            conn, provider, backfill_start=start_date, end=end_date, force_full_backfill=force_full_history,
            today=end_date, resync_grace_days=SYNC_RESYNC_GRACE_DAYS,
        )
        # provider.sync_hydration is the single shared call site for this
        # (also used by app.sync.perform_sync_pass's background/manual sync
        # pass) -- see its docstring for why hydration must run after every
        # Tonal sync_all_metrics call, not just this one.
        results["tonal_strength_sets"] = provider.sync_hydration(conn, start_date, end_date, force_full_history)
        return results


@mcp.tool()
def search_tonal_movements(query: str | None = None, muscle_group: str | None = None) -> list[dict]:
    """Search the Tonal movement library by a name/muscle-group keyword and/or an exact muscle group (e.g. 'Chest', 'Quads')."""
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "tonal_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Tonal credentials not found. Please connect your Tonal account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.tonal import TonalProvider

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = TonalProvider(store)

    return provider.search_movements(query=query, muscle_group=muscle_group)


@mcp.tool()
def get_tonal_workout_history(limit: int = 10) -> list[dict]:
    """Fetch the athlete's most recent Tonal strength workouts (most recent first), each with an activity_id usable with get_tonal_workout_detail."""
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "tonal_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Tonal credentials not found. Please connect your Tonal account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.tonal_client import TonalClient

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    client = TonalClient(store)

    # TonalClient.get_activities already returns the exact raw shape wanted
    # here (activity_id/date/title/type/duration_seconds/total_volume_lbs).
    # Going through TonalProvider.fetch_activities would map into the
    # Activity dataclass instead, which has no total_volume_lbs field and
    # would silently drop it -- so this tool talks to TonalClient directly
    # rather than through TonalProvider.
    return client.get_activities(limit=limit)


@mcp.tool()
def get_movement_history(query: str, limit: int = 20) -> list[dict]:
    """Chronological set history (reps, weight, one-rep-max, volume) for one Tonal movement across workouts -- the signal for whether a specific lift is progressing, entirely from locally hydrated data (no live Tonal API call). `query` accepts an exact movement_id or a name/keyword (e.g. "bench press"). Only movements synced at least once (via sync_tonal_data or get_tonal_workout_detail) are resolvable. If the keyword matches more than one distinct movement, returns the candidate list instead of guessing -- check for a "movement_id"/"movement_name" shape in the result to tell candidates apart from actual history rows."""
    with _connection() as conn:
        matches = repository.find_known_movements(conn, query)
        distinct_ids = {m["movement_id"] for m in matches}
        if len(distinct_ids) != 1:
            return matches
        movement_id = distinct_ids.pop()
        sets = repository.get_strength_sets_by_movement(conn, movement_id, limit=limit)
        return [
            {
                "date": s.occurred_at.isoformat(),
                "reps": s.reps,
                "weight_lbs": s.weight_lbs,
                "one_rep_max": s.one_rep_max,
                "volume_lbs": s.volume_lbs,
                "is_warm_up": s.is_warm_up,
                "struggling_score": s.struggling_score,
            }
            for s in sets
        ]


@mcp.tool()
def get_muscle_group_volume(start_date: str, end_date: str) -> list[dict]:
    """Trained volume by muscle group over a date range, aggregated entirely from locally hydrated Tonal data (no live API call) -- sorted busiest-first, so a muscle group missing from the results, or with an old last_trained date, is the "what have I been neglecting" signal. Only reflects muscle groups from workouts synced at least once via sync_tonal_data or get_tonal_workout_detail."""
    with _connection() as conn:
        return repository.get_muscle_group_volume(conn, date.fromisoformat(start_date), date.fromisoformat(end_date))


@mcp.tool()
def get_tonal_workout_detail(activity_id: str) -> dict:
    """Fetch the per-set breakdown (reps, weight, volume, one-rep-max) for one Tonal workout by activity_id."""
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "tonal_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Tonal credentials not found. Please connect your Tonal account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.tonal import TonalProvider

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = TonalProvider(store)

    with _connection() as conn:
        return provider.get_workout_detail(conn, activity_id)


@mcp.tool()
def estimate_tonal_workout(blocks: list[dict]) -> dict:
    """Estimate duration and set count for a candidate Tonal workout (a list of exercise blocks) without pushing it to the machine."""
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "tonal_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Tonal credentials not found. Please connect your Tonal account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.tonal import TonalProvider

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = TonalProvider(store)

    return provider.estimate_workout(blocks)


@mcp.tool()
def create_tonal_workout(title: str, blocks: list[dict]) -> dict:
    """Push a new workout onto the athlete's Tonal machine. Call estimate_tonal_workout first and confirm with the athlete before creating."""
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "tonal_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Tonal credentials not found. Please connect your Tonal account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.tonal import TonalProvider

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = TonalProvider(store)

    return provider.create_workout(title, blocks)


@mcp.tool()
def delete_tonal_workout(workout_id: str) -> bool:
    """Delete a workout from the athlete's Tonal account by workout_id."""
    data_dir = _db_path().parent
    secret_key_path = data_dir / ".env"
    credentials_path = data_dir / "tonal_credentials.enc"

    if not credentials_path.exists() or not secret_key_path.exists():
        raise ValueError("Tonal credentials not found. Please connect your Tonal account in Athlytics settings first.")

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore
    from core.providers.tonal import TonalProvider

    secret_key = get_or_create_secret_key(secret_key_path)
    store = CredentialStore(secret_key, credentials_path)
    provider = TonalProvider(store)

    return provider.delete_workout(workout_id)


# ---------------------------------------------------------------------------
# Dynamic Context Resources
# ---------------------------------------------------------------------------


@mcp.resource("athlytics://athlete/snapshot")
def athlete_snapshot() -> str:
    """Current 7-day health snapshot: 7d RHR/HRV vs baseline, training load, and sleep score."""
    with _connection() as conn:
        return build_athlete_snapshot(conn)


@mcp.resource("athlytics://training/current-state")
def training_current_state() -> str:
    """Active training plan details, current phase, scheduled workouts, and active targets."""
    with _connection() as conn:
        return build_training_current_state(conn)


@mcp.resource("athlytics://coach/context")
def coach_context() -> str:
    """Athlete coaching profile, recent qualitative feedback, injury history, and notes."""
    with _connection() as conn:
        return build_coach_context(conn)


@mcp.resource("athlytics://coach/playbook")
def coach_playbook() -> str:
    """Evidence-based coaching playbook: recovery gating, the 10% volume rule, deload cadence, and action persistence."""
    return build_coach_playbook()


# ---------------------------------------------------------------------------
# Workflow Prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def readiness_check() -> str:
    """Daily morning recovery check-in and workout readiness evaluation."""
    return prompt_readiness_check()


@mcp.prompt()
def weekly_review() -> str:
    """7-day training volume, recovery metrics, and target compliance retrospective."""
    return prompt_weekly_review()


@mcp.prompt()
def build_training_plan(
    goal: str, target_date: str, current_weekly_volume: str | None = None
) -> str:
    """Guides building a structured, periodized training block with the 10% rule and deload weeks."""
    return prompt_build_training_plan(goal, target_date, current_weekly_volume)


@mcp.prompt()
def build_tonal_program(goal: str, target_date: str | None = None) -> str:
    """Guides building a Tonal strength program around movement selection, muscle-group balance, and readiness, with an estimate-before-create confirmation step."""
    return prompt_build_tonal_program(goal, target_date)


if __name__ == "__main__":
    mcp.run()
