from app.db import ensure_app_schema
from app.sync import record_metric_statuses, record_sync_run
from core.security.credentials import CredentialStore
from core.storage.db import connect
from cryptography.fernet import Fernet


def _login(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})


def test_sync_status_route_requires_admin_login(client):
    response = client.get("/api/sync-status")

    assert response.status_code == 401


def test_sync_status_route_reports_not_connected_before_any_connect(client):
    _login(client)

    response = client.get("/api/sync-status")

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["last_run_at"] is None
    assert body["auth_error"] is None
    assert body["metrics"] == []


def test_sync_status_route_reports_connected_after_credentials_saved(app, client):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    response = client.get("/api/sync-status")

    assert response.json()["connected"] is True


def test_sync_status_route_reflects_recorded_run_and_metric_statuses(app, client):
    _login(client)
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    record_sync_run(conn, auth_error=None)
    record_metric_statuses(conn, {"steps": "complete", "resting_hr": "failed"})
    conn.close()

    response = client.get("/api/sync-status")

    body = response.json()
    assert body["last_run_at"] is not None
    metrics = {m["metric_type"]: m["status"] for m in body["metrics"]}
    assert metrics == {"steps": "complete", "resting_hr": "failed"}


def test_sync_status_route_surfaces_auth_error(app, client):
    _login(client)
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    record_sync_run(conn, auth_error="Garmin requires an MFA code to complete login")
    conn.close()

    response = client.get("/api/sync-status")

    assert response.json()["auth_error"] == "Garmin requires an MFA code to complete login"


def test_sync_trigger_route_triggers_background_scheduler(client):
    _login(client)
    response = client.post("/api/sync/trigger")
    assert response.status_code == 200
    assert response.json() == {"status": "triggered"}


def test_full_history_sync_route_runs_perform_sync_pass_with_force_full_backfill(client, monkeypatch):
    import threading

    _login(client)
    ran = threading.Event()
    captured = {}

    def fake_perform_sync_pass(*args, **kwargs):
        captured.update(kwargs)
        ran.set()

    monkeypatch.setattr("app.routes.sync_status.perform_sync_pass", fake_perform_sync_pass)

    response = client.post("/api/sync/full-history")

    assert response.status_code == 200
    assert response.json() == {"status": "started"}
    assert ran.wait(timeout=2), "route should run perform_sync_pass in a background thread"
    assert captured.get("force_full_backfill") is True
