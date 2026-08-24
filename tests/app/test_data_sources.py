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


def test_supported_providers_contains_only_credentials_form_providers():
    from core.providers.registry import PROVIDER_REGISTRY

    expected = {p.id for p in PROVIDER_REGISTRY if p.flow_type == "credentials_form"}
    assert SUPPORTED_PROVIDERS == expected == {"garmin"}


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

    assert response.status_code == 401


def test_apple_health_import_route_accepts_bearer_token_without_cookie(app, client):
    from app.settings import set_api_token
    from core.storage.db import connect

    conn = connect(app.state.db_path)
    from app.db import ensure_app_schema

    ensure_app_schema(conn)
    set_api_token(conn, "shortcut-token")
    conn.close()

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", _apple_health_zip(), "application/zip")},
        headers={"Authorization": "Bearer shortcut-token"},
    )

    assert response.status_code == 200
    assert response.json()["steps"] == "imported: 1"


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


def test_apple_health_import_route_returns_400_for_record_missing_value_attribute(client):
    # A mapped record type missing its "value" attribute triggers
    # float(None) -> TypeError deep in the provider; the route must
    # translate that into a 400, not let it surface as an unhandled 500.
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-01 08:00:00 -0500" endDate="2026-01-01 09:00:00 -0500"/>
</HealthData>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml)

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", buf.getvalue(), "application/zip")},
    )

    assert response.status_code == 400


def test_apple_health_import_route_returns_400_when_no_recognized_records(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierUVExposure" sourceName="iPhone" unit="count" startDate="2026-01-01 08:00:00 -0500" endDate="2026-01-01 09:00:00 -0500" value="3"/>
</HealthData>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml)

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", buf.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "no recognized" in response.json()["detail"].lower()


def test_import_apple_health_checkpoint_does_not_regress_on_older_reupload(tmp_path):
    from app.data_sources import import_apple_health
    from core.storage.db import connect
    from core.storage import repository
    from datetime import date

    conn = connect(tmp_path / "test.db")

    later_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-10 08:00:00 -0500" endDate="2026-01-10 09:00:00 -0500" value="1000"/>
</HealthData>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", later_xml)
    import_apple_health(conn, buf.getvalue())

    assert repository.get_checkpoint(conn, "apple_health", "steps") == date(2026, 1, 10)

    earlier_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-01 08:00:00 -0500" endDate="2026-01-01 09:00:00 -0500" value="500"/>
</HealthData>"""
    buf2 = BytesIO()
    with zipfile.ZipFile(buf2, "w") as zf:
        zf.writestr("apple_health_export/export.xml", earlier_xml)
    import_apple_health(conn, buf2.getvalue())

    # Checkpoint must stay at the later date, not regress to the older upload's date.
    assert repository.get_checkpoint(conn, "apple_health", "steps") == date(2026, 1, 10)


def test_ingest_apple_health_metrics_upserts_readings_with_looked_up_units_and_sets_checkpoint(tmp_path):
    from app.data_sources import ingest_apple_health_metrics
    from core.storage.db import connect
    from core.storage import repository
    from datetime import date

    conn = connect(tmp_path / "test.db")

    result = ingest_apple_health_metrics(conn, date(2026, 1, 1), {"steps": 8432, "resting_hr": 58})

    assert result == {"imported": {"steps": "imported: 1", "resting_hr": "imported: 1"}, "skipped": []}
    readings = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))
    assert len(readings) == 1
    assert readings[0].value == 8432.0
    assert readings[0].unit == "count"
    assert repository.get_checkpoint(conn, "apple_health", "steps") == date(2026, 1, 1)
    assert repository.get_checkpoint(conn, "apple_health", "resting_hr") == date(2026, 1, 1)


def test_ingest_apple_health_metrics_skips_unrecognized_keys_but_imports_the_rest(tmp_path):
    from app.data_sources import ingest_apple_health_metrics
    from core.storage.db import connect
    from datetime import date

    conn = connect(tmp_path / "test.db")

    result = ingest_apple_health_metrics(conn, date(2026, 1, 1), {"steps": 100, "not_a_real_metric": 1})

    assert result["imported"] == {"steps": "imported: 1"}
    assert result["skipped"] == ["not_a_real_metric"]


def test_apple_health_metrics_route_requires_admin_login(client):
    response = client.post(
        "/api/data-sources/apple-health/metrics",
        json={"readings": {"steps": 100}},
    )

    assert response.status_code == 401


def test_apple_health_metrics_route_accepts_bearer_token_without_cookie(app, client):
    from app.settings import set_api_token
    from core.storage.db import connect
    from app.db import ensure_app_schema

    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    set_api_token(conn, "shortcut-token")
    conn.close()

    response = client.post(
        "/api/data-sources/apple-health/metrics",
        json={"readings": {"steps": 100}},
        headers={"Authorization": "Bearer shortcut-token"},
    )

    assert response.status_code == 200
    assert response.json()["imported"] == {"steps": "imported: 1"}


def test_apple_health_metrics_route_succeeds_and_returns_summary(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/metrics",
        json={"date": "2026-01-01", "readings": {"steps": 8432, "sleep_duration": 7.5}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == {"steps": "imported: 1", "sleep_duration": "imported: 1"}
    assert body["skipped"] == []


def test_apple_health_metrics_route_defaults_date_to_today(client):
    from datetime import date

    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/metrics",
        json={"readings": {"steps": 100}},
    )

    assert response.status_code == 200
    assert response.json()["imported"] == {"steps": "imported: 1"}


def test_apple_health_metrics_route_returns_400_for_empty_readings(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/metrics",
        json={"readings": {}},
    )

    assert response.status_code == 400


def test_apple_health_metrics_route_returns_400_when_no_recognized_keys(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/metrics",
        json={"readings": {"not_a_real_metric": 1}},
    )

    assert response.status_code == 400
    assert "not_a_real_metric" in response.json()["detail"]


def test_apple_health_metrics_route_reports_partial_skip_with_200(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/metrics",
        json={"readings": {"steps": 100, "not_a_real_metric": 1}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == {"steps": "imported: 1"}
    assert body["skipped"] == ["not_a_real_metric"]


def test_app_exposes_strava_credential_store(app):
    assert app.state.strava_credential_store is not None
    assert app.state.strava_credential_store.load() is None  # nothing connected yet


def test_strava_credential_store_is_independent_of_garmin_store(app, tmp_path):
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})
    app.state.strava_credential_store.save({"client_id": "1", "client_secret": "s", "access_token": "a", "refresh_token": "r", "expires_at": "9999999999"})

    assert app.state.credential_store.load()["email"] == "a@example.com"
    assert app.state.strava_credential_store.load()["client_id"] == "1"


def test_onboarding_connect_page_shows_both_provider_options(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/connect")

    assert response.status_code == 200
    assert "garmin" in response.text.lower()
    assert "apple health" in response.text.lower()
    assert 'action="/api/data-sources/apple-health/import"' in response.text


def test_completing_onboarding_via_apple_health_upload_reaches_dashboard(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/profile", data={"athlete_name": "Athlete Name", "athlete_dob": "1995-06-15"})
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


def test_onboarding_connect_page_shows_strava_option(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/connect")

    assert response.status_code == 200
    assert "strava" in response.text.lower()
    assert 'action="/oauth/strava/authorize"' in response.text


def test_onboarding_connect_page_shows_mi_fitness_qr_login_section(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/connect")

    assert response.status_code == 200
    assert "mi fitness" in response.text.lower()
    assert "data-mi-fitness-start" in response.text


def test_mi_fitness_connect_route_requires_admin_login(client):
    response = client.post("/api/data-sources/mi-fitness/connect", follow_redirects=False)

    assert response.status_code == 303


def test_mi_fitness_status_route_reports_not_started_before_any_connect_attempt(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/api/data-sources/mi-fitness/status")

    assert response.json() == {"status": "not_started", "qr_image_url": None, "error": None}


def test_mi_fitness_connect_route_starts_background_login(app, client, monkeypatch):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    started = {}

    def fake_start_mi_fitness_login(pending, credential_store, **kwargs):
        started["pending"] = pending
        started["credential_store"] = credential_store
        pending.status = "qr_ready"
        pending.qr_image_url = "https://example.com/qr.png"
        import threading

        return threading.Thread(target=lambda: None)

    monkeypatch.setattr("app.routes.data_sources.start_mi_fitness_login", fake_start_mi_fitness_login)

    response = client.post("/api/data-sources/mi-fitness/connect")

    assert response.status_code == 200
    assert response.json() == {"status": "started"}
    assert started["credential_store"] is app.state.mi_fitness_credential_store

    status_response = client.get("/api/data-sources/mi-fitness/status")
    assert status_response.json() == {"status": "qr_ready", "qr_image_url": "https://example.com/qr.png", "error": None}
