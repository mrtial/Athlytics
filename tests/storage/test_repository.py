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
