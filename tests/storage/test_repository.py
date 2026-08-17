from datetime import date, datetime

import pytest

from core.storage import repository
from core.storage.db import connect
from core.storage.models import Activity, MetricReading


def test_upsert_and_get_readings_roundtrip(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [
        MetricReading(
            source="garmin",
            metric_type="resting_hr",
            timestamp=datetime(2026, 1, 1, 7, 0),
            value=52.0,
            unit="bpm",
        ),
        MetricReading(
            source="garmin",
            metric_type="resting_hr",
            timestamp=datetime(2026, 1, 2, 7, 0),
            value=54.0,
            unit="bpm",
        ),
    ]

    repository.upsert_readings(conn, readings)
    result = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date(2026, 1, 2))

    assert result == readings


def test_upsert_is_idempotent_and_updates_value(tmp_path):
    conn = connect(tmp_path / "test.db")
    original = MetricReading(
        source="garmin", metric_type="steps", timestamp=datetime(2026, 1, 1), value=1000.0, unit="count"
    )
    updated = MetricReading(
        source="garmin", metric_type="steps", timestamp=datetime(2026, 1, 1), value=1500.0, unit="count"
    )

    repository.upsert_readings(conn, [original])
    repository.upsert_readings(conn, [updated])
    result = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [updated]


def test_get_readings_excludes_other_metric_types_and_out_of_range_dates(tmp_path):
    conn = connect(tmp_path / "test.db")
    in_range = MetricReading("garmin", "steps", datetime(2026, 1, 5), 1000.0, "count")
    other_metric = MetricReading("garmin", "hrv", datetime(2026, 1, 5), 45.0, "ms")
    out_of_range = MetricReading("garmin", "steps", datetime(2026, 2, 1), 1000.0, "count")
    repository.upsert_readings(conn, [in_range, other_metric, out_of_range])

    result = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 31))

    assert result == [in_range]


def test_checkpoint_roundtrip_defaults_to_none(tmp_path):
    conn = connect(tmp_path / "test.db")

    assert repository.get_checkpoint(conn, "garmin", "resting_hr") is None

    repository.set_checkpoint(conn, "garmin", "resting_hr", date(2026, 1, 15))
    assert repository.get_checkpoint(conn, "garmin", "resting_hr") == date(2026, 1, 15)

    repository.set_checkpoint(conn, "garmin", "resting_hr", date(2026, 1, 20))
    assert repository.get_checkpoint(conn, "garmin", "resting_hr") == date(2026, 1, 20)


def test_list_metric_summaries_returns_aggregates(tmp_path):
    from core.storage.models import MetricSummary
    conn = connect(tmp_path / "test.db")
    readings = [
        MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 52.0, "bpm"),
        MetricReading("garmin", "resting_hr", datetime(2026, 1, 5), 54.0, "bpm"),
        MetricReading("garmin", "steps", datetime(2026, 1, 3), 8000.0, "count"),
    ]
    repository.upsert_readings(conn, readings)

    summaries = repository.list_metric_summaries(conn)

    assert summaries == [
        MetricSummary("resting_hr", date(2026, 1, 1), date(2026, 1, 5), 2, "bpm"),
        MetricSummary("steps", date(2026, 1, 3), date(2026, 1, 3), 1, "count"),
    ]


def test_save_and_get_report_roundtrip(tmp_path):
    from core.storage.models import Report
    conn = connect(tmp_path / "test.db")
    created = datetime(2026, 1, 15, 10, 0)
    rep_id = repository.save_report(conn, "Week 2 Review", "Strong consistency.", created)

    retrieved = repository.get_report(conn, rep_id)
    assert retrieved == Report(rep_id, created, "Week 2 Review", "Strong consistency.")
    assert repository.get_report(conn, 9999) is None


def test_target_crud_operations(tmp_path):
    from core.storage.models import Target
    conn = connect(tmp_path / "test.db")
    now = datetime(2026, 1, 1, 12, 0)
    target = Target(
        id="t-1",
        metric_type="steps",
        target_value=10000.0,
        operator="gte",
        target_window="daily",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status="active",
        notes="Daily step goal",
        created_at=now,
    )
    saved = repository.save_target(conn, target)
    assert saved == target

    assert repository.get_target_by_id(conn, "t-1") == target
    assert len(repository.get_targets(conn, status="active")) == 1
    assert len(repository.get_targets(conn, status="completed")) == 0

    assert repository.delete_target(conn, "t-1") is True
    assert repository.get_target_by_id(conn, "t-1") is None
    assert repository.delete_target(conn, "t-1") is False


def test_training_plan_crud_and_status_update(tmp_path):
    from core.storage.models import TrainingPlan
    conn = connect(tmp_path / "test.db")
    now = datetime(2026, 1, 1, 12, 0)
    plan = TrainingPlan(
        id="plan-1",
        title="Half Marathon Base",
        goal_description="Build aerobic base",
        start_date=date(2026, 2, 1),
        target_date=date(2026, 4, 30),
        plan_json='{"weeks": 12}',
        status="active",
        created_at=now,
    )
    repository.save_training_plan(conn, plan)

    assert repository.get_training_plan_by_id(conn, "plan-1") == plan
    assert len(repository.get_training_plans(conn, status="active")) == 1

    updated = repository.update_plan_status(conn, "plan-1", "completed")
    assert updated.status == "completed"
    assert repository.update_plan_status(conn, "non-existent", "completed") is None


def test_coach_note_save_and_retrieve(tmp_path):
    from core.storage.models import CoachNote
    conn = connect(tmp_path / "test.db")
    now = datetime(2026, 1, 1, 12, 0)
    note1 = CoachNote(
        id="n-1",
        date=date(2026, 1, 2),
        category="injury",
        note="Mild left Achilles tightness.",
        tags_json='["achilles", "recovery"]',
        created_at=now,
    )
    note2 = CoachNote(
        id="n-2",
        date=date(2026, 1, 3),
        category="nutrition",
        note="Carb loaded before long run.",
        tags_json=None,
        created_at=now,
    )
    repository.save_coach_note(conn, note1)
    repository.save_coach_note(conn, note2)

    all_notes = repository.get_coach_notes(conn, limit=10)
    assert len(all_notes) == 2
    assert all_notes[0].id == "n-2"  # Sorted by date DESC

    injury_notes = repository.get_coach_notes(conn, category="injury")
    assert len(injury_notes) == 1
    assert injury_notes[0].category == "injury"


def test_activity_upsert_and_retrieve(tmp_path):
    from core.storage.models import Activity
    conn = connect(tmp_path / "test.db")
    now = datetime(2026, 1, 1, 12, 0)
    act1 = Activity(
        id="garmin:101",
        source="garmin",
        activity_id="101",
        activity_name="Morning 5K",
        activity_type="running",
        sport_type="running",
        start_time=datetime(2026, 1, 2, 7, 0),
        duration_seconds=1500.0,
        distance_meters=5000.0,
        calories=350.0,
        avg_hr=152.0,
        max_hr=168.0,
        avg_speed=3.33,
        max_speed=4.1,
        elevation_gain=25.0,
        elevation_loss=25.0,
        created_at=now,
    )
    act2 = Activity(
        id="garmin:102",
        source="garmin",
        activity_id="102",
        activity_name="Afternoon Tempo Ride",
        activity_type="cycling",
        sport_type="cycling",
        start_time=datetime(2026, 1, 3, 14, 0),
        duration_seconds=3600.0,
        distance_meters=28000.0,
        calories=650.0,
        avg_hr=145.0,
        max_hr=165.0,
        avg_speed=7.78,
        max_speed=11.2,
        elevation_gain=120.0,
        elevation_loss=120.0,
        created_at=now,
    )
    repository.upsert_activities(conn, [act1, act2])

    all_acts = repository.get_activities(conn, limit=10)
    assert len(all_acts) == 2
    assert all_acts[0].id == "garmin:102"  # Sorted DESC by start_time

    runs = repository.get_activities(conn, activity_type="running")
    assert len(runs) == 1
    assert runs[0].activity_name == "Morning 5K"

    by_id = repository.get_activity_by_id(conn, "101")
    assert by_id is not None
    assert by_id.activity_id == "101"

    # Idempotent update
    updated_act1 = Activity(
        id="garmin:101",
        source="garmin",
        activity_id="101",
        activity_name="Morning 5K Tempo",
        activity_type="running",
        sport_type="running",
        start_time=datetime(2026, 1, 2, 7, 0),
        duration_seconds=1450.0,
        distance_meters=5000.0,
        calories=360.0,
        avg_hr=155.0,
        max_hr=170.0,
        avg_speed=3.45,
        max_speed=4.2,
        elevation_gain=25.0,
        elevation_loss=25.0,
        created_at=now,
    )
    repository.upsert_activities(conn, [updated_act1])
    retrieved = repository.get_activity_by_id(conn, "garmin:101")
    assert retrieved.activity_name == "Morning 5K Tempo"
    assert retrieved.duration_seconds == 1450.0

def test_get_source_priority_returns_none_when_unset(tmp_path):
    conn = connect(tmp_path / "test.db")
    assert repository.get_source_priority(conn, "resting_hr") is None


def test_set_and_get_source_priority_roundtrip(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_source_priority(conn, "resting_hr", "garmin")

    assert repository.get_source_priority(conn, "resting_hr") == "garmin"


def test_set_source_priority_overwrites_existing_value(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_source_priority(conn, "resting_hr", "garmin")
    repository.set_source_priority(conn, "resting_hr", "apple_health")

    assert repository.get_source_priority(conn, "resting_hr") == "apple_health"


def test_set_source_priority_rejects_unknown_source(tmp_path):
    conn = connect(tmp_path / "test.db")

    with pytest.raises(ValueError):
        repository.set_source_priority(conn, "resting_hr", "fitbit")


def test_get_readings_prefers_garmin_by_default_on_overlap(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_reading = MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 50.0, "bpm")
    apple_reading = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 1), 55.0, "bpm")
    repository.upsert_readings(conn, [garmin_reading, apple_reading])

    result = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [garmin_reading]


def test_get_readings_respects_explicit_source_priority_override(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_reading = MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 50.0, "bpm")
    apple_reading = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 1), 55.0, "bpm")
    repository.upsert_readings(conn, [garmin_reading, apple_reading])
    repository.set_source_priority(conn, "resting_hr", "apple_health")

    result = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [apple_reading]


def test_get_readings_uses_the_only_available_source_regardless_of_priority(tmp_path):
    conn = connect(tmp_path / "test.db")
    apple_only = MetricReading("apple_health", "steps", datetime(2026, 1, 1), 8000.0, "count")
    repository.upsert_readings(conn, [apple_only])

    result = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [apple_only]


def test_get_readings_reconciles_per_day_independently(tmp_path):
    conn = connect(tmp_path / "test.db")
    day1_garmin = MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 50.0, "bpm")
    day1_apple = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 1), 55.0, "bpm")
    day2_apple_only = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 2), 52.0, "bpm")
    repository.upsert_readings(conn, [day1_garmin, day1_apple, day2_apple_only])

    result = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date(2026, 1, 2))

    assert result == [day1_garmin, day2_apple_only]


def test_get_readings_keeps_multiple_same_source_readings_on_overlapping_day(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_morning = MetricReading("garmin", "steps", datetime(2026, 1, 1, 8, 0), 100.0, "count")
    garmin_evening = MetricReading("garmin", "steps", datetime(2026, 1, 1, 20, 0), 200.0, "count")
    apple_reading = MetricReading("apple_health", "steps", datetime(2026, 1, 1, 12, 0), 9000.0, "count")
    repository.upsert_readings(conn, [garmin_morning, garmin_evening, apple_reading])

    result = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [garmin_morning, garmin_evening]


def test_has_synced_data_false_when_no_checkpoint_for_source(tmp_path):
    conn = connect(tmp_path / "test.db")
    assert repository.has_synced_data(conn, "apple_health") is False


def test_has_synced_data_true_after_a_checkpoint_is_set(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_checkpoint(conn, "apple_health", "steps", date(2026, 1, 1))

    assert repository.has_synced_data(conn, "apple_health") is True


def test_has_synced_data_is_source_specific(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_checkpoint(conn, "garmin", "steps", date(2026, 1, 1))

    assert repository.has_synced_data(conn, "apple_health") is False
    assert repository.has_synced_data(conn, "garmin") is True


def _activity(source, activity_id, start_time, activity_type="running"):
    return Activity(
        id=f"{source}:{activity_id}",
        source=source,
        activity_id=activity_id,
        activity_name="Morning Run",
        activity_type=activity_type,
        sport_type="run",
        start_time=start_time,
        duration_seconds=1800.0,
        distance_meters=5000.0,
        calories=300.0,
        avg_hr=140.0,
        max_hr=160.0,
        avg_speed=2.8,
        max_speed=3.5,
        elevation_gain=20.0,
        elevation_loss=20.0,
        created_at=datetime(2026, 1, 1, 8, 0),
    )


def test_upsert_activities_skips_strava_duplicate_of_existing_garmin_activity(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_activity = _activity("garmin", "111", datetime(2026, 1, 1, 7, 0))
    repository.upsert_activities(conn, [garmin_activity])

    strava_duplicate = _activity("strava", "999", datetime(2026, 1, 1, 7, 2))  # 2 min later, same run
    inserted = repository.upsert_activities(conn, [strava_duplicate])

    assert inserted == 0
    all_activities = repository.get_activities(conn)
    assert len(all_activities) == 1
    assert all_activities[0].source == "garmin"


def test_upsert_activities_strava_first_then_garmin_supersedes(tmp_path):
    conn = connect(tmp_path / "test.db")
    strava_activity = _activity("strava", "999", datetime(2026, 1, 1, 7, 0))
    repository.upsert_activities(conn, [strava_activity])

    garmin_activity = _activity("garmin", "111", datetime(2026, 1, 1, 7, 1))  # 1 min later, same run
    inserted = repository.upsert_activities(conn, [garmin_activity])

    assert inserted == 1
    all_activities = repository.get_activities(conn)
    assert len(all_activities) == 1
    assert all_activities[0].source == "garmin"


def test_upsert_activities_different_activity_types_both_kept(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_run = _activity("garmin", "111", datetime(2026, 1, 1, 7, 0), activity_type="running")
    strava_ride = _activity("strava", "222", datetime(2026, 1, 1, 7, 2), activity_type="cycling")

    repository.upsert_activities(conn, [garmin_run])
    inserted = repository.upsert_activities(conn, [strava_ride])

    assert inserted == 1
    assert len(repository.get_activities(conn)) == 2


def test_upsert_activities_far_apart_in_time_both_kept(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_activity = _activity("garmin", "111", datetime(2026, 1, 1, 7, 0))
    strava_activity = _activity("strava", "222", datetime(2026, 1, 1, 9, 0))  # 2 hours later

    repository.upsert_activities(conn, [garmin_activity])
    inserted = repository.upsert_activities(conn, [strava_activity])

    assert inserted == 1
    assert len(repository.get_activities(conn)) == 2
