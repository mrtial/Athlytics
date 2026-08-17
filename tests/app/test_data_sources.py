import zipfile
from io import BytesIO

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


class _RouteMfaRequiredClient:
    def __init__(self, email, password, return_on_mfa=False):
        self.resume_login_calls = []
        self.client = _RouteFakeInnerClient()

    def login(self, tokenstore=None):
        return (True, {"pending": "state"})

    def resume_login(self, client_state, mfa_code):
        self.resume_login_calls.append((client_state, mfa_code))
        return (None, None)


class _RouteFakeInnerClient:
    def __init__(self):
        self.dump_calls = []

    def dump(self, path):
        self.dump_calls.append(path)


class _RouteMfaWrongCodeClient(_RouteMfaRequiredClient):
    def resume_login(self, client_state, mfa_code):
        from garminconnect import GarminConnectAuthenticationError

        raise GarminConnectAuthenticationError("invalid MFA code")


def test_connect_route_redirects_to_mfa_form_when_mfa_required(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    app.state.garmin_client_factory = _RouteMfaRequiredClient

    response = client.post(
        "/api/data-sources/garmin/connect",
        data={"email": "athlete@example.com", "password": "hunter2"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/connect/mfa"
    assert app.state.pending_garmin_mfa is not None
    assert app.state.pending_garmin_mfa["client_state"] == {"pending": "state"}


def test_mfa_form_get_redirects_to_connect_when_no_pending_challenge(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/connect/mfa", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/connect"


def test_mfa_form_get_renders_when_challenge_pending(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    app.state.pending_garmin_mfa = {"client": _RouteMfaRequiredClient("a", "b"), "client_state": {}}

    response = client.get("/onboarding/connect/mfa")

    assert response.status_code == 200
    assert "code" in response.text.lower()


def test_mfa_submit_completes_login_and_redirects_to_dashboard(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    stub_client = _RouteMfaRequiredClient("a", "b")
    app.state.pending_garmin_mfa = {"client": stub_client, "client_state": {"pending": "state"}}
    triggered = []
    app.state.sync_scheduler.trigger = lambda: triggered.append(True)

    response = client.post(
        "/api/data-sources/garmin/mfa", data={"mfa_code": "123456"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert stub_client.resume_login_calls == [({"pending": "state"}, "123456")]
    assert stub_client.client.dump_calls == [str(app.state.token_cache_dir)]
    assert app.state.pending_garmin_mfa is None
    assert triggered == [True]


def test_mfa_submit_with_no_pending_challenge_returns_400(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post("/api/data-sources/garmin/mfa", data={"mfa_code": "123456"})

    assert response.status_code == 400


def test_mfa_submit_with_wrong_code_returns_400_and_keeps_pending_state(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    stub_client = _RouteMfaWrongCodeClient("a", "b")
    app.state.pending_garmin_mfa = {"client": stub_client, "client_state": {"pending": "state"}}

    response = client.post("/api/data-sources/garmin/mfa", data={"mfa_code": "000000"})

    assert response.status_code == 400
    assert "mfa code" in response.json()["detail"].lower()
    assert app.state.pending_garmin_mfa is not None


def _apple_health_zip() -> bytes:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-01 08:00:00 -0500" endDate="2026-01-01 09:00:00 -0500" value="1200"/>
</HealthData>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml)
    return buf.getvalue()


def test_import_apple_health_upserts_readings_and_sets_checkpoint(tmp_path):
    from app.data_sources import import_apple_health
    from core.storage.db import connect
    from core.storage import repository
    from datetime import date

    conn = connect(tmp_path / "test.db")

    result = import_apple_health(conn, _apple_health_zip())

    assert result["steps"] == "imported: 1"
    readings = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))
    assert len(readings) == 1
    assert readings[0].value == 1200.0
    assert repository.has_synced_data(conn, "apple_health") is True


def test_apple_health_import_route_requires_admin_login(client):
    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", _apple_health_zip(), "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_apple_health_import_route_succeeds_and_returns_summary(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", _apple_health_zip(), "application/zip")},
    )

    assert response.status_code == 200
    assert response.json()["steps"] == "imported: 1"


def test_apple_health_import_route_returns_400_for_invalid_zip(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", b"not a zip", "application/zip")},
    )

    assert response.status_code == 400


def test_apple_health_import_route_returns_400_for_malformed_xml_inside_valid_zip(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", b"<HealthData><Record not closed")

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", buf.getvalue(), "application/zip")},
    )

    assert response.status_code == 400


def test_onboarding_connect_page_shows_both_provider_options(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/connect")

    assert response.status_code == 200
    assert "garmin" in response.text.lower()
    assert "apple health" in response.text.lower()
    assert 'action="/api/data-sources/apple-health/import"' in response.text


def test_completing_onboarding_via_apple_health_upload_reaches_dashboard(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", _apple_health_zip(), "application/zip")},
        follow_redirects=False,
    )
    assert response.status_code == 200  # import route returns a JSON summary, not a redirect

    root_response = client.get("/", follow_redirects=False)
    assert root_response.headers["location"] == "/dashboard"
