import httpx
import pytest


def _admin_client(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    return client


def test_authorize_redirects_to_strava_with_client_id(client):
    _admin_client(client)

    response = client.post(
        "/oauth/strava/authorize",
        data={"client_id": "12345", "client_secret": "shh"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("https://www.strava.com/oauth/authorize")
    assert "client_id=12345" in location
    assert "scope=read%2Cactivity%3Aread_all" in location or "activity" in location


def test_callback_exchanges_code_and_saves_credentials(app, client):
    _admin_client(client)
    client.post("/oauth/strava/authorize", data={"client_id": "12345", "client_secret": "shh"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "token_type": "Bearer", "access_token": "tok-abc", "refresh_token": "ref-xyz",
            "expires_at": 9999999999, "expires_in": 21600,
        })

    app.state.strava_http_client_factory = lambda: httpx.Client(
        base_url="https://www.strava.com", transport=httpx.MockTransport(handler)
    )

    response = client.get("/oauth/strava/callback", params={"code": "auth-code"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    saved = app.state.strava_credential_store.load()
    assert saved["access_token"] == "tok-abc"
    assert saved["client_id"] == "12345"


def test_callback_without_pending_oauth_state_fails(client):
    _admin_client(client)

    response = client.get("/oauth/strava/callback", params={"code": "auth-code"})

    assert response.status_code == 400


def test_callback_with_strava_error_param_fails(client):
    _admin_client(client)
    client.post("/oauth/strava/authorize", data={"client_id": "12345", "client_secret": "shh"})

    response = client.get("/oauth/strava/callback", params={"error": "access_denied"})

    assert response.status_code == 400
