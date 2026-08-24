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
