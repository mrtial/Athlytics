from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.auth import create_admin
from app.db import ensure_app_schema
from app.dependencies import onboarding_status, require_admin_api, require_admin_page
from app.session import SESSION_LIFETIME, create_session, delete_session, is_valid_session
from app.settings import set_api_token
from core.storage.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    ensure_app_schema(c)
    return c


def test_create_session_returns_a_token_that_is_valid(conn):
    token = create_session(conn)

    assert isinstance(token, str)
    assert len(token) > 20
    assert is_valid_session(conn, token) is True


def test_is_valid_session_false_for_unknown_token(conn):
    assert is_valid_session(conn, "not-a-real-token") is False


def test_is_valid_session_false_for_expired_token(conn):
    token = create_session(conn)
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn.execute("UPDATE session SET expires_at = ? WHERE token = ?", (expired.isoformat(), token))
    conn.commit()

    assert is_valid_session(conn, token) is False


def test_delete_session_invalidates_it(conn):
    token = create_session(conn)

    delete_session(conn, token)

    assert is_valid_session(conn, token) is False


def test_two_sessions_get_distinct_tokens(conn):
    assert create_session(conn) != create_session(conn)


def test_session_lifetime_is_thirty_days():
    assert SESSION_LIFETIME == timedelta(days=30)


def _tiny_app(data_dir):
    from app.main import create_app

    test_app = create_app(data_dir)

    @test_app.get("/protected-page")
    def protected_page(conn=Depends(require_admin_page)):
        return {"ok": True}

    @test_app.get("/protected-api")
    def protected_api(conn=Depends(require_admin_api)):
        return {"ok": True}

    return test_app


def test_require_admin_page_redirects_to_login_when_unauthenticated(tmp_path):
    client = TestClient(_tiny_app(tmp_path / "data"))

    response = client.get("/protected-page", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_require_admin_api_returns_401_when_unauthenticated(tmp_path):
    client = TestClient(_tiny_app(tmp_path / "data"))

    response = client.get("/protected-api")

    assert response.status_code == 401


def test_require_admin_page_allows_valid_session_cookie(tmp_path):
    app = _tiny_app(tmp_path / "data")
    client = TestClient(app)
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    create_admin(conn, "athlete", "hunter2hunter2")
    token = create_session(conn)
    conn.close()
    client.cookies.set("athlytics_session", token)

    response = client.get("/protected-page")

    assert response.status_code == 200


def test_require_admin_api_allows_valid_bearer_token_without_cookie(tmp_path):
    app = _tiny_app(tmp_path / "data")
    client = TestClient(app)
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    set_api_token(conn, "shortcut-token")
    conn.close()

    response = client.get("/protected-api", headers={"Authorization": "Bearer shortcut-token"})

    assert response.status_code == 200


def test_require_admin_api_rejects_wrong_bearer_token(tmp_path):
    app = _tiny_app(tmp_path / "data")
    client = TestClient(app)
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    set_api_token(conn, "shortcut-token")
    conn.close()

    response = client.get("/protected-api", headers={"Authorization": "Bearer wrong-token"})

    assert response.status_code == 401


def test_onboarding_status_progresses_admin_then_profile_then_persona(tmp_path):
    from types import SimpleNamespace

    from app.settings import set_athlete_profile
    from core.security.credentials import CredentialStore
    from cryptography.fernet import Fernet

    conn = connect(tmp_path / "test.db")
    ensure_app_schema(conn)
    credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    strava_credential_store = CredentialStore(Fernet.generate_key(), tmp_path / "strava.enc")
    state = SimpleNamespace(credential_store=credential_store, strava_credential_store=strava_credential_store)

    assert onboarding_status(conn, state) == "admin"

    create_admin(conn, "athlete", "hunter2hunter2")
    assert onboarding_status(conn, state) == "profile"

    set_athlete_profile(conn, "Athlete Name", "1995-06-15")
    assert onboarding_status(conn, state) == "persona"
