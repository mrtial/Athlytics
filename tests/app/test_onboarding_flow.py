from app.session import SESSION_COOKIE_NAME


def test_root_redirects_to_admin_creation_on_first_run(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/admin"


def test_root_redirects_through_each_onboarding_step_in_order(client):
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/onboarding/admin"

    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/onboarding/profile"

    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/onboarding/persona"

    client.post("/onboarding/persona", data={"persona": "full_overview"})
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/onboarding/theme"

    client.post("/onboarding/theme", data={"theme": "light"})
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/onboarding/connect"


def test_root_redirects_to_dashboard_once_onboarding_complete_and_logged_in(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == "/dashboard"


def test_root_redirects_to_login_when_onboarding_complete_but_not_logged_in(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    client.cookies.clear()

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == "/login"


class _E2EStubGarminClient:
    def __init__(self, email, password, return_on_mfa=False):
        pass

    def login(self, tokenstore=None):
        return (False, None)


def test_root_redirects_to_dashboard_when_only_apple_health_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    from core.storage import repository
    from core.storage.db import connect
    from datetime import date
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "steps", date(2026, 1, 1))

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == "/dashboard"


def test_root_redirects_to_dashboard_when_only_strava_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    app.state.strava_credential_store.save(
        {"client_id": "1", "client_secret": "s", "access_token": "a", "refresh_token": "r", "expires_at": "9999999999"}
    )

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == "/dashboard"


def test_root_still_redirects_to_connect_when_neither_source_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == "/onboarding/connect"


def test_step_tracker_lets_athlete_navigate_back_to_a_completed_step(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})

    response = client.get("/onboarding/theme")

    assert response.status_code == 200
    assert 'href="/onboarding/profile"' in response.text
    assert 'href="/onboarding/persona"' in response.text


def test_step_tracker_does_not_link_future_steps(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/profile")

    assert response.status_code == 200
    assert 'href="/onboarding/persona"' not in response.text
    assert 'href="/onboarding/theme"' not in response.text
    assert 'href="/onboarding/connect"' not in response.text


def test_full_onboarding_flow_end_to_end(app, client, monkeypatch):
    """Walks the entire design-doc Onboarding Flow through the real running
    app: admin creation -> profile -> persona -> theme -> connect ->
    dashboard usable, with a background sync pass triggered and completing
    before the dashboard is checked.
    """
    app.state.garmin_client_factory = _E2EStubGarminClient

    import threading

    pass_finished = threading.Event()

    def fake_sync_all_metrics(conn, provider, backfill_start, end, chunk_days=30, pace_seconds=0.0, **kwargs):
        pass_finished.set()
        return {"resting_hr": "complete"}

    monkeypatch.setattr("app.sync.sync_all_metrics", fake_sync_all_metrics)

    # Step 1: first run redirects to admin creation.
    assert client.get("/", follow_redirects=False).headers["location"] == "/onboarding/admin"

    # Step 1 (cont'd): create the admin account -- this also logs them in.
    response = client.post(
        "/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert client.cookies.get(SESSION_COOKIE_NAME) is not None

    # Step 2: fill in the athlete profile.
    response = client.post(
        "/onboarding/profile", data={"athlete_name": "Jamie Rivera", "athlete_dob": "1995-06-15"}, follow_redirects=False
    )
    assert response.headers["location"] == "/onboarding/persona"

    # Step 3: choose a persona.
    response = client.post("/onboarding/persona", data={"persona": "endurance_runner"}, follow_redirects=False)
    assert response.headers["location"] == "/onboarding/theme"

    # Step 4: choose a theme.
    response = client.post("/onboarding/theme", data={"theme": "dark"}, follow_redirects=False)
    assert response.headers["location"] == "/onboarding/connect"

    # Step 5: connect a data source (Garmin).
    response = client.post(
        "/api/data-sources/garmin/connect",
        data={"email": "athlete@example.com", "password": "hunter2"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"

    # Step 6: backfill starts in the background immediately (triggered by
    # the connect handler) -- wait for the triggered pass to complete.
    assert pass_finished.wait(timeout=5), "background sync pass should run promptly after connect"

    # The dashboard is usable now.
    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 200
    assert "Jamie Rivera" in dashboard_response.text

    # The sync-status panel reflects the completed pass.
    status_response = client.get("/api/sync-status")
    body = status_response.json()
    garmin_status = body["providers"]["garmin"]
    assert garmin_status["connected"] is True
    assert garmin_status["auth_error"] is None
    metrics = {m["metric_type"]: m["status"] for m in garmin_status["metrics"]}
    assert metrics == {"resting_hr": "complete"}

    # Settings: persona/theme are changeable after onboarding.
    response = client.post("/settings/persona", data={"persona": "sleep_recovery_focus"}, follow_redirects=False)
    assert response.status_code == 303
    settings_response = client.get("/settings")
    assert "sleep_recovery_focus" in settings_response.text

    # Logout, then re-visiting root sends the user to login, not back
    # through onboarding (onboarding is already complete).
    client.post("/logout")
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"

    # Logging back in returns them straight to a usable dashboard.
    response = client.post(
        "/login", data={"username": "athlete", "password": "hunter2hunter2"}, follow_redirects=False
    )
    assert response.headers["location"] == "/dashboard"
    assert client.get("/dashboard").status_code == 200
