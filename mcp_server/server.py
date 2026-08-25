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
from datetime import date, datetime, timezone
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
        return sync_all_metrics(
            conn, provider, backfill_start=start_date, end=end_date, force_full_backfill=force_full_history,
            today=end_date,
        )


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
            today=end_date,
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
            today=end_date,
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
    detail for workouts since the last hydration, at no extra API cost --
    see get_movement_history/get_muscle_group_volume for the local-data
    queries this enables. force_full_history runs skip hydration entirely
    (years of per-set data is out of proportion to what a backfill needs)
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
            today=end_date,
        )
        if force_full_history:
            results["tonal_strength_sets"] = "skipped (full history sync)"
        else:
            checkpoint = repository.get_checkpoint(conn, "tonal", "tonal_strength_sets")
            # Re-hydrate the checkpoint day itself (not checkpoint + 1): unlike
            # core/scheduler/sync.py's daily-aggregate checkpoints, where
            # re-processing the checkpoint day would double-count, upsert_strength_sets
            # is idempotent by id, so re-hydrating it on every sync is free and
            # correct -- and skipping it would silently drop any workout logged
            # later the same day as a prior sync, with no in-product recovery path.
            hydrate_since = checkpoint if checkpoint else start_date
            try:
                hydration = provider.hydrate_recent_strength_sets(conn, since=hydrate_since)
                repository.set_checkpoint(conn, "tonal", "tonal_strength_sets", end_date)
                results["tonal_strength_sets"] = f"{hydration['sets']} sets across {hydration['workouts']} workouts"
            except Exception as exc:
                # Isolate hydration failures (rate limits, HTTP 5xx, auth
                # errors from the pre-loop fetch calls) from the results
                # sync_all_metrics already successfully computed above --
                # don't let a hydration error discard a good sync. Leave the
                # checkpoint untouched so the next sync retries this window.
                logger.warning("Tonal strength-set hydration failed", exc_info=True)
                results["tonal_strength_sets"] = f"hydration failed: {exc}"
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
