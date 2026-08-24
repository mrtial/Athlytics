from app.auth import admin_exists
from app.db import ensure_app_schema
from app.session import SESSION_COOKIE_NAME, is_valid_session
from core.storage.db import connect


def test_onboarding_admin_get_renders_form(client):
    response = client.get("/onboarding/admin")

    assert response.status_code == 200
    assert "username" in response.text.lower()


def test_onboarding_admin_post_creates_admin_and_logs_in(app, client):
    response = client.post(
        "/onboarding/admin",
        data={"username": "athlete", "password": "hunter2hunter2"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/profile"

    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    assert admin_exists(conn) is True

    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    assert is_valid_session(conn, token) is True


def test_onboarding_admin_post_rejects_when_admin_already_exists(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/onboarding/admin", data={"username": "someone_else", "password": "another_password"}
    )

    assert response.status_code == 400


def test_login_get_renders_form(client):
    response = client.get("/login")

    assert response.status_code == 200


def test_login_post_with_correct_credentials_creates_session_and_redirects(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.cookies.clear()

    response = client.post(
        "/login", data={"username": "athlete", "password": "hunter2hunter2"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert client.cookies.get(SESSION_COOKIE_NAME) is not None


def test_login_post_with_wrong_password_returns_401_with_error(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.cookies.clear()

    response = client.post("/login", data={"username": "athlete", "password": "wrong"})

    assert response.status_code == 401
    assert "error" in response.text.lower() or "invalid" in response.text.lower()


def test_logout_deletes_session_and_redirects_to_login(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    token = client.cookies.get(SESSION_COOKIE_NAME)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    assert is_valid_session(conn, token) is False
