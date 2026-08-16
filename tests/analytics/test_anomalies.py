import statistics
from datetime import date, datetime, timedelta

import pytest

from core.analytics.anomalies import (
    Anomaly,
    Baseline,
    compute_baseline,
    detect_anomalies,
    detect_anomalies_for_metrics,
)
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading


def _reading(metric_type: str, day_number: int, value: float, unit: str) -> MetricReading:
    timestamp = datetime(2026, 1, 1) + timedelta(days=day_number - 1)
    return MetricReading("garmin", metric_type, timestamp, value, unit)


def test_compute_baseline_computes_mean_and_stdev_over_window(tmp_path):
    conn = connect(tmp_path / "test.db")
    values = [50.0] * 9 + [70.0]
    readings = [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 11), values)]
    repository.upsert_readings(conn, readings)

    baseline = compute_baseline(conn, "resting_hr", window_days=10, as_of=date(2026, 1, 10))

    assert baseline == Baseline(
        metric_type="resting_hr",
        window_days=10,
        window_start=date(2026, 1, 1),
        window_end=date(2026, 1, 10),
        mean=statistics.mean(values),
        stdev=statistics.stdev(values),
        sample_count=10,
    )


def test_compute_baseline_returns_none_with_fewer_than_two_readings(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.upsert_readings(conn, [_reading("resting_hr", 1, 50.0, "bpm")])

    assert compute_baseline(conn, "resting_hr", window_days=10, as_of=date(2026, 1, 1)) is None


def test_compute_baseline_rejects_non_positive_window_days(tmp_path):
    conn = connect(tmp_path / "test.db")

    with pytest.raises(ValueError, match="window_days must be >= 1"):
        compute_baseline(conn, "resting_hr", window_days=0, as_of=date(2026, 1, 1))


def test_detect_anomalies_flags_readings_beyond_z_threshold(tmp_path):
    conn = connect(tmp_path / "test.db")
    values = [50.0] * 9 + [70.0]
    readings = [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 11), values)]
    repository.upsert_readings(conn, readings)
    expected_mean = statistics.mean(values)
    expected_stdev = statistics.stdev(values)

    anomalies = detect_anomalies(
        conn, "resting_hr", baseline_window_days=10, z_threshold=2.0, as_of=date(2026, 1, 10)
    )

    assert anomalies == [
        Anomaly(
            metric_type="resting_hr",
            timestamp=datetime(2026, 1, 10),
            value=70.0,
            baseline_mean=expected_mean,
            baseline_stdev=expected_stdev,
            z_score=(70.0 - expected_mean) / expected_stdev,
            direction="above",
            baseline_window_days=10,
        )
    ]


def test_detect_anomalies_returns_empty_list_when_baseline_insufficient(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.upsert_readings(conn, [_reading("resting_hr", 1, 50.0, "bpm")])

    assert detect_anomalies(conn, "resting_hr", baseline_window_days=10, as_of=date(2026, 1, 1)) == []


def test_detect_anomalies_returns_empty_list_when_baseline_has_zero_variance(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading("resting_hr", d, 50.0, "bpm") for d in range(1, 11)]
    repository.upsert_readings(conn, readings)

    assert (
        detect_anomalies(conn, "resting_hr", baseline_window_days=10, z_threshold=2.0, as_of=date(2026, 1, 10))
        == []
    )


def test_detect_anomalies_since_restricts_candidates_but_not_the_baseline(tmp_path):
    conn = connect(tmp_path / "test.db")
    values = [50.0] * 9 + [70.0, 51.0]
    readings = [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 12), values)]
    repository.upsert_readings(conn, readings)
    baseline_mean = statistics.mean(values)
    baseline_stdev = statistics.stdev(values)

    anomalies = detect_anomalies(
        conn,
        "resting_hr",
        baseline_window_days=11,
        z_threshold=2.0,
        since=date(2026, 1, 10),
        as_of=date(2026, 1, 11),
    )

    assert [a.timestamp.date() for a in anomalies] == [date(2026, 1, 10)]
    assert anomalies[0].value == 70.0
    assert anomalies[0].z_score == pytest.approx((70.0 - baseline_mean) / baseline_stdev)
    assert anomalies[0].baseline_mean == pytest.approx(baseline_mean)


def test_detect_anomalies_rejects_since_after_as_of(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading("resting_hr", d, 50.0 + d, "bpm") for d in range(1, 11)]
    repository.upsert_readings(conn, readings)

    with pytest.raises(ValueError, match="must not be after"):
        detect_anomalies(
            conn, "resting_hr", baseline_window_days=10, since=date(2026, 1, 10), as_of=date(2026, 1, 5)
        )


def test_detect_anomalies_for_metrics_flattens_and_sorts_across_metric_types(tmp_path):
    conn = connect(tmp_path / "test.db")
    hr_values = [50.0] * 9 + [70.0]
    steps_values = [10000.0] * 9 + [2000.0]
    repository.upsert_readings(
        conn, [_reading("resting_hr", d, v, "bpm") for d, v in zip(range(1, 11), hr_values)]
    )
    repository.upsert_readings(
        conn, [_reading("steps", d, v, "count") for d, v in zip(range(1, 11), steps_values)]
    )

    anomalies = detect_anomalies_for_metrics(
        conn, ["steps", "resting_hr"], baseline_window_days=10, z_threshold=2.0, as_of=date(2026, 1, 10)
    )

    assert [(a.metric_type, a.value, a.direction) for a in anomalies] == [
        ("resting_hr", 70.0, "above"),
        ("steps", 2000.0, "below"),
    ]


def test_detect_anomalies_for_metrics_returns_empty_list_for_empty_input(tmp_path):
    conn = connect(tmp_path / "test.db")

    assert detect_anomalies_for_metrics(conn, [], as_of=date(2026, 1, 10)) == []
