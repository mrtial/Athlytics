import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from garminconnect import Garmin

from core.providers.garmin import GarminAuthError, GarminProvider
from core.scheduler.sync import sync_all_metrics
from core.security.credentials import CredentialStore
from core.storage.db import connect

logger = logging.getLogger(__name__)

BACKFILL_LOOKBACK_DAYS = 3650  # ~10 years; sync_all_metrics's chunking/checkpointing
                                # makes over-requesting cheap (empty chunks just advance
                                # the checkpoint), so this is a generous, not exact, cap.
SYNC_INTERVAL_SECONDS = 24 * 60 * 60
SYNC_PACE_SECONDS = 1.0
SYNC_CHUNK_DAYS = 30


def record_sync_run(conn: sqlite3.Connection, auth_error: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sync_run_status (id, last_run_at, auth_error) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET last_run_at = excluded.last_run_at, auth_error = excluded.auth_error
        """,
        (now, auth_error),
    )
    conn.commit()


def record_metric_statuses(conn: sqlite3.Connection, results: dict[str, str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO sync_metric_status (metric_type, status, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(metric_type) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
        """,
        [(metric_type, status, now) for metric_type, status in results.items()],
    )
    conn.commit()


def get_sync_status(conn: sqlite3.Connection) -> dict:
    run_row = conn.execute("SELECT last_run_at, auth_error FROM sync_run_status WHERE id = 1").fetchone()
    metric_rows = conn.execute(
        "SELECT metric_type, status, updated_at FROM sync_metric_status ORDER BY metric_type"
    ).fetchall()
    return {
        "last_run_at": run_row[0] if run_row else None,
        "auth_error": run_row[1] if run_row else None,
        "metrics": [{"metric_type": r[0], "status": r[1], "updated_at": r[2]} for r in metric_rows],
    }


def perform_sync_pass(
    db_path: Path,
    credential_store: CredentialStore,
    token_cache_dir: Path,
    garmin_client_factory: Callable[..., Garmin] = Garmin,
    force_full_backfill: bool = False,
) -> None:
    if credential_store.load() is None:
        return

    conn = connect(db_path)
    try:
        try:
            provider = GarminProvider(credential_store, token_cache_dir, garmin_client_factory=garmin_client_factory)
        except GarminAuthError as exc:
            record_sync_run(conn, auth_error=str(exc))
            return

        backfill_start = date.today() - timedelta(days=BACKFILL_LOOKBACK_DAYS)
        results = sync_all_metrics(
            conn,
            provider,
            backfill_start,
            date.today(),
            chunk_days=SYNC_CHUNK_DAYS,
            pace_seconds=SYNC_PACE_SECONDS,
            force_full_backfill=force_full_backfill,
        )
        record_sync_run(conn, auth_error=None)
        record_metric_statuses(conn, results)
    finally:
        conn.close()


class BackgroundSyncScheduler:
    """Runs a sync_fn() on a roughly-daily cadence in a background thread,
    triggerable on demand so backfill starts immediately after a data
    source is connected rather than waiting up to interval_seconds.
    """

    def __init__(self, sync_fn: Callable[[], None], interval_seconds: float = SYNC_INTERVAL_SECONDS):
        self._sync_fn = sync_fn
        self._interval_seconds = interval_seconds
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def trigger(self) -> None:
        self._wake_event.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sync_fn()
            except Exception:
                logger.exception("background sync pass failed")
            self._wake_event.wait(timeout=self._interval_seconds)
            self._wake_event.clear()
