from app.auth import create_admin_without_password
from app.db import ensure_app_schema
from core.storage.db import connect


def _session_count(app) -> int:
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    try:
        return conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
    finally:
        conn.close()


def test_healthz_returns_200_without_auth(client):
    response = client.get("/healthz", follow_redirects=False)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_never_creates_sessions_for_a_passwordless_admin(app, client):
    # Regression: the Docker HEALTHCHECK used to hit GET /login, which for a
    # passwordless admin issues a fresh session on every probe (~14k/day).
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    create_admin_without_password(conn)
    conn.close()

    for _ in range(5):
        assert client.get("/healthz", follow_redirects=False).status_code == 200

    assert _session_count(app) == 0
