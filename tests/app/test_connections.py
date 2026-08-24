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
