def _login(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})


def test_connections_page_requires_admin_login(client):
    response = client.get("/connections", follow_redirects=False)

    assert response.status_code == 303


def test_connections_page_lists_all_four_providers_when_none_connected(client):
    _login(client)

    response = client.get("/connections")

    assert response.status_code == 200
    assert "Garmin" in response.text
    assert "Strava" in response.text
    assert "Apple Health" in response.text
    assert "Mi Fitness" in response.text


def test_connections_page_includes_mi_fitness_qr_login_section(client):
    _login(client)

    response = client.get("/connections")

    assert "data-mi-fitness-start" in response.text


def test_connections_page_shows_garmin_connected_after_credentials_saved(app, client):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    response = client.get("/connections")

    assert "Connected" in response.text
    assert "status-dot" in response.text


def test_connections_page_shows_sync_status_for_connected_provider(app, client):
    from app.sync import record_metric_statuses, record_sync_run
    from core.storage.db import connect

    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    conn = connect(app.state.db_path)
    record_sync_run(conn, "garmin", auth_error=None)
    record_metric_statuses(conn, "garmin", {"resting_hr": "complete", "hrv": "failed"})
    conn.close()

    response = client.get("/connections")

    assert "Sync Status" in response.text
    assert "resting hr: complete" in response.text.lower()
    assert "hrv: failed" in response.text.lower()


def test_connections_page_shows_syncing_badge_for_the_source_currently_syncing(app, client, monkeypatch):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    monkeypatch.setattr(app.state.sync_scheduler, "is_syncing", lambda: True)
    app.state.currently_syncing_source = "garmin"

    response = client.get("/connections")

    assert 'data-sync-status-badge="garmin"' in response.text
    assert "Syncing" in response.text


def test_connections_page_shows_metric_progress_count_in_syncing_badge(app, client, monkeypatch):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    monkeypatch.setattr(app.state.sync_scheduler, "is_syncing", lambda: True)
    app.state.currently_syncing_source = "garmin"
    app.state.sync_metric_progress = {"completed": 5, "total": 18}

    response = client.get("/connections")

    badge_html = response.text.split('data-sync-status-badge="garmin"')[1].split("</span>")[0]
    assert "5" in badge_html and "18" in badge_html


def test_connections_page_shows_syncing_badge_without_count_when_progress_not_yet_known(app, client, monkeypatch):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    monkeypatch.setattr(app.state.sync_scheduler, "is_syncing", lambda: True)
    app.state.currently_syncing_source = "garmin"
    app.state.sync_metric_progress = None

    response = client.get("/connections")

    badge_html = response.text.split('data-sync-status-badge="garmin"')[1].split("</span>")[0]
    assert "Syncing" in badge_html


def test_connections_page_omits_syncing_badge_for_a_different_source(app, client, monkeypatch):
    # Garmin is connected and healthy; a sync is running for Strava, not
    # Garmin -- Garmin's own badge must still read Healthy, not Syncing.
    _login(client)
    from app.sync import record_sync_run
    from core.storage.db import connect

    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    conn = connect(app.state.db_path)
    record_sync_run(conn, "garmin", auth_error=None)
    conn.close()
    monkeypatch.setattr(app.state.sync_scheduler, "is_syncing", lambda: True)
    app.state.currently_syncing_source = "strava"

    response = client.get("/connections")

    garmin_panel = response.text.split('data-source="garmin"')[1].split('data-source="apple_health"')[0]
    badge_html = garmin_panel.split('data-sync-status-badge="garmin"')[1].split("</span>")[0]
    assert "Syncing" not in badge_html
    assert "Healthy" in garmin_panel


def test_connections_page_includes_sync_status_poll_script(client):
    _login(client)

    response = client.get("/connections")

    assert 'addEventListener("DOMContentLoaded", pollSyncStatus)' in response.text


def test_connections_page_renders_raw_utc_last_synced_timestamp_not_server_formatted(app, client):
    # last_run_display used to be pre-formatted server-side with no
    # timezone conversion, showing raw UTC wall-clock time mislabeled as
    # local (see app/static/app.js's hydrateLocalTimestamps). Only a raw,
    # offset-aware UTC timestamp should reach the template now.
    from app.sync import record_sync_run
    from core.storage.db import connect

    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    conn = connect(app.state.db_path)
    record_sync_run(conn, "garmin", auth_error=None)
    conn.close()

    response = client.get("/connections")

    assert "data-utc-timestamp=" in response.text
    assert "last_run_display" not in response.text


def test_connections_page_shows_auth_error_for_connected_provider(app, client):
    from app.sync import record_sync_run
    from core.storage.db import connect

    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    conn = connect(app.state.db_path)
    record_sync_run(conn, "garmin", auth_error="Garmin requires an MFA code to complete login")
    conn.close()

    response = client.get("/connections")

    assert "Needs attention" in response.text
    assert "Garmin requires an MFA code" in response.text


def test_connections_page_omits_sync_status_for_disconnected_provider(client):
    _login(client)

    response = client.get("/connections")

    assert "Sync Status" not in response.text


def test_connections_page_omits_sync_status_for_apple_health(app, client):
    from core.storage import repository
    from core.storage.db import connect
    from datetime import date

    _login(client)
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "steps", date(2026, 1, 1))
    conn.close()

    response = client.get("/connections")

    assert "Sync Status" not in response.text


def test_connections_page_includes_garmin_connect_form(client):
    _login(client)

    response = client.get("/connections")

    assert 'action="/api/data-sources/garmin/connect"' in response.text


def test_connections_page_includes_strava_authorize_form(client):
    _login(client)

    response = client.get("/connections")

    assert 'action="/oauth/strava/authorize"' in response.text


def test_connections_page_includes_apple_health_import_form(client):
    _login(client)

    response = client.get("/connections")

    assert 'action="/api/data-sources/apple-health/import"' in response.text


def test_connections_page_apple_health_upload_toggle_open_by_default_when_not_connected(client):
    _login(client)

    response = client.get("/connections")

    assert _details_is_open(response.text, "Upload export file") is True
    assert _details_is_open(response.text, "Set up automatic sync via iOS Shortcut") is False


def test_connections_page_apple_health_toggles_both_collapsed_when_connected(app, client):
    from datetime import date

    from core.storage import repository
    from core.storage.db import connect

    _login(client)
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "steps", date(2026, 1, 1))

    response = client.get("/connections")

    assert _details_is_open(response.text, "Upload export file") is False
    assert _details_is_open(response.text, "Set up automatic sync via iOS Shortcut") is False


def test_connections_page_shows_priority_picker_only_for_overlapping_metric_types(app, client):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    from core.storage import repository
    from core.storage.db import connect
    from datetime import date
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "resting_hr", date(2026, 1, 1))  # overlaps Garmin
    repository.set_checkpoint(conn, "apple_health", "mindful_minutes", date(2026, 1, 1))  # Apple-only

    response = client.get("/connections")

    assert 'value="resting_hr"' in response.text
    assert 'value="mindful_minutes"' not in response.text  # no overlap, no picker row


def test_connections_page_auto_generates_api_token_and_shows_it(app, client):
    from app.settings import get_api_token
    from core.storage.db import connect

    _login(client)

    response = client.get("/connections")

    assert response.status_code == 200
    conn = connect(app.state.db_path)
    token = get_api_token(conn)
    assert token is not None
    assert token in response.text


def test_connections_page_reuses_existing_api_token_across_requests(app, client):
    from app.settings import get_api_token
    from core.storage.db import connect

    _login(client)

    client.get("/connections")
    conn = connect(app.state.db_path)
    first_token = get_api_token(conn)

    client.get("/connections")
    second_token = get_api_token(conn)

    assert first_token == second_token


def test_connections_page_shows_apple_health_upload_url_and_auth_header_snippet(client):
    _login(client)

    response = client.get("/connections")

    assert "/api/data-sources/apple-health/import" in response.text
    assert "Authorization: Bearer" in response.text


def test_connections_page_shows_radio_picker_for_all_providers(client):
    _login(client)

    response = client.get("/connections")

    assert 'value="garmin"' in response.text
    assert 'value="strava"' in response.text
    assert 'value="apple_health"' in response.text
    assert 'value="mi_fitness"' in response.text


def test_connections_page_shows_strava_subscription_disclosure(client):
    _login(client)

    response = client.get("/connections")

    assert "subscription" in response.text.lower()


def _details_is_open(html: str, summary_text: str) -> bool:
    """True if the <details> immediately preceding a <summary> containing
    summary_text has an `open` attribute -- used to check which of
    Strava's two toggle sections (API / file import) starts expanded."""
    before_summary = html.split(summary_text)[0]
    details_tag = before_summary.rsplit("<details", 1)[1].split(">")[0]
    return "open" in details_tag.split()


def test_connections_page_strava_api_toggle_open_by_default_when_not_connected(client):
    _login(client)

    response = client.get("/connections")

    assert _details_is_open(response.text, "Connect with API") is True
    assert _details_is_open(response.text, "Or import from a data export file") is False


def test_connections_page_strava_toggles_both_collapsed_when_connected(app, client):
    from datetime import datetime

    from core.storage import repository
    from core.storage.db import connect
    from core.storage.models import Activity

    _login(client)
    conn = connect(app.state.db_path)
    repository.upsert_activities(
        conn,
        [
            Activity(
                id="strava:1", source="strava", activity_id="1", activity_name="Morning Run",
                activity_type="running", sport_type="Run", start_time=datetime(2026, 1, 1, 7, 0),
                duration_seconds=1800.0, distance_meters=5000.0, calories=300.0, avg_hr=140.0, max_hr=160.0,
                avg_speed=2.8, max_speed=3.5, elevation_gain=20.0, elevation_loss=20.0,
                created_at=datetime(2026, 1, 1, 8, 0),
            )
        ],
    )

    response = client.get("/connections")

    assert _details_is_open(response.text, "Connect with API") is False
    assert _details_is_open(response.text, "Or import from a data export file") is False


def test_connections_page_includes_strava_export_upload_form(client):
    _login(client)

    response = client.get("/connections")

    assert 'action="/api/data-sources/strava/import"' in response.text
    assert 'accept=".zip"' in response.text
    # The main OAuth connect form stays the default -- upload is the alternative.
    assert 'action="/oauth/strava/authorize"' in response.text


def test_connections_page_shows_strava_export_instructions_behind_a_toggle(client):
    _login(client)

    response = client.get("/connections")

    assert "<details" in response.text
    assert "Request Your Archive" in response.text


def test_connections_page_shows_strava_connected_after_file_import_without_oauth(app, client):
    from datetime import datetime

    from core.storage import repository
    from core.storage.db import connect
    from core.storage.models import Activity

    _login(client)
    conn = connect(app.state.db_path)
    repository.upsert_activities(
        conn,
        [
            Activity(
                id="strava:1",
                source="strava",
                activity_id="1",
                activity_name="Morning Run",
                activity_type="running",
                sport_type="Run",
                start_time=datetime(2026, 1, 1, 7, 0),
                duration_seconds=1800.0,
                distance_meters=5000.0,
                calories=300.0,
                avg_hr=140.0,
                max_hr=160.0,
                avg_speed=2.8,
                max_speed=3.5,
                elevation_gain=20.0,
                elevation_loss=20.0,
                created_at=datetime(2026, 1, 1, 8, 0),
            )
        ],
    )

    response = client.get("/connections")

    assert response.text.index('value="strava"') < response.text.index('value="garmin"')
    assert response.text.index('value="strava"') < response.text.index('value="apple_health"')
    assert response.text.index('value="strava"') < response.text.index('value="mi_fitness"')


def test_connections_page_shows_apple_health_shortcut_instructions(client):
    _login(client)

    response = client.get("/connections")

    assert "Shortcut" in response.text
    assert "export_file" in response.text


def test_connections_page_shows_a_scannable_qr_code_for_shortcut_setup(client):
    _login(client)

    response = client.get("/connections")

    assert "<svg" in response.text


def test_settings_regenerate_api_token_route_issues_a_new_token_and_redirects_to_connections(app, client):
    from app.settings import get_api_token
    from core.storage.db import connect

    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.get("/connections")
    conn = connect(app.state.db_path)
    original_token = get_api_token(conn)

    response = client.post("/settings/api-token/regenerate", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/connections"
    new_token = get_api_token(conn)
    assert new_token != original_token


def test_connections_page_sorts_connected_providers_before_disconnected_ones(app, client):
    # Strava is not first in PROVIDER_REGISTRY -- connecting only it must
    # still bring it to the front of both the picker list and the panels.
    _login(client)
    app.state.strava_credential_store.save({"access_token": "t", "refresh_token": "r", "expires_at": 0})

    response = client.get("/connections")

    assert response.text.index('value="strava"') < response.text.index('value="garmin"')
    assert response.text.index('value="strava"') < response.text.index('value="apple_health"')
    assert response.text.index('value="strava"') < response.text.index('value="mi_fitness"')


def test_connections_page_hides_garmin_credentials_form_when_connected(app, client):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    response = client.get("/connections")

    assert 'action="/api/data-sources/garmin/connect"' not in response.text
    assert "Sync Now" in response.text
    assert "Disconnect Garmin" in response.text


def test_connections_page_shows_garmin_credentials_form_when_not_connected(client):
    _login(client)

    response = client.get("/connections")

    assert 'action="/api/data-sources/garmin/connect"' in response.text
    assert "Disconnect Garmin" not in response.text


def test_garmin_disconnect_route_requires_admin_login(client):
    response = client.post("/api/data-sources/garmin/disconnect", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_garmin_disconnect_route_clears_credentials_and_redirects_to_connections(app, client):
    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    assert app.state.credential_store.load() is not None

    response = client.post("/api/data-sources/garmin/disconnect", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/connections"
    assert app.state.credential_store.load() is None


def test_garmin_disconnect_route_leaves_synced_readings_untouched(app, client):
    from core.storage import repository
    from core.storage.db import connect
    from core.storage.models import MetricReading
    from datetime import date, datetime

    _login(client)
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    conn = connect(app.state.db_path)
    repository.upsert_readings(
        conn, [MetricReading(source="garmin", metric_type="steps", timestamp=datetime(2026, 1, 1), value=100, unit="count")]
    )
    conn.close()

    client.post("/api/data-sources/garmin/disconnect")

    conn = connect(app.state.db_path)
    readings = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))
    assert len(readings) == 1
