from datetime import datetime, timezone

import pytest

from core.storage.models import Activity, MetricReading


def test_naive_timestamp_is_accepted():
    reading = MetricReading("garmin", "steps", datetime(2026, 1, 1, 0, 0), 1000.0, "count")

    assert reading.timestamp == datetime(2026, 1, 1, 0, 0)


def test_aware_timestamp_is_rejected():
    aware = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="naive datetime"):
        MetricReading("garmin", "steps", aware, 1000.0, "count")


def test_activity_naive_timestamps_accepted():
    act = Activity(
        id="garmin:101",
        source="garmin",
        activity_id="101",
        activity_name="Morning Run",
        activity_type="running",
        sport_type="running",
        start_time=datetime(2026, 1, 1, 8, 30),
        duration_seconds=1800.0,
        distance_meters=5000.0,
        calories=350.0,
        avg_hr=150.0,
        max_hr=170.0,
        avg_speed=2.78,
        max_speed=3.5,
        elevation_gain=30.0,
        elevation_loss=30.0,
        created_at=datetime(2026, 1, 1, 9, 0),
    )
    assert act.id == "garmin:101"
    assert act.start_time == datetime(2026, 1, 1, 8, 30)


def test_activity_aware_timestamp_rejected():
    aware = datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    naive = datetime(2026, 1, 1, 9, 0)
    with pytest.raises(ValueError, match="naive datetime"):
        Activity(
            id="garmin:101",
            source="garmin",
            activity_id="101",
            activity_name="Morning Run",
            activity_type="running",
            sport_type="running",
            start_time=aware,
            duration_seconds=1800.0,
            distance_meters=5000.0,
            calories=350.0,
            avg_hr=150.0,
            max_hr=170.0,
            avg_speed=2.78,
            max_speed=3.5,
            elevation_gain=30.0,
            elevation_loss=30.0,
            created_at=naive,
        )

