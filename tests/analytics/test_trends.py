from datetime import date, datetime, timedelta

import pytest

from core.analytics.trends import (
    Delta,
    RollingAverage,
    Trend,
    compute_delta,
    get_trend,
    month_over_month_delta,
    rolling_average,
    rolling_average_series,
    week_over_week_delta,
)
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


def test_week_over_week_delta(tmp_path):
    conn = connect(tmp_path / "test.db")
    previous_week = [_reading("steps", d, 1000.0, "count") for d in range(1, 8)]
    current_week = [_reading("steps", d, 2000.0, "count") for d in range(8, 15)]
    repository.upsert_readings(conn, previous_week + current_week)

    result = week_over_week_delta(conn, "steps", as_of=date(2026, 1, 14))

    assert result == compute_delta(conn, "steps", 7, as_of=date(2026, 1, 14))
    assert result.current.average == 2000.0
    assert result.previous.average == 1000.0
    assert result.absolute_change == 1000.0
    assert result.percent_change == 100.0


def test_month_over_month_delta(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading("weight", d, float(d), "kg") for d in range(1, 61)]
    repository.upsert_readings(conn, readings)

    result = month_over_month_delta(conn, "weight", as_of=date(2026, 3, 1))

    expected_current_mean = sum(range(31, 61)) / 30
    expected_previous_mean = sum(range(1, 31)) / 30
    assert result.window_days == 30
    assert result.current.window_start == date(2026, 1, 31)
    assert result.current.window_end == date(2026, 3, 1)
    assert result.current.average == pytest.approx(expected_current_mean)
    assert result.previous.window_start == date(2026, 1, 1)
    assert result.previous.window_end == date(2026, 1, 30)
    assert result.previous.average == pytest.approx(expected_previous_mean)
    assert result.absolute_change == pytest.approx(expected_current_mean - expected_previous_mean)
    assert result.percent_change == pytest.approx(
        (expected_current_mean - expected_previous_mean) / expected_previous_mean * 100
    )


def test_delta_handles_missing_previous_window_data(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading("steps", d, 1000.0, "count") for d in range(1, 4)]
    repository.upsert_readings(conn, readings)

    result = compute_delta(conn, "steps", window_days=3, as_of=date(2026, 1, 3))

    assert result.current.average == 1000.0
    assert result.previous.average is None
    assert result.absolute_change is None
    assert result.percent_change is None


def test_get_trend_combines_current_average_and_delta(tmp_path):
    conn = connect(tmp_path / "test.db")
    previous_week = [_reading("steps", d, 1000.0, "count") for d in range(1, 8)]
    current_week = [_reading("steps", d, 2000.0, "count") for d in range(8, 15)]
    repository.upsert_readings(conn, previous_week + current_week)

    trend = get_trend(conn, "steps", window_days=7, as_of=date(2026, 1, 14))

    assert trend.metric_type == "steps"
    assert trend.window_days == 7
    assert trend.current == rolling_average(conn, "steps", 7, as_of=date(2026, 1, 14))
    assert trend.delta == compute_delta(conn, "steps", 7, as_of=date(2026, 1, 14))
