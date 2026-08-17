from datetime import date, datetime

from core.providers.apple_health import HK_QUANTITY_MAP, aggregate_daily, parse_apple_health_timestamp


def test_parse_apple_health_timestamp_converts_offset_to_naive_utc():
    result = parse_apple_health_timestamp("2026-05-01 07:30:00 -0400")

    assert result == datetime(2026, 5, 1, 11, 30, 0)
    assert result.tzinfo is None


def test_parse_apple_health_timestamp_handles_utc_offset():
    result = parse_apple_health_timestamp("2026-05-01 07:30:00 +0000")

    assert result == datetime(2026, 5, 1, 7, 30, 0)


def test_hk_quantity_map_covers_shared_and_apple_only_metric_types():
    assert HK_QUANTITY_MAP["HKQuantityTypeIdentifierRestingHeartRate"] == ("resting_hr", "bpm", "mean")
    assert HK_QUANTITY_MAP["HKQuantityTypeIdentifierStepCount"] == ("steps", "count", "sum")
    assert HK_QUANTITY_MAP["HKQuantityTypeIdentifierAppleExerciseTime"] == ("exercise_minutes", "min", "sum")


def test_aggregate_daily_sums_cumulative_values():
    readings = {date(2026, 1, 1): [100.0, 200.0, 50.0]}

    result = aggregate_daily(readings, "sum")

    assert result == {date(2026, 1, 1): 350.0}


def test_aggregate_daily_averages_point_in_time_values():
    readings = {date(2026, 1, 1): [50.0, 54.0]}

    result = aggregate_daily(readings, "mean")

    assert result == {date(2026, 1, 1): 52.0}


def test_aggregate_daily_handles_multiple_days_independently():
    readings = {date(2026, 1, 1): [50.0], date(2026, 1, 2): [60.0, 70.0]}

    result = aggregate_daily(readings, "mean")

    assert result == {date(2026, 1, 1): 50.0, date(2026, 1, 2): 65.0}
