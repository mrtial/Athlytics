import logging
import sqlite3
import time
from datetime import date, timedelta
from typing import Callable

from core.providers.base import Provider, RateLimitError
from core.storage import repository
from core.storage.models import MetricReading

logger = logging.getLogger(__name__)


def sync_all_metrics(
    conn: sqlite3.Connection,
    provider: Provider,
    backfill_start: date,
    end: date,
    chunk_days: int = 30,
    pace_seconds: float = 0.0,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
    force_full_backfill: bool = False,
    on_metric_progress: Callable[[int, int], None] | None = None,
    today: date | None = None,
) -> dict[str, str]:
    """Sync every metric_type the provider supports from its checkpoint (or
    backfill_start, if none yet) through end.

    force_full_backfill=True ignores any existing checkpoint and starts
    every metric_type back at backfill_start instead -- a deliberate,
    manually-triggered "resync all history" action (Settings page / MCP
    tool), distinct from the normal incremental sync that only ever moves
    forward. Refetching already-synced days is safe: upsert_readings is
    idempotent on (source, metric_type, timestamp).

    today, if given, marks the real wall-clock "today". Every real caller
    passes end=date.today(), and a day that hasn't finished yet can still
    gain new data after this sync pass runs -- a Garmin activity logged an
    hour after an earlier "Sync Now" click, for example. Without this, the
    checkpoint for a chunk reaching `end` gets set to `end` itself, so the
    *next* sync's cursor (checkpoint + 1 day) lands after `end` and reports
    "up_to_date" without ever re-fetching that day again -- silently
    hiding same-day data added after the first sync. Passing `today` caps
    the checkpoint written for any chunk reaching on-or-past it at
    `today - 1 day` instead, so the next pass always re-walks today rather
    than treating it as permanently done. Has no effect on chunks that end
    before `today` (a bounded historical backfill's days are already
    final). Omit (the default) to preserve the old unconditional
    checkpoint-through-chunk_end behavior.

    on_metric_progress(completed, total), if given, fires once per
    metric_type immediately after it's done (whether it ended up
    "complete", "up_to_date", or "failed") -- lets a caller show a running
    "N of TOTAL metrics" count for a backfill that can otherwise take a
    long time with no visible progress between metric_types.
    """
    if chunk_days < 1:
        raise ValueError(f"chunk_days must be >= 1, got {chunk_days}")

    results: dict[str, str] = {}
    metric_types = provider.supported_metric_types()
    total = len(metric_types)
    snapshot_types = frozenset(provider.snapshot_metric_types()) if hasattr(provider, "snapshot_metric_types") else frozenset()

    for completed, metric_type in enumerate(metric_types, start=1):
        if metric_type in snapshot_types:
            # A snapshot metric (e.g. Tonal's per-muscle readiness) has no
            # historical range support -- every call returns "right now"
            # regardless of (start, end). Chunking it like a real
            # time-series metric would issue the same call ~122 times over
            # a 10-year first backfill and write that many near-duplicate
            # rows. Fetch it exactly once per sync pass instead, ignoring
            # checkpoint/force_full_backfill (there's no history to resume
            # or re-walk), and record `end` as its checkpoint purely for
            # "last synced" display consistency with every other metric.
            try:
                readings = _fetch_with_backoff(provider, metric_type, end, end, max_retries, sleep_fn)
                repository.upsert_readings(conn, readings)
                repository.set_checkpoint(conn, provider.name, metric_type, end)
                results[metric_type] = "complete"
            except Exception:
                logger.exception("sync failed for metric_type=%s", metric_type)
                results[metric_type] = "failed"

            if on_metric_progress:
                on_metric_progress(completed, total)
            continue

        if force_full_backfill:
            cursor = backfill_start
        else:
            checkpoint = repository.get_checkpoint(conn, provider.name, metric_type)
            cursor = checkpoint + timedelta(days=1) if checkpoint else backfill_start

        if cursor > end:
            results[metric_type] = "up_to_date"
        else:
            try:
                while cursor <= end:
                    chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
                    readings = _fetch_with_backoff(provider, metric_type, cursor, chunk_end, max_retries, sleep_fn)
                    repository.upsert_readings(conn, readings)
                    if metric_type in ("activity_duration", "tonal_workout_duration") and hasattr(provider, "fetch_activities"):
                        try:
                            activities = provider.fetch_activities(cursor, chunk_end)
                            repository.upsert_activities(conn, activities)
                        except Exception:
                            logger.warning("failed to upsert activities during sync pass", exc_info=True)
                    checkpoint_value = chunk_end
                    if today is not None and chunk_end >= today:
                        checkpoint_value = min(chunk_end, today - timedelta(days=1))
                    repository.set_checkpoint(conn, provider.name, metric_type, checkpoint_value)
                    cursor = chunk_end + timedelta(days=1)
                    if pace_seconds and cursor <= end:
                        sleep_fn(pace_seconds)
                results[metric_type] = "complete"
            except Exception:
                logger.exception("sync failed for metric_type=%s", metric_type)
                results[metric_type] = "failed"

        if on_metric_progress:
            on_metric_progress(completed, total)

    return results


def _fetch_with_backoff(
    provider: Provider,
    metric_type: str,
    start: date,
    end: date,
    max_retries: int,
    sleep_fn: Callable[[float], None],
) -> list[MetricReading]:
    attempt = 0
    delay = 1.0
    while True:
        try:
            return provider.fetch(metric_type, start, end)
        except RateLimitError:
            attempt += 1
            if attempt > max_retries:
                raise
            sleep_fn(delay)
            delay *= 2
