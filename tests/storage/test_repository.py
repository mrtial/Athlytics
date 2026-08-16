from datetime import date, datetime

from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading


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
