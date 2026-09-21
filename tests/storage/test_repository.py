from datetime import date, datetime, timezone
import sqlite3

import pytest

from core.storage import repository
from core.storage.db import connect
from core.storage.models import Activity, MetricReading, StrengthSet, SleepSession, SleepStageSegment, SleepRestlessMoment


def test_sleep_tables_exist(tmp_path):
    conn = connect(tmp_path / "test.db")
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"sleep_session", "sleep_stage_segment", "sleep_restless_moment"} <= tables


def test_sleep_session_rejects_timezone_aware_sleep_start_utc():
    with pytest.raises(ValueError, match="sleep_start_utc must be a naive datetime"):
        SleepSession(
            id="s-1",
            calendar_date=date(2026, 1, 1),
            sleep_start_utc=datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc),
            sleep_end_utc=None,
            sleep_start_local=None,
            sleep_end_local=None,
            total_sleep_seconds=None,
            nap_time_seconds=None,
            deep_sleep_seconds=None,
            light_sleep_seconds=None,
            rem_sleep_seconds=None,
            awake_sleep_seconds=None,
            unmeasurable_sleep_seconds=None,
            awake_count=None,
            restless_moments_count=None,
            avg_sleep_stress=None,
            avg_heart_rate=None,
            avg_overnight_hrv=None,
            avg_respiration=None,
            lowest_respiration=None,
            highest_respiration=None,
            overall_score=None,
            overall_score_qualifier=None,
            duration_qualifier=None,
            stress_qualifier=None,
            awake_count_qualifier=None,
            restlessness_qualifier=None,
            rem_percentage=None,
            rem_percentage_qualifier=None,
            light_percentage=None,
            light_percentage_qualifier=None,
            deep_percentage=None,
            deep_percentage_qualifier=None,
            sleep_need_baseline_minutes=None,
            sleep_need_actual_minutes=None,
            sleep_need_feedback=None,
            score_feedback=None,
            score_insight=None,
            score_personalized_insight=None,
            created_at=datetime(2026, 1, 2, 12, 0),
        )


def test_sleep_session_rejects_timezone_aware_created_at():
    with pytest.raises(ValueError, match="created_at must be a naive datetime"):
        SleepSession(
            id="s-1",
            calendar_date=date(2026, 1, 1),
            sleep_start_utc=None,
            sleep_end_utc=None,
            sleep_start_local=None,
            sleep_end_local=None,
            total_sleep_seconds=None,
            nap_time_seconds=None,
            deep_sleep_seconds=None,
            light_sleep_seconds=None,
            rem_sleep_seconds=None,
            awake_sleep_seconds=None,
            unmeasurable_sleep_seconds=None,
            awake_count=None,
            restless_moments_count=None,
            avg_sleep_stress=None,
            avg_heart_rate=None,
            avg_overnight_hrv=None,
            avg_respiration=None,
            lowest_respiration=None,
            highest_respiration=None,
            overall_score=None,
            overall_score_qualifier=None,
            duration_qualifier=None,
            stress_qualifier=None,
            awake_count_qualifier=None,
            restlessness_qualifier=None,
            rem_percentage=None,
            rem_percentage_qualifier=None,
            light_percentage=None,
            light_percentage_qualifier=None,
            deep_percentage=None,
            deep_percentage_qualifier=None,
            sleep_need_baseline_minutes=None,
            sleep_need_actual_minutes=None,
            sleep_need_feedback=None,
            score_feedback=None,
            score_insight=None,
            score_personalized_insight=None,
            created_at=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
        )


def test_sleep_stage_segment_rejects_timezone_aware_start_utc():
    with pytest.raises(ValueError, match="start_utc must be a naive datetime"):
        SleepStageSegment(
            id="seg-1",
            sleep_session_id="s-1",
            segment_index=0,
            stage="deep",
            start_utc=datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc),
            end_utc=datetime(2026, 1, 1, 23, 0),
            duration_seconds=3600.0,
        )


def test_sleep_stage_segment_rejects_timezone_aware_end_utc():
    with pytest.raises(ValueError, match="end_utc must be a naive datetime"):
        SleepStageSegment(
            id="seg-1",
            sleep_session_id="s-1",
            segment_index=0,
            stage="deep",
            start_utc=datetime(2026, 1, 1, 22, 0),
            end_utc=datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc),
            duration_seconds=3600.0,
        )


def test_sleep_restless_moment_rejects_timezone_aware_occurred_at_utc():
    with pytest.raises(ValueError, match="occurred_at_utc must be a naive datetime"):
        SleepRestlessMoment(
            sleep_session_id="s-1",
            occurred_at_utc=datetime(2026, 1, 1, 22, 30, tzinfo=timezone.utc),
            value=1,
        )


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


def test_has_activities_from_source_false_when_none_stored(tmp_path):
    conn = connect(tmp_path / "test.db")
    assert repository.has_activities_from_source(conn, "strava") is False


def test_has_activities_from_source_true_after_upsert(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.upsert_activities(conn, [_activity("strava", "999", datetime(2026, 1, 1, 7, 0))])

    assert repository.has_activities_from_source(conn, "strava") is True


def test_has_activities_from_source_is_source_specific(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.upsert_activities(conn, [_activity("garmin", "111", datetime(2026, 1, 1, 7, 0))])

    assert repository.has_activities_from_source(conn, "strava") is False
    assert repository.has_activities_from_source(conn, "garmin") is True


def _strength_set(
    activity_id,
    set_index,
    movement_id="mv-bench",
    created_at=datetime(2026, 1, 1, 12, 0),
    occurred_at=None,
):
    return StrengthSet(
        id=f"tonal:{activity_id}:{set_index}",
        activity_id=activity_id,
        movement_id=movement_id,
        movement_name="Bench Press",
        set_index=set_index,
        is_warm_up=False,
        reps=10,
        weight_lbs=135.0,
        volume_lbs=1350.0,
        one_rep_max=180.0,
        max_power_watts=450.0,
        rom_inches=18.5,
        struggling_score=0.2,
        side="Both",
        created_at=created_at,
        occurred_at=occurred_at if occurred_at is not None else created_at,
    )


def test_strength_set_upsert_and_get_by_activity_roundtrip(tmp_path):
    conn = connect(tmp_path / "test.db")
    set0 = _strength_set("tonal:abc123", 0)
    set1 = _strength_set("tonal:abc123", 1)

    inserted = repository.upsert_strength_sets(conn, [set0, set1])
    assert inserted == 2

    result = repository.get_strength_sets(conn, "tonal:abc123")
    assert result == [set0, set1]


def test_strength_set_upsert_is_idempotent_and_updates_fields(tmp_path):
    conn = connect(tmp_path / "test.db")
    original = _strength_set("tonal:abc123", 0)
    repository.upsert_strength_sets(conn, [original])

    updated = StrengthSet(
        id=original.id,
        activity_id=original.activity_id,
        movement_id=original.movement_id,
        movement_name=original.movement_name,
        set_index=original.set_index,
        is_warm_up=original.is_warm_up,
        reps=12,
        weight_lbs=140.0,
        volume_lbs=1680.0,
        one_rep_max=185.0,
        max_power_watts=460.0,
        rom_inches=19.0,
        struggling_score=0.3,
        side="Both",
        created_at=original.created_at,
        occurred_at=original.occurred_at,
    )
    repository.upsert_strength_sets(conn, [updated])

    result = repository.get_strength_sets(conn, "tonal:abc123")
    assert result == [updated]


def test_get_strength_sets_filters_by_activity_id(tmp_path):
    conn = connect(tmp_path / "test.db")
    set_a = _strength_set("tonal:aaa", 0)
    set_b = _strength_set("tonal:bbb", 0)
    repository.upsert_strength_sets(conn, [set_a, set_b])

    result = repository.get_strength_sets(conn, "tonal:aaa")

    assert result == [set_a]


def test_get_strength_sets_by_movement_orders_by_occurred_at_not_created_at(tmp_path):
    """Regression: get_strength_sets_by_movement must order by occurred_at
    (the real workout timestamp), not created_at (when the row was written
    to our DB) -- these deliberately disagree here to prove it."""
    conn = connect(tmp_path / "test.db")
    # Written to the DB in this order (created_at ascending)...
    oldest_written = _strength_set(
        "tonal:w1", 0, movement_id="mv-bench",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 8, 18, 8, 0),
    )
    middle_written = _strength_set(
        "tonal:w2", 0, movement_id="mv-bench",
        created_at=datetime(2026, 1, 5, 12, 0), occurred_at=datetime(2026, 1, 10, 8, 0),
    )
    newest_written = _strength_set(
        "tonal:w3", 0, movement_id="mv-bench",
        created_at=datetime(2026, 1, 10, 12, 0), occurred_at=datetime(2026, 1, 1, 8, 0),
    )
    other_movement = _strength_set(
        "tonal:w4", 0, movement_id="mv-squat",
        created_at=datetime(2026, 1, 12, 12, 0), occurred_at=datetime(2026, 1, 15, 8, 0),
    )
    repository.upsert_strength_sets(conn, [oldest_written, middle_written, newest_written, other_movement])

    result = repository.get_strength_sets_by_movement(conn, "mv-bench")
    # occurred_at descending: oldest_written (Aug 18) > middle_written (Jan 10) > newest_written (Jan 1)
    assert result == [oldest_written, middle_written, newest_written]

    limited = repository.get_strength_sets_by_movement(conn, "mv-bench", limit=2)
    assert limited == [oldest_written, middle_written]


def test_connect_migrates_legacy_strength_set_table_and_backfills_occurred_at(tmp_path):
    """Simulates a database created before `occurred_at` existed: a
    strength_set table without that column (CREATE TABLE IF NOT EXISTS
    no-ops on a table that already exists, so adding the column to the
    schema string alone wouldn't reach a real pre-existing database)."""
    db_path = tmp_path / "test.db"
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript(
        """
        CREATE TABLE activity (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, activity_id TEXT NOT NULL,
            activity_name TEXT, activity_type TEXT NOT NULL, sport_type TEXT,
            start_time TEXT NOT NULL, duration_seconds REAL NOT NULL, distance_meters REAL,
            calories REAL, avg_hr REAL, max_hr REAL, avg_speed REAL, max_speed REAL,
            elevation_gain REAL, elevation_loss REAL, created_at TEXT NOT NULL
        );
        CREATE TABLE strength_set (
            id TEXT PRIMARY KEY, activity_id TEXT NOT NULL, movement_id TEXT NOT NULL,
            movement_name TEXT, set_index INTEGER NOT NULL, is_warm_up INTEGER NOT NULL DEFAULT 0,
            reps INTEGER, weight_lbs REAL, volume_lbs REAL, one_rep_max REAL,
            max_power_watts REAL, rom_inches REAL, struggling_score REAL, side TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    legacy_conn.execute(
        "INSERT INTO activity VALUES ('tonal:act-1','tonal','act-1',NULL,'strength_training',NULL,"
        "'2026-08-18T11:33:08',719.0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'2026-08-18T12:00:00')"
    )
    legacy_conn.execute(
        "INSERT INTO strength_set VALUES ('tonal:act-1:0','tonal:act-1','mv-bench','Bench Press',0,0,"
        "10,135.0,1350.0,180.0,450.0,18.5,0.2,'Both','2026-08-25T00:00:00')"
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = connect(db_path)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(strength_set)").fetchall()}
    assert "occurred_at" in columns

    row = conn.execute("SELECT occurred_at FROM strength_set WHERE id = 'tonal:act-1:0'").fetchone()
    assert row[0] == "2026-08-18T11:33:08"


def test_connect_migrates_orphaned_legacy_strength_set_falls_back_to_created_at(tmp_path):
    """A legacy strength_set row whose activity_id has no matching activity
    row (an orphan -- plausible here since activity rows only exist for date
    ranges a sync actually covered) must not be left with occurred_at = ''.
    That empty string later crashes datetime.fromisoformat in
    _row_to_strength_set, so the backfill must fall back to the row's own
    created_at instead of leaving it blank."""
    db_path = tmp_path / "test.db"
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript(
        """
        CREATE TABLE activity (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, activity_id TEXT NOT NULL,
            activity_name TEXT, activity_type TEXT NOT NULL, sport_type TEXT,
            start_time TEXT NOT NULL, duration_seconds REAL NOT NULL, distance_meters REAL,
            calories REAL, avg_hr REAL, max_hr REAL, avg_speed REAL, max_speed REAL,
            elevation_gain REAL, elevation_loss REAL, created_at TEXT NOT NULL
        );
        CREATE TABLE strength_set (
            id TEXT PRIMARY KEY, activity_id TEXT NOT NULL, movement_id TEXT NOT NULL,
            movement_name TEXT, set_index INTEGER NOT NULL, is_warm_up INTEGER NOT NULL DEFAULT 0,
            reps INTEGER, weight_lbs REAL, volume_lbs REAL, one_rep_max REAL,
            max_power_watts REAL, rom_inches REAL, struggling_score REAL, side TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    # No corresponding row in `activity` for 'tonal:orphan-1' -- this is the
    # orphan case the backfill's old EXISTS(...) guard skipped entirely.
    legacy_conn.execute(
        "INSERT INTO strength_set VALUES ('tonal:orphan-1:0','tonal:orphan-1','mv-squat','Squat',0,0,"
        "8,225.0,1800.0,275.0,500.0,20.0,0.1,'Both','2026-08-19T09:15:00')"
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = connect(db_path)

    row = conn.execute(
        "SELECT occurred_at, created_at FROM strength_set WHERE id = 'tonal:orphan-1:0'"
    ).fetchone()
    assert row[0] == "2026-08-19T09:15:00"
    assert row[0] == row[1]
    assert row[0] != ""

    # And the row must be readable without raising (get_strength_sets calls
    # datetime.fromisoformat(occurred_at) internally via _row_to_strength_set).
    sets = repository.get_strength_sets(conn, "tonal:orphan-1")
    assert len(sets) == 1
    assert sets[0].occurred_at == datetime(2026, 8, 19, 9, 15, 0)


def test_replace_strength_set_muscle_groups_deletes_stale_pairs(tmp_path):
    """Delete-then-insert, not a plain upsert: if a movement's tagged
    muscle groups change between two writes for the same set, the old
    pairing must not survive (the composite PK can add-or-ignore new pairs
    but can't remove a stale one on its own)."""
    conn = connect(tmp_path / "test.db")
    ss = _strength_set("tonal:w1", 0)
    repository.upsert_strength_sets(conn, [ss])

    repository.replace_strength_set_muscle_groups(conn, ss.id, ["Chest", "Triceps"])
    rows = conn.execute(
        "SELECT muscle_group FROM strength_set_muscle_group WHERE strength_set_id = ? ORDER BY muscle_group", (ss.id,)
    ).fetchall()
    assert [r[0] for r in rows] == ["Chest", "Triceps"]

    repository.replace_strength_set_muscle_groups(conn, ss.id, ["Shoulders"])
    rows = conn.execute(
        "SELECT muscle_group FROM strength_set_muscle_group WHERE strength_set_id = ?", (ss.id,)
    ).fetchall()
    assert [r[0] for r in rows] == ["Shoulders"]


def test_get_muscle_group_volume_aggregates_across_multi_muscle_movements(tmp_path):
    conn = connect(tmp_path / "test.db")
    chest_set = _strength_set(
        "tonal:w1", 0, movement_id="mv-bench", occurred_at=datetime(2026, 7, 10, 8, 0)
    )  # volume_lbs=1350.0 via helper default
    quad_set = StrengthSet(
        id="tonal:w2:0", activity_id="tonal:w2", movement_id="mv-squat", movement_name="Squat",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=225.0, volume_lbs=1800.0,
        one_rep_max=280.0, max_power_watts=500.0, rom_inches=20.0, struggling_score=0.3, side="Both",
        created_at=datetime(2026, 7, 12, 12, 0), occurred_at=datetime(2026, 7, 12, 8, 0),
    )
    repository.upsert_strength_sets(conn, [chest_set, quad_set])
    repository.replace_strength_set_muscle_groups(conn, chest_set.id, ["Chest", "Triceps"])
    repository.replace_strength_set_muscle_groups(conn, quad_set.id, ["Quads"])

    result = repository.get_muscle_group_volume(conn, date(2026, 7, 1), date(2026, 7, 31))

    by_group = {r["muscle_group"]: r for r in result}
    assert by_group["Chest"]["total_volume_lbs"] == 1350.0
    assert by_group["Triceps"]["total_volume_lbs"] == 1350.0
    assert by_group["Quads"]["total_volume_lbs"] == 1800.0
    assert by_group["Quads"]["session_count"] == 1
    assert by_group["Quads"]["last_trained"] == "2026-07-12T08:00:00"
    # sorted busiest-first
    assert result[0]["muscle_group"] == "Quads"


def test_get_muscle_group_volume_excludes_sets_outside_range(tmp_path):
    conn = connect(tmp_path / "test.db")
    outside = _strength_set("tonal:w1", 0, occurred_at=datetime(2026, 6, 1, 8, 0))
    repository.upsert_strength_sets(conn, [outside])
    repository.replace_strength_set_muscle_groups(conn, outside.id, ["Chest"])

    result = repository.get_muscle_group_volume(conn, date(2026, 7, 1), date(2026, 7, 31))

    assert result == []


def test_find_known_movements_matches_by_exact_id_or_name_substring(tmp_path):
    conn = connect(tmp_path / "test.db")
    bench = _strength_set("tonal:w1", 0, movement_id="mv-bench")  # movement_name="Bench Press" via helper default
    close_grip = StrengthSet(
        id="tonal:w2:0", activity_id="tonal:w2", movement_id="mv-cgbp", movement_name="Close Grip Bench Press",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=100.0, volume_lbs=800.0, one_rep_max=130.0,
        max_power_watts=400.0, rom_inches=18.0, struggling_score=0.3, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 1, 1, 12, 0),
    )
    squat = StrengthSet(
        id="tonal:w3:0", activity_id="tonal:w3", movement_id="mv-squat", movement_name="Barbell Squat",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=225.0, volume_lbs=1800.0, one_rep_max=280.0,
        max_power_watts=500.0, rom_inches=20.0, struggling_score=0.3, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 1, 1, 12, 0),
    )
    repository.upsert_strength_sets(conn, [bench, close_grip, squat])

    exact_id = repository.find_known_movements(conn, "mv-squat")
    assert exact_id == [{"movement_id": "mv-squat", "movement_name": "Barbell Squat"}]

    ambiguous = repository.find_known_movements(conn, "bench")
    assert {m["movement_id"] for m in ambiguous} == {"mv-bench", "mv-cgbp"}

    no_match = repository.find_known_movements(conn, "deadlift")
    assert no_match == []


def _make_session(session_id="garmin:2026-09-15", d=date(2026, 9, 15)):
    return SleepSession(
        id=session_id, calendar_date=d,
        sleep_start_utc=datetime(2026, 9, 15, 3, 46, 52),
        sleep_end_utc=datetime(2026, 9, 15, 10, 39, 52),
        sleep_start_local=datetime(2026, 9, 14, 23, 46, 52),
        sleep_end_local=datetime(2026, 9, 15, 6, 39, 52),
        total_sleep_seconds=24780.0, nap_time_seconds=0.0,
        deep_sleep_seconds=7140.0, light_sleep_seconds=12060.0,
        rem_sleep_seconds=5580.0, awake_sleep_seconds=0.0,
        unmeasurable_sleep_seconds=0.0, awake_count=0,
        restless_moments_count=31, avg_sleep_stress=26.0, avg_heart_rate=65.0,
        avg_overnight_hrv=26.0, avg_respiration=17.0, lowest_respiration=13.0,
        highest_respiration=22.0, overall_score=76.0, overall_score_qualifier="FAIR",
        duration_qualifier="FAIR", stress_qualifier="POOR", awake_count_qualifier="EXCELLENT",
        restlessness_qualifier="EXCELLENT", rem_percentage=23.0, rem_percentage_qualifier="EXCELLENT",
        light_percentage=49.0, light_percentage_qualifier="EXCELLENT", deep_percentage=29.0,
        deep_percentage_qualifier="EXCELLENT", sleep_need_baseline_minutes=470,
        sleep_need_actual_minutes=500, sleep_need_feedback="INCREASED",
        score_feedback="NEGATIVE_NOT_RESTORATIVE", score_insight="NONE",
        score_personalized_insight="GENERAL_NEG_FAIR_OR_POOR_SLEEP",
        created_at=datetime(2026, 9, 15, 12, 0, 0),
    )


def test_upsert_and_get_sleep_session(tmp_path):
    conn = connect(tmp_path / "test.db")
    session = _make_session()
    repository.upsert_sleep_session(conn, session)
    fetched = repository.get_sleep_session(conn, "garmin:2026-09-15")
    assert fetched is not None
    assert fetched.overall_score == 76.0
    assert fetched.duration_qualifier == "FAIR"
    assert fetched.calendar_date == date(2026, 9, 15)

    # upsert again with a changed score -- should update, not duplicate
    updated = _make_session()
    object.__setattr__(updated, "overall_score", 80.0)  # frozen dataclass, test-only mutation
    repository.upsert_sleep_session(conn, updated)
    assert repository.get_sleep_session(conn, "garmin:2026-09-15").overall_score == 80.0


def test_replace_sleep_stage_segments_deletes_stale_rows(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.upsert_sleep_session(conn, _make_session())
    first_pass = [
        SleepStageSegment(
            id="garmin:2026-09-15:0", sleep_session_id="garmin:2026-09-15",
            segment_index=0, stage="light",
            start_utc=datetime(2026, 9, 15, 3, 46, 52),
            end_utc=datetime(2026, 9, 15, 3, 47, 52), duration_seconds=60.0,
        ),
        SleepStageSegment(
            id="garmin:2026-09-15:1", sleep_session_id="garmin:2026-09-15",
            segment_index=1, stage="deep",
            start_utc=datetime(2026, 9, 15, 3, 47, 52),
            end_utc=datetime(2026, 9, 15, 4, 39, 52), duration_seconds=3120.0,
        ),
    ]
    repository.replace_sleep_stage_segments(conn, "garmin:2026-09-15", first_pass)
    assert len(repository.get_sleep_stage_segments(conn, "garmin:2026-09-15")) == 2

    # Re-hydration with a different (reclassified) segment set -- old rows must be gone
    second_pass = [
        SleepStageSegment(
            id="garmin:2026-09-15:0", sleep_session_id="garmin:2026-09-15",
            segment_index=0, stage="rem",
            start_utc=datetime(2026, 9, 15, 3, 46, 52),
            end_utc=datetime(2026, 9, 15, 4, 30, 0), duration_seconds=2588.0,
        ),
    ]
    repository.replace_sleep_stage_segments(conn, "garmin:2026-09-15", second_pass)
    segments = repository.get_sleep_stage_segments(conn, "garmin:2026-09-15")
    assert len(segments) == 1
    assert segments[0].stage == "rem"


def test_replace_sleep_restless_moments(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.upsert_sleep_session(conn, _make_session())
    moments = [
        SleepRestlessMoment(
            sleep_session_id="garmin:2026-09-15",
            occurred_at_utc=datetime(2026, 9, 15, 4, 5, 32), value=1,
        ),
    ]
    repository.replace_sleep_restless_moments(conn, "garmin:2026-09-15", moments)
    fetched = repository.get_sleep_restless_moments(conn, "garmin:2026-09-15")
    assert len(fetched) == 1 and fetched[0].value == 1


def test_get_sleep_sessions_range(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.upsert_sleep_session(conn, _make_session("garmin:2026-09-14", date(2026, 9, 14)))
    repository.upsert_sleep_session(conn, _make_session("garmin:2026-09-15", date(2026, 9, 15)))
    repository.upsert_sleep_session(conn, _make_session("garmin:2026-09-20", date(2026, 9, 20)))
    in_range = repository.get_sleep_sessions_range(conn, date(2026, 9, 14), date(2026, 9, 15))
    assert {s.id for s in in_range} == {"garmin:2026-09-14", "garmin:2026-09-15"}
