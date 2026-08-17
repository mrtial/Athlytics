from datetime import date, datetime

from core.providers.apple_health import (
    SLEEP_ASLEEP_VALUES,
    HK_QUANTITY_MAP,
    aggregate_daily,
    aggregate_mindful_minutes,
    aggregate_sleep_hours,
    aggregate_stand_hours,
    parse_apple_health_timestamp,
)


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


def test_sleep_asleep_values_excludes_awake_and_in_bed():
    assert "HKCategoryValueSleepAnalysisAsleepCore" in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisAsleepDeep" in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisAsleepREM" in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisAwake" not in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisInBed" not in SLEEP_ASLEEP_VALUES


def test_aggregate_sleep_hours_sums_only_asleep_stages():
    stage_records = [
        ("HKCategoryValueSleepAnalysisAsleepCore", datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 2, 1, 0)),
        ("HKCategoryValueSleepAnalysisAsleepDeep", datetime(2026, 1, 2, 1, 0), datetime(2026, 1, 2, 3, 0)),
        ("HKCategoryValueSleepAnalysisAwake", datetime(2026, 1, 2, 3, 0), datetime(2026, 1, 2, 3, 15)),
    ]

    result = aggregate_sleep_hours(stage_records)

    # Bucketed by the night's ending date (2026-01-02): 2h Core + 2h Deep = 4.0 hours.
    assert result == {date(2026, 1, 2): 4.0}


def test_aggregate_mindful_minutes_sums_session_durations():
    session_records = [
        (datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 8, 10)),
        (datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 1, 20, 5)),
    ]

    result = aggregate_mindful_minutes(session_records)

    assert result == {date(2026, 1, 1): 15.0}


def test_aggregate_stand_hours_counts_only_stood_hours():
    stand_records = [
        ("HKCategoryValueAppleStandHourStood", datetime(2026, 1, 1, 9, 0)),
        ("HKCategoryValueAppleStandHourIdle", datetime(2026, 1, 1, 10, 0)),
        ("HKCategoryValueAppleStandHourStood", datetime(2026, 1, 1, 11, 0)),
    ]

    result = aggregate_stand_hours(stand_records)

    assert result == {date(2026, 1, 1): 2.0}
