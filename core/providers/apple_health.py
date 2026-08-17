"""Apple Health XML import provider.

Streams Apple's exported apple_health_export/export.xml and yields
MetricReading objects, mapping HealthKit record types onto Athlytics's
canonical metric_type vocabulary. See docs/superpowers/specs/
2026-08-16-apple-health-provider.md for the full design.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

APPLE_HEALTH_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S %z"

SOURCE = "apple_health"

# HealthKit quantity-type identifier -> (metric_type, unit, aggregation).
# aggregation is "sum" for cumulative daily totals, "mean" for point-in-time
# readings averaged across the day's samples. For metric_types Garmin also
# reports (resting_hr, hrv, vo2max, weight, spo2, respiration, steps), the
# unit string here MUST exactly match the literal GarminProvider already
# uses for that metric_type (core/providers/garmin.py) -- same metric_type
# with two different unit strings would corrupt MetricSummary.unit and any
# UI/MCP text that assumes one unit per metric_type. walking_asymmetry and
# walking_steadiness have no Garmin equivalent, so their unit is free to
# choose ("percent", matching HealthKit's own percentage semantics).
HK_QUANTITY_MAP: dict[str, tuple[str, str, str]] = {
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_hr", "bpm", "mean"),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv", "ms", "mean"),
    "HKQuantityTypeIdentifierVO2Max": ("vo2max", "ml/kg/min", "mean"),
    "HKQuantityTypeIdentifierBodyMass": ("weight", "kg", "mean"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("spo2", "percent", "mean"),
    "HKQuantityTypeIdentifierRespiratoryRate": ("respiration", "breaths_per_min", "mean"),
    "HKQuantityTypeIdentifierStepCount": ("steps", "count", "sum"),
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": ("walking_asymmetry", "percent", "mean"),
    "HKQuantityTypeIdentifierAppleWalkingSteadiness": ("walking_steadiness", "percent", "mean"),
    "HKQuantityTypeIdentifierAppleExerciseTime": ("exercise_minutes", "min", "sum"),
}


def parse_apple_health_timestamp(value: str) -> datetime:
    """Apple Health timestamps are offset-aware strings like
    "2026-05-01 07:30:00 -0400". Convert to naive UTC per MetricReading's
    timezone contract (core/storage/models.py)."""
    ts_aware = datetime.strptime(value, APPLE_HEALTH_TIMESTAMP_FMT)
    return ts_aware.astimezone(timezone.utc).replace(tzinfo=None)


def aggregate_daily(readings_by_day: dict[date, list[float]], aggregation: str) -> dict[date, float]:
    """Reduce each day's raw sample list to a single value. "sum" for
    cumulative types (steps, exercise minutes); "mean" for point-in-time
    types (resting heart rate, weight)."""
    if aggregation == "sum":
        return {day: sum(values) for day, values in readings_by_day.items()}
    if aggregation == "mean":
        return {day: sum(values) / len(values) for day, values in readings_by_day.items()}
    raise ValueError(f"unknown aggregation: {aggregation!r}")


SLEEP_ASLEEP_VALUES: set[str] = {
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
}

STAND_HOUR_STOOD_VALUE = "HKCategoryValueAppleStandHourStood"


def aggregate_sleep_hours(stage_records: list[tuple[str, datetime, datetime]]) -> dict[date, float]:
    """stage_records: (category value, startDate, endDate) per raw sleep-stage
    record. Only Asleep* stages count; Awake/InBed are excluded. Bucketed by
    endDate's calendar date, since a night's sleep is conventionally
    attributed to the morning it ends."""
    hours_by_day: dict[date, float] = {}
    for value, start, end in stage_records:
        if value not in SLEEP_ASLEEP_VALUES:
            continue
        day = end.date()
        duration_hours = (end - start).total_seconds() / 3600
        hours_by_day[day] = hours_by_day.get(day, 0.0) + duration_hours
    return hours_by_day


def aggregate_mindful_minutes(session_records: list[tuple[datetime, datetime]]) -> dict[date, float]:
    """session_records: (startDate, endDate) per HKCategoryTypeIdentifierMindfulSession
    record. Bucketed by startDate's calendar date."""
    minutes_by_day: dict[date, float] = {}
    for start, end in session_records:
        day = start.date()
        duration_minutes = (end - start).total_seconds() / 60
        minutes_by_day[day] = minutes_by_day.get(day, 0.0) + duration_minutes
    return minutes_by_day


def aggregate_stand_hours(stand_records: list[tuple[str, datetime]]) -> dict[date, float]:
    """stand_records: (category value, startDate) per HKCategoryTypeIdentifierAppleStandHour
    record. Counts only hours marked Stood (not Idle)."""
    counts_by_day: dict[date, float] = {}
    for value, start in stand_records:
        if value != STAND_HOUR_STOOD_VALUE:
            continue
        day = start.date()
        counts_by_day[day] = counts_by_day.get(day, 0.0) + 1
    return counts_by_day
