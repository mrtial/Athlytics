from datetime import datetime

from app.db import ensure_app_schema
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading


def _login(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})


def test_metric_detail_route_requires_admin_login(client):
    response = client.get("/api/metric-detail/resting_hr")

    assert response.status_code == 401


def test_metric_detail_route_rejects_unknown_metric_type(client):
    _login(client)

    response = client.get("/api/metric-detail/not_a_real_metric")

    assert response.status_code == 404


def test_metric_detail_route_returns_seven_days_of_points(app, client):
    _login(client)
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    repository.upsert_readings(conn, [MetricReading("garmin", "hrv", datetime(2026, 1, 7, 6, 0), 42.0, "ms")])
    conn.close()

    response = client.get("/api/metric-detail/hrv?as_of=2026-01-07")

    assert response.status_code == 200
    body = response.json()
    assert body["metric_type"] == "hrv"
    assert body["unit"] == "ms"
    assert len(body["points"]) == 7
    assert body["points"][-1] == {"date": "2026-01-07", "value": 42.0}


def test_metric_detail_route_accepts_thirty_day_range(app, client):
    _login(client)
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    repository.upsert_readings(conn, [MetricReading("garmin", "hrv", datetime(2026, 1, 7, 6, 0), 42.0, "ms")])
    conn.close()

    response = client.get("/api/metric-detail/hrv?as_of=2026-01-07&days=30")

    assert response.status_code == 200
    assert len(response.json()["points"]) == 30


def test_metric_detail_route_rejects_unsupported_day_range(client):
    _login(client)

    response = client.get("/api/metric-detail/hrv?days=14")

    assert response.status_code == 400
