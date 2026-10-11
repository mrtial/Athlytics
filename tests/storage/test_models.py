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



def _base_activity_kwargs(**overrides):
    kwargs = dict(
        id="garmin:101", source="garmin", activity_id="101", activity_name="Run",
        activity_type="running", sport_type="running", start_time=datetime(2026, 1, 1, 13, 17),
        duration_seconds=4223.7, distance_meters=11813.9, calories=862.0, avg_hr=150.0,
        max_hr=176.0, avg_speed=2.8, max_speed=4.1, elevation_gain=69.0, elevation_loss=88.0,
        created_at=datetime(2026, 1, 2, 0, 0),
    )
    kwargs.update(overrides)
    return kwargs


def test_activity_detail_fields_default_to_none_so_other_providers_need_no_change():
    act = Activity(**_base_activity_kwargs())

    for field in (
        "start_time_local", "utc_offset_minutes", "moving_duration_seconds",
        "hr_zone_1_s", "hr_zone_2_s", "hr_zone_3_s", "hr_zone_4_s", "hr_zone_5_s",
        "aerobic_training_effect", "anaerobic_training_effect", "training_effect_label",
        "activity_training_load", "avg_cadence", "avg_power", "norm_power",
    ):
        assert getattr(act, field) is None, field


def test_activity_start_time_local_must_be_naive():
    aware = datetime(2026, 1, 1, 9, 17, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="start_time_local must be a naive datetime"):
        Activity(**_base_activity_kwargs(start_time_local=aware))
