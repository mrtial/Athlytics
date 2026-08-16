import logging
import time
from datetime import date, timedelta
from typing import Callable

from core.providers.base import Provider, RateLimitError
from core.storage import repository

logger = logging.getLogger(__name__)


def sync_all_metrics(
    conn,
    provider: Provider,
    backfill_start: date,
    end: date,
    chunk_days: int = 30,
    pace_seconds: float = 0.0,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    results: dict[str, str] = {}

    for metric_type in provider.supported_metric_types():
        checkpoint = repository.get_checkpoint(conn, provider.name, metric_type)
        cursor = checkpoint + timedelta(days=1) if checkpoint else backfill_start

        if cursor > end:
            results[metric_type] = "up_to_date"
            continue

        try:
            while cursor <= end:
                chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
                readings = _fetch_with_backoff(provider, metric_type, cursor, chunk_end, max_retries, sleep_fn)
                repository.upsert_readings(conn, readings)
                repository.set_checkpoint(conn, provider.name, metric_type, chunk_end)
                cursor = chunk_end + timedelta(days=1)
                if pace_seconds and cursor <= end:
                    sleep_fn(pace_seconds)
            results[metric_type] = "complete"
        except Exception:
            logger.exception("sync failed for metric_type=%s", metric_type)
            results[metric_type] = "failed"

    return results


def _fetch_with_backoff(
    provider: Provider,
    metric_type: str,
    start: date,
    end: date,
    max_retries: int,
    sleep_fn: Callable[[float], None],
):
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
