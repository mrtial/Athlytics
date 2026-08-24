import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from garminconnect import Garmin

import httpx

from core.providers.garmin import GarminAuthError, GarminProvider
from core.providers.mi_fitness import MiFitnessAuthError, MiFitnessProvider
from core.providers.strava import StravaAuthError, StravaProvider
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


def record_sync_run(conn: sqlite3.Connection, source: str, auth_error: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sync_run_status (source, last_run_at, auth_error) VALUES (?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET last_run_at = excluded.last_run_at, auth_error = excluded.auth_error
        """,
        (source, now, auth_error),
    )
    conn.commit()


def record_metric_statuses(conn: sqlite3.Connection, source: str, results: dict[str, str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO sync_metric_status (source, metric_type, status, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(source, metric_type) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
        """,
        [(source, metric_type, status, now) for metric_type, status in results.items()],
    )
    conn.commit()


def get_sync_status(conn: sqlite3.Connection, source: str = "garmin") -> dict:
    run_row = conn.execute(
        "SELECT last_run_at, auth_error FROM sync_run_status WHERE source = ?", (source,)
    ).fetchone()
    metric_rows = conn.execute(
        "SELECT metric_type, status, updated_at FROM sync_metric_status WHERE source = ? ORDER BY metric_type",
        (source,),
    ).fetchall()
    return {
        "last_run_at": run_row[0] if run_row else None,
        "auth_error": run_row[1] if run_row else None,
        "metrics": [{"metric_type": r[0], "status": r[1], "updated_at": r[2]} for r in metric_rows],
    }


def _run_provider_sync(
    conn: sqlite3.Connection,
    source: str,
    provider,
    backfill_start: date,
    end: date,
    chunk_days: int,
    pace_seconds: float,
    force_full_backfill: bool,
    on_metric_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Runs sync_all_metrics for an already-constructed, already-authenticated
    provider and records the run/metric statuses. Shared by every provider
    block in perform_sync_pass so a new source is one construction branch,
    not another copy of this bookkeeping."""
    results = sync_all_metrics(
        conn, provider, backfill_start, end,
        chunk_days=chunk_days, pace_seconds=pace_seconds, force_full_backfill=force_full_backfill,
        on_metric_progress=on_metric_progress,
    )
    record_sync_run(conn, source, auth_error=None)
    record_metric_statuses(conn, source, results)


def perform_sync_pass(
    db_path: Path,
    credential_store: CredentialStore,
    token_cache_dir: Path,
    garmin_client_factory: Callable[..., Garmin] = Garmin,
    strava_credential_store: CredentialStore | None = None,
    strava_http_client_factory: Callable[[], httpx.Client] | None = None,
    mi_fitness_credential_store: CredentialStore | None = None,
    force_full_backfill: bool = False,
    on_source_start: Callable[[str], None] | None = None,
    on_metric_progress: Callable[[str, int, int], None] | None = None,
) -> None:
    backfill_start = date.today() - timedelta(days=BACKFILL_LOOKBACK_DAYS)
    end = date.today()

    if credential_store.load() is not None:
        if on_source_start:
            on_source_start("garmin")
        conn = connect(db_path)
        try:
            try:
                provider = GarminProvider(credential_store, token_cache_dir, garmin_client_factory=garmin_client_factory)
            except GarminAuthError as exc:
                record_sync_run(conn, "garmin", auth_error=str(exc))
            else:
                _run_provider_sync(
                    conn, "garmin", provider, backfill_start, end, SYNC_CHUNK_DAYS, SYNC_PACE_SECONDS, force_full_backfill,
                    on_metric_progress=(lambda completed, total: on_metric_progress("garmin", completed, total)) if on_metric_progress else None,
                )
        finally:
            conn.close()

    if strava_credential_store is not None and strava_credential_store.load() is not None:
        if on_source_start:
            on_source_start("strava")
        conn = connect(db_path)
        try:
            http_client = strava_http_client_factory() if strava_http_client_factory else None
            try:
                provider = StravaProvider(strava_credential_store, http_client=http_client)
            except StravaAuthError as exc:
                record_sync_run(conn, "strava", auth_error=str(exc))
            else:
                _run_provider_sync(
                    conn, "strava", provider, backfill_start, end, SYNC_CHUNK_DAYS, SYNC_PACE_SECONDS, force_full_backfill,
                    on_metric_progress=(lambda completed, total: on_metric_progress("strava", completed, total)) if on_metric_progress else None,
                )
        finally:
            conn.close()

    if mi_fitness_credential_store is not None and mi_fitness_credential_store.load() is not None:
        if on_source_start:
            on_source_start("mi_fitness")
        conn = connect(db_path)
        try:
            try:
                provider = MiFitnessProvider(mi_fitness_credential_store)
            except MiFitnessAuthError as exc:
                record_sync_run(conn, "mi_fitness", auth_error=str(exc))
            else:
                _run_provider_sync(
                    conn, "mi_fitness", provider, backfill_start, end, SYNC_CHUNK_DAYS, SYNC_PACE_SECONDS, force_full_backfill,
                    on_metric_progress=(lambda completed, total: on_metric_progress("mi_fitness", completed, total)) if on_metric_progress else None,
                )
        finally:
            conn.close()


class BackgroundSyncScheduler:
    """Runs a sync_fn(force_full_backfill) on a roughly-daily cadence in a
    background thread, triggerable on demand so backfill starts immediately
    after a data source is connected rather than waiting up to
    interval_seconds. This is the *only* thread that ever calls sync_fn --
    routing every trigger (including a manual "resync all history" request)
    through here, instead of spawning ad-hoc threads elsewhere, is what
    guarantees two sync passes never run concurrently against the same
    provider APIs.
    """

    def __init__(self, sync_fn: Callable[[bool], None], interval_seconds: float = SYNC_INTERVAL_SECONDS):
        self._sync_fn = sync_fn
        self._interval_seconds = interval_seconds
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._force_full_backfill_requested = threading.Event()
        self._syncing = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def trigger(self, force_full_backfill: bool = False) -> None:
        if force_full_backfill:
            self._force_full_backfill_requested.set()
        self._wake_event.set()

    def is_syncing(self) -> bool:
        return self._syncing.is_set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            force_full_backfill = self._force_full_backfill_requested.is_set()
            self._force_full_backfill_requested.clear()
            self._syncing.set()
            try:
                self._sync_fn(force_full_backfill)
            except Exception:
                logger.exception("background sync pass failed")
            finally:
                self._syncing.clear()
            self._wake_event.wait(timeout=self._interval_seconds)
            self._wake_event.clear()
