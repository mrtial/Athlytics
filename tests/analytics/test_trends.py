from datetime import date, datetime, timedelta

import pytest

from core.analytics.trends import RollingAverage, rolling_average, rolling_average_series
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading


def _reading(metric_type: str, day_number: int, value: float, unit: str) -> MetricReading:
    timestamp = datetime(2026, 1, 1) + timedelta(days=day_number - 1)
    return MetricReading("garmin", metric_type, timestamp, value, unit)


def test_rolling_average_over_a_window_covering_all_readings(tmp_path):
    conn = connect(tmp_path / "test.db")
    values = [50.0, 52.0, 54.0, 56.0, 58.0]
    readings = [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 6), values)]
    repository.upsert_readings(conn, readings)

    result = rolling_average(conn, "resting_hr", window_days=5, as_of=date(2026, 1, 5))

    assert result == RollingAverage(
        metric_type="resting_hr",
        window_days=5,
        window_start=date(2026, 1, 1),
        window_end=date(2026, 1, 5),
        average=54.0,
        sample_count=5,
    )


def test_rolling_average_over_a_narrower_window(tmp_path):
    conn = connect(tmp_path / "test.db")
    values = [50.0, 52.0, 54.0, 56.0, 58.0]
    readings = [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 6), values)]
    repository.upsert_readings(conn, readings)

    result = rolling_average(conn, "resting_hr", window_days=3, as_of=date(2026, 1, 5))

    assert result == RollingAverage(
        metric_type="resting_hr",
        window_days=3,
        window_start=date(2026, 1, 3),
        window_end=date(2026, 1, 5),
        average=56.0,
        sample_count=3,
    )


def test_rolling_average_returns_none_average_when_no_data_in_window(tmp_path):
    conn = connect(tmp_path / "test.db")

    result = rolling_average(conn, "resting_hr", window_days=7, as_of=date(2026, 1, 5))

    assert result == RollingAverage(
        metric_type="resting_hr",
        window_days=7,
        window_start=date(2025, 12, 30),
        window_end=date(2026, 1, 5),
        average=None,
        sample_count=0,
    )


def test_rolling_average_rejects_non_positive_window_days(tmp_path):
    conn = connect(tmp_path / "test.db")

    with pytest.raises(ValueError, match="window_days must be >= 1"):
        rolling_average(conn, "resting_hr", window_days=0, as_of=date(2026, 1, 5))


def test_rolling_average_series_produces_one_point_per_day(tmp_path):
    conn = connect(tmp_path / "test.db")
    values = [50.0, 52.0, 54.0, 56.0, 58.0]
    readings = [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 6), values)]
    repository.upsert_readings(conn, readings)

    series = rolling_average_series(
        conn, "resting_hr", window_days=3, start=date(2026, 1, 3), end=date(2026, 1, 5)
    )

    assert series == [
        RollingAverage("resting_hr", 3, date(2026, 1, 1), date(2026, 1, 3), 52.0, 3),
        RollingAverage("resting_hr", 3, date(2026, 1, 2), date(2026, 1, 4), 54.0, 3),
        RollingAverage("resting_hr", 3, date(2026, 1, 3), date(2026, 1, 5), 56.0, 3),
    ]


def test_rolling_average_series_rejects_start_after_end(tmp_path):
    conn = connect(tmp_path / "test.db")

    with pytest.raises(ValueError, match="must not be after"):
        rolling_average_series(conn, "resting_hr", window_days=3, start=date(2026, 1, 5), end=date(2026, 1, 1))
