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
