import zipfile
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from core.providers.apple_health import (
    APPLE_HEALTH_METRIC_TYPES,
    SLEEP_ASLEEP_VALUES,
    HK_QUANTITY_MAP,
    AppleHealthProvider,
    aggregate_daily,
    aggregate_mindful_minutes,
    aggregate_sleep_hours,
    aggregate_stand_hours,
    apple_health_local_date,
    parse_apple_health_timestamp,
)

FIXTURE_XML = (Path(__file__).parent.parent / "fixtures" / "apple_health_export.xml").read_bytes()


def _zip_payload(xml_bytes: bytes) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml_bytes)
    return buf.getvalue()


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


def test_sleep_asleep_values_includes_unspecified():
    # Devices/apps without stage-specific sleep tracking (pre-Watch-Series-6,
    # or third-party apps) record AsleepUnspecified rather than Core/Deep/REM.
    assert "HKCategoryValueSleepAnalysisAsleepUnspecified" in SLEEP_ASLEEP_VALUES


def test_aggregate_sleep_hours_sums_only_asleep_stages():
    stage_records = [
        ("HKCategoryValueSleepAnalysisAsleepCore", datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 2, 1, 0), date(2026, 1, 2)),
        ("HKCategoryValueSleepAnalysisAsleepDeep", datetime(2026, 1, 2, 1, 0), datetime(2026, 1, 2, 3, 0), date(2026, 1, 2)),
        ("HKCategoryValueSleepAnalysisAwake", datetime(2026, 1, 2, 3, 0), datetime(2026, 1, 2, 3, 15), date(2026, 1, 2)),
    ]

    result = aggregate_sleep_hours(stage_records)

    # Bucketed by the night's local ending date (2026-01-02): 2h Core + 2h Deep = 4.0 hours.
    assert result == {date(2026, 1, 2): 4.0}


def test_aggregate_mindful_minutes_sums_session_durations():
    session_records = [
        (datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 8, 10), date(2026, 1, 1)),
        (datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 1, 20, 5), date(2026, 1, 1)),
    ]

    result = aggregate_mindful_minutes(session_records)

    assert result == {date(2026, 1, 1): 15.0}


def test_aggregate_stand_hours_counts_only_stood_hours():
    stand_records = [
        ("HKCategoryValueAppleStandHourStood", date(2026, 1, 1)),
        ("HKCategoryValueAppleStandHourIdle", date(2026, 1, 1)),
        ("HKCategoryValueAppleStandHourStood", date(2026, 1, 1)),
    ]

    result = aggregate_stand_hours(stand_records)

    assert result == {date(2026, 1, 1): 2.0}


def test_apple_health_metric_types_includes_shared_and_apple_only_types():
    assert "resting_hr" in APPLE_HEALTH_METRIC_TYPES
    assert "steps" in APPLE_HEALTH_METRIC_TYPES
    assert "mindful_minutes" in APPLE_HEALTH_METRIC_TYPES
    assert "sleep_duration" in APPLE_HEALTH_METRIC_TYPES
    assert "stand_hours" in APPLE_HEALTH_METRIC_TYPES


def test_ingest_yields_aggregated_daily_readings_from_zip():
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    by_type = {r.metric_type: r for r in readings}

    assert by_type["resting_hr"].value == 52.0  # mean of 50, 54
    assert by_type["resting_hr"].source == "apple_health"
    assert by_type["resting_hr"].unit == "bpm"

    assert by_type["steps"].value == 2000.0  # sum of 1200, 800

    assert by_type["mindful_minutes"].value == 10.0

    assert by_type["sleep_duration"].value == 2.0  # 2 hours Core, Awake excluded
    assert by_type["sleep_duration"].unit == "hr"

    assert by_type["stand_hours"].value == 1.0


def test_ingest_never_writes_sleep_data_under_garmins_sleep_score_type():
    # Garmin's sleep_score is a 0-100 quality score; Apple Health's sleep data
    # here is hours-asleep -- a different physical quantity that must never
    # land under the same metric_type (see spec's Metric Mapping section).
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    metric_types = {r.metric_type for r in readings}
    assert "sleep_score" not in metric_types
    assert "sleep_duration" in metric_types


def test_ingest_skips_unrecognized_record_types_without_erroring():
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    metric_types = {r.metric_type for r in readings}
    assert "uv_exposure" not in metric_types  # HKQuantityTypeIdentifierUVExposure has no mapping


def test_ingest_produces_naive_utc_midnight_timestamps():
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    for r in readings:
        assert r.timestamp.tzinfo is None
        assert r.timestamp.hour == 0 and r.timestamp.minute == 0


def test_ingest_buckets_by_local_day_not_utc_day():
    # 22:00 Eastern (-0500) converts to 03:00 UTC the *next* day. Bucketing
    # must still attribute this reading to Jan 1 (the local day it
    # happened), not Jan 2 (the UTC day) -- the bug this test guards
    # against silently shifted every evening reading in negative-UTC-offset
    # timezones onto the wrong day.
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-01 22:00:00 -0500" endDate="2026-01-01 22:30:00 -0500" value="500"/>
</HealthData>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml)

    provider = AppleHealthProvider()
    readings = list(provider.ingest(buf.getvalue()))

    assert len(readings) == 1
    assert readings[0].timestamp.date() == date(2026, 1, 1)


def test_apple_health_local_date_uses_the_strings_own_offset_not_utc():
    # Same instant as parse_apple_health_timestamp's UTC-conversion test,
    # but the local calendar date must stay on the string's own day even
    # though UTC conversion would land on the same day here -- exercised
    # properly by test_ingest_buckets_by_local_day_not_utc_day above, which
    # crosses the UTC day boundary.
    assert apple_health_local_date("2026-05-01 07:30:00 -0400") == date(2026, 5, 1)
