from datetime import date, datetime, timedelta

import pytest

from app.db import ensure_app_schema
from app.widgets import build_dashboard_widgets
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading


def _reading(metric_type: str, day_number: int, value: float, unit: str) -> MetricReading:
    timestamp = datetime(2026, 1, 1) + timedelta(days=day_number - 1)
    return MetricReading("garmin", metric_type, timestamp, value, unit)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    ensure_app_schema(c)
    return c


def test_build_dashboard_widgets_returns_a_trend_per_metric_type(conn):
    readings = [_reading("resting_hr", d, 50.0 + d, "bpm") for d in range(1, 8)]
    repository.upsert_readings(conn, readings)

    widgets = build_dashboard_widgets(conn, ["resting_hr"], as_of=date(2026, 1, 7))

    assert "resting_hr" in widgets["trends"]
    trend = widgets["trends"]["resting_hr"]
    assert trend.metric_type == "resting_hr"
    assert trend.window_days == 7


def test_build_dashboard_widgets_handles_metric_type_with_no_data(conn):
    widgets = build_dashboard_widgets(conn, ["hrv"], as_of=date(2026, 1, 7))

    assert widgets["trends"]["hrv"].current.average is None


def test_build_dashboard_widgets_includes_anomalies_across_requested_metric_types(conn):
    values = [50.0] * 9 + [70.0]
    readings = [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 11), values)]
    repository.upsert_readings(conn, readings)

    widgets = build_dashboard_widgets(conn, ["resting_hr"], as_of=date(2026, 1, 10))

    assert len(widgets["anomalies"]) == 1
    assert widgets["anomalies"][0].metric_type == "resting_hr"


def test_build_dashboard_widgets_returns_empty_anomalies_for_empty_metric_list(conn):
    widgets = build_dashboard_widgets(conn, [], as_of=date(2026, 1, 10))

    assert widgets["trends"] == {}
    assert widgets["anomalies"] == []


def test_dashboard_route_requires_admin_login(client):
    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_route_redirects_to_persona_step_when_onboarding_incomplete(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/persona"


def test_dashboard_route_renders_widgets_for_completed_onboarding(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "sleep_recovery_focus"})
    client.post("/onboarding/theme", data={"theme": "dark"})
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    repository.upsert_readings(conn, [_reading("sleep_score", 1, 82.0, "score")])
    conn.close()
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "sleep_score" in response.text.lower() or "sleep" in response.text.lower()
