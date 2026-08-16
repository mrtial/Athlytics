import pytest
from cryptography.fernet import Fernet

from app.data_sources import SUPPORTED_PROVIDERS, connect_garmin
from core.providers.garmin import GarminAuthError
from core.security.credentials import CredentialStore


class _StubGarminClient:
    def __init__(self, email, password, return_on_mfa=False):
        self.email = email
        self.password = password

    def login(self, tokenstore=None):
        return (False, None)


class _LoginFailsClient(_StubGarminClient):
    def login(self, tokenstore=None):
        from garminconnect import GarminConnectAuthenticationError

        raise GarminConnectAuthenticationError("bad credentials")


def test_supported_providers_contains_only_garmin():
    assert SUPPORTED_PROVIDERS == {"garmin"}


def test_connect_garmin_saves_credentials_and_succeeds_with_valid_login(tmp_path):
    credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")

    connect_garmin(
        credential_store, tmp_path / "tokens", "athlete@example.com", "hunter2",
        garmin_client_factory=_StubGarminClient,
    )

    assert credential_store.load() == {"email": "athlete@example.com", "password": "hunter2"}


def test_connect_garmin_raises_garmin_auth_error_on_bad_credentials(tmp_path):
    credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")

    with pytest.raises(GarminAuthError):
        connect_garmin(
            credential_store, tmp_path / "tokens", "athlete@example.com", "wrong",
            garmin_client_factory=_LoginFailsClient,
        )


def test_connect_garmin_leaves_credentials_saved_after_failed_validation(tmp_path):
    credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")

    with pytest.raises(GarminAuthError):
        connect_garmin(
            credential_store, tmp_path / "tokens", "athlete@example.com", "wrong",
            garmin_client_factory=_LoginFailsClient,
        )

    # Credentials remain saved so a retry doesn't require re-entering the
    # still-correct email -- only the outcome (raised GarminAuthError) tells
    # the caller not to treat the connection as successful.
    assert credential_store.load() == {"email": "athlete@example.com", "password": "wrong"}


def test_connect_route_returns_404_for_unsupported_provider(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple_health/connect", data={"email": "a@example.com", "password": "x"}
    )

    assert response.status_code == 404


def test_connect_route_requires_admin_login(client):
    response = client.post(
        "/api/data-sources/garmin/connect", data={"email": "a@example.com", "password": "x"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_onboarding_connect_get_requires_admin_login(client):
    response = client.get("/onboarding/connect", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_onboarding_connect_get_renders_form_when_logged_in(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/connect")

    assert response.status_code == 200
    assert "garmin" in response.text.lower()


class _RouteStubGarminClient:
    def __init__(self, email, password, return_on_mfa=False):
        pass

    def login(self, tokenstore=None):
        return (False, None)


class _RouteLoginFailsClient(_RouteStubGarminClient):
    def login(self, tokenstore=None):
        from garminconnect import GarminConnectAuthenticationError

        raise GarminConnectAuthenticationError("bad credentials")


def test_connect_route_succeeds_and_triggers_scheduler(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    app.state.garmin_client_factory = _RouteStubGarminClient
    triggered = []
    app.state.sync_scheduler.trigger = lambda: triggered.append(True)

    response = client.post(
        "/api/data-sources/garmin/connect",
        data={"email": "athlete@example.com", "password": "hunter2"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert triggered == [True]
    assert app.state.credential_store.load() == {"email": "athlete@example.com", "password": "hunter2"}


def test_connect_route_returns_400_with_message_on_bad_credentials(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    app.state.garmin_client_factory = _RouteLoginFailsClient

    response = client.post(
        "/api/data-sources/garmin/connect", data={"email": "athlete@example.com", "password": "wrong"}
    )

    assert response.status_code == 400
    assert "authentication failed" in response.json()["detail"].lower()
