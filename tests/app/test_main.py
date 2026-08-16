import sqlite3

from app.db import ensure_app_schema
from app.main import create_app


def test_create_app_returns_fastapi_app_with_expected_state(tmp_path):
    app = create_app(tmp_path / "data")

    assert app.state.db_path == tmp_path / "data" / "athlytics.db"
    assert app.state.data_dir == tmp_path / "data"
    assert app.state.token_cache_dir == tmp_path / "data" / "garmin_tokens"


def test_create_app_creates_data_dir_and_db_file(tmp_path):
    data_dir = tmp_path / "data"
    create_app(data_dir)

    assert data_dir.exists()
    assert (data_dir / "athlytics.db").exists()


def test_create_app_applies_app_schema(tmp_path):
    data_dir = tmp_path / "data"
    create_app(data_dir)

    conn = sqlite3.connect(data_dir / "athlytics.db")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    conn.close()

    assert {"admin_user", "session", "app_setting", "sync_run_status", "sync_metric_status"} <= tables
    # core's own tables must also be present -- same file, not a second database
    assert {"metric_reading", "sync_checkpoint"} <= tables


def test_ensure_app_schema_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")

    ensure_app_schema(conn)
    ensure_app_schema(conn)  # must not raise

    conn.close()


def test_root_route_exists(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 303)
