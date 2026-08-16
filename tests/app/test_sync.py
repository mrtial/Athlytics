import threading
import time

import pytest
from cryptography.fernet import Fernet

from app.db import ensure_app_schema
from app.sync import (
    BackgroundSyncScheduler,
    get_sync_status,
    perform_sync_pass,
    record_metric_statuses,
    record_sync_run,
)
from core.security.credentials import CredentialStore
from core.storage.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    ensure_app_schema(c)
    return c


def test_record_and_get_sync_run_status_roundtrips(conn):
    record_sync_run(conn, auth_error=None)

    status = get_sync_status(conn)

    assert status["auth_error"] is None
    assert status["last_run_at"] is not None


def test_record_sync_run_persists_auth_error(conn):
    record_sync_run(conn, auth_error="Garmin requires an MFA code")

    status = get_sync_status(conn)

    assert status["auth_error"] == "Garmin requires an MFA code"


def test_record_sync_run_overwrites_previous_run(conn):
    record_sync_run(conn, auth_error="first error")
    record_sync_run(conn, auth_error=None)

    assert get_sync_status(conn)["auth_error"] is None


def test_record_and_get_metric_statuses(conn):
    record_metric_statuses(conn, {"steps": "complete", "resting_hr": "failed"})

    status = get_sync_status(conn)

    metrics = {m["metric_type"]: m["status"] for m in status["metrics"]}
    assert metrics == {"steps": "complete", "resting_hr": "failed"}


def test_record_metric_statuses_upserts_existing_metric_type(conn):
    record_metric_statuses(conn, {"steps": "failed"})
    record_metric_statuses(conn, {"steps": "complete"})

    metrics = {m["metric_type"]: m["status"] for m in get_sync_status(conn)["metrics"]}
    assert metrics == {"steps": "complete"}


def test_get_sync_status_before_any_run_has_no_run_and_no_metrics(conn):
    status = get_sync_status(conn)

    assert status["last_run_at"] is None
    assert status["auth_error"] is None
    assert status["metrics"] == []


class _StubGarminClient:
    def __init__(self, email, password, return_on_mfa=False):
        self.email = email
        self.password = password

    def login(self, tokenstore=None):
        return (False, None)

    def get_rhr_daily(self, start, end):
        return []


def test_perform_sync_pass_is_noop_when_no_credentials_saved(tmp_path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    ensure_app_schema(conn)
    conn.close()
    credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")

    perform_sync_pass(db_path, credential_store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)

    conn = connect(db_path)
    status = get_sync_status(conn)
    assert status["last_run_at"] is None
    assert status["metrics"] == []


class _MfaRequiredClient(_StubGarminClient):
    def login(self, tokenstore=None):
        return (True, None)


def test_perform_sync_pass_records_auth_error_on_mfa_challenge(tmp_path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    ensure_app_schema(conn)
    conn.close()
    credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    credential_store.save({"email": "a@example.com", "password": "x"})

    perform_sync_pass(db_path, credential_store, tmp_path / "tokens", garmin_client_factory=_MfaRequiredClient)

    conn = connect(db_path)
    status = get_sync_status(conn)
    assert status["auth_error"] is not None
    assert "MFA" in status["auth_error"]
    assert status["metrics"] == []


class _OneMetricClient(_StubGarminClient):
    """A stub whose GarminProvider only ever registers resting_hr."""


def test_perform_sync_pass_calls_sync_all_metrics_and_records_results(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    ensure_app_schema(conn)
    conn.close()
    credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    credential_store.save({"email": "a@example.com", "password": "x"})

    def fake_sync_all_metrics(conn, provider, backfill_start, end, chunk_days=30, pace_seconds=0.0, **kwargs):
        return {"resting_hr": "complete"}

    monkeypatch.setattr("app.sync.sync_all_metrics", fake_sync_all_metrics)

    perform_sync_pass(db_path, credential_store, tmp_path / "tokens", garmin_client_factory=_OneMetricClient)

    conn = connect(db_path)
    status = get_sync_status(conn)
    assert status["auth_error"] is None
    assert status["last_run_at"] is not None
    metrics = {m["metric_type"]: m["status"] for m in status["metrics"]}
    assert metrics == {"resting_hr": "complete"}


def test_background_sync_scheduler_trigger_runs_sync_fn_promptly():
    ran = threading.Event()

    def sync_fn():
        ran.set()

    scheduler = BackgroundSyncScheduler(sync_fn, interval_seconds=1000)
    scheduler.start()
    try:
        assert ran.wait(timeout=2), "trigger should cause sync_fn to run without waiting the full interval"
        ran.clear()
        scheduler.trigger()
        assert ran.wait(timeout=2)
    finally:
        scheduler.stop(timeout=2)


def test_background_sync_scheduler_stop_joins_thread():
    scheduler = BackgroundSyncScheduler(lambda: None, interval_seconds=1000)
    scheduler.start()

    scheduler.stop(timeout=2)

    assert not scheduler._thread.is_alive()


def test_background_sync_scheduler_swallows_exceptions_from_sync_fn():
    calls = []

    def flaky_sync_fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    scheduler = BackgroundSyncScheduler(flaky_sync_fn, interval_seconds=1000)
    scheduler.start()
    try:
        deadline = time.time() + 2
        while len(calls) < 1 and time.time() < deadline:
            time.sleep(0.05)
        assert len(calls) == 1  # thread must still be alive after the exception, not crashed
        assert scheduler._thread.is_alive()
    finally:
        scheduler.stop(timeout=2)
