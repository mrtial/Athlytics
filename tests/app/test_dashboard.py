from datetime import date, datetime, timedelta

import pytest

from app.db import ensure_app_schema
from app.widgets import build_dashboard_widgets, build_metric_detail
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


def test_build_metric_detail_returns_one_point_per_day_in_window(conn):
    readings = [_reading("resting_hr", d, 50.0 + d, "bpm") for d in (1, 3, 7)]
    repository.upsert_readings(conn, readings)

    detail = build_metric_detail(conn, "resting_hr", days=7, as_of=date(2026, 1, 7))

    assert detail["metric_type"] == "resting_hr"
    assert detail["unit"] == "bpm"
    assert [p["date"] for p in detail["points"]] == [
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
        "2026-01-05", "2026-01-06", "2026-01-07",
    ]
    assert detail["points"][0]["value"] == 51.0
    assert detail["points"][1]["value"] is None
    assert detail["points"][2]["value"] == 53.0


def test_build_metric_detail_averages_multiple_readings_on_same_day(conn):
    same_day = [
        MetricReading("garmin", "steps", datetime(2026, 1, 7, 8, 0), 100.0, "steps"),
        MetricReading("garmin", "steps", datetime(2026, 1, 7, 20, 0), 200.0, "steps"),
    ]
    repository.upsert_readings(conn, same_day)

    detail = build_metric_detail(conn, "steps", days=1, as_of=date(2026, 1, 7))

    assert detail["points"] == [{"date": "2026-01-07", "value": 150.0}]


def test_build_metric_detail_has_no_unit_when_no_readings_in_window(conn):
    detail = build_metric_detail(conn, "hrv", days=7, as_of=date(2026, 1, 7))

    assert detail["unit"] is None
    assert all(p["value"] is None for p in detail["points"])


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


def test_dashboard_shows_only_garmin_metrics_when_only_garmin_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "mindful_minutes" not in response.text  # Apple-only metric_type, not connected


def test_dashboard_shows_apple_only_metrics_when_only_apple_health_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    from core.storage import repository
    from core.storage.db import connect
    from datetime import date
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "mindful_minutes", date(2026, 1, 1))

    response = client.get("/dashboard")

    assert response.status_code == 200
    # training_load is Garmin-only per PERSONA_METRIC_TYPES["full_overview"]; must not appear.
    assert "training_load" not in response.text


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


def test_build_recent_activities_formats_correctly(conn):
    from core.storage.models import Activity
    from app.widgets import build_recent_activities

    act = Activity(
        id="garmin:201",
        source="garmin",
        activity_id="201",
        activity_name="Morning 10K",
        activity_type="running",
        sport_type="running",
        start_time=datetime(2026, 1, 10, 7, 30),
        duration_seconds=3000.0,
        distance_meters=10000.0,
        calories=720.0,
        avg_hr=156.0,
        max_hr=175.0,
        avg_speed=3.333,
        max_speed=4.2,
        elevation_gain=45.0,
        elevation_loss=45.0,
        created_at=datetime(2026, 1, 10, 9, 0),
    )
    repository.upsert_activities(conn, [act])

    formatted = build_recent_activities(conn, unit="km")
    assert len(formatted) == 1
    assert formatted[0]["name"] == "Morning 10K"
    assert formatted[0]["sport_label"] == "Running"
    assert formatted[0]["distance_formatted"] == "10.00 km"
    assert formatted[0]["duration_formatted"] == "50m"
    assert formatted[0]["pace_or_speed_val"] == "5:00 /km"
    assert formatted[0]["avg_hr"] == 156
    assert formatted[0]["calories"] == 720

    formatted_mi = build_recent_activities(conn, unit="mi")
    assert formatted_mi[0]["distance_formatted"] == "6.21 mi"
    assert formatted_mi[0]["pace_or_speed_val"] == "8:02 /mi"


def test_activities_route_requires_admin_and_renders(app, client):
    # Unauthenticated redirects
    res_unauth = client.get("/activities", follow_redirects=False)
    assert res_unauth.status_code == 303

    # Complete onboarding
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "dark"})
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    from core.storage.models import Activity
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    act = Activity(
        id="garmin:201",
        source="garmin",
        activity_id="201",
        activity_name="Morning 10K",
        activity_type="running",
        sport_type="running",
        start_time=datetime(2026, 1, 10, 7, 30),
        duration_seconds=3000.0,
        distance_meters=10000.0,
        calories=720.0,
        avg_hr=156.0,
        max_hr=175.0,
        avg_speed=3.333,
        max_speed=4.2,
        elevation_gain=45.0,
        elevation_loss=45.0,
        created_at=datetime(2026, 1, 10, 9, 0),
    )
    repository.upsert_activities(conn, [act])
    conn.close()

    res = client.get("/activities")
    assert res.status_code == 200
    assert "Morning 10K" in res.text
    assert "Running" in res.text
    assert "Workout Sessions" in res.text


