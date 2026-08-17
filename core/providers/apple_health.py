"""Apple Health XML import provider.

Streams Apple's exported apple_health_export/export.xml and yields
MetricReading objects, mapping HealthKit record types onto Athlytics's
canonical metric_type vocabulary. See docs/superpowers/specs/
2026-08-16-apple-health-provider.md for the full design.
"""
from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timezone
from io import BytesIO
from typing import Iterator

from core.storage.models import MetricReading

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


def apple_health_local_date(value: str) -> date:
    """The calendar date in the source's own timezone, parsed directly
    from the offset-aware string before any UTC conversion -- used for
    day-bucketing so a reading is attributed to the day it happened
    locally, matching how Garmin's own calendarDate already works.
    Contrast with parse_apple_health_timestamp, which converts to UTC
    and is used only for elapsed-duration math (sleep/mindful session
    length), where local vs. UTC makes no difference since it's a
    difference between two instants, not a calendar-day lookup.
    """
    return datetime.strptime(value, APPLE_HEALTH_TIMESTAMP_FMT).date()


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
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
}

STAND_HOUR_STOOD_VALUE = "HKCategoryValueAppleStandHourStood"


def aggregate_sleep_hours(stage_records: list[tuple[str, datetime, datetime, date]]) -> dict[date, float]:
    """stage_records: (category value, startDate, endDate, local_end_date)
    -- local_end_date is endDate's calendar date in the source's own
    timezone (not UTC-derived), used for bucketing so a night's sleep is
    attributed to the correct local morning it ends. Only Asleep* stages
    count; Awake/InBed are excluded."""
    hours_by_day: dict[date, float] = {}
    for value, start, end, local_end_date in stage_records:
        if value not in SLEEP_ASLEEP_VALUES:
            continue
        duration_hours = (end - start).total_seconds() / 3600
        hours_by_day[local_end_date] = hours_by_day.get(local_end_date, 0.0) + duration_hours
    return hours_by_day


def aggregate_mindful_minutes(session_records: list[tuple[datetime, datetime, date]]) -> dict[date, float]:
    """session_records: (startDate, endDate, local_start_date) per
    HKCategoryTypeIdentifierMindfulSession record. Bucketed by
    local_start_date (the source's own timezone), not a UTC-derived date."""
    minutes_by_day: dict[date, float] = {}
    for start, end, local_start_date in session_records:
        duration_minutes = (end - start).total_seconds() / 60
        minutes_by_day[local_start_date] = minutes_by_day.get(local_start_date, 0.0) + duration_minutes
    return minutes_by_day


def aggregate_stand_hours(stand_records: list[tuple[str, date]]) -> dict[date, float]:
    """stand_records: (category value, local_start_date) per
    HKCategoryTypeIdentifierAppleStandHour record. Counts only hours
    marked Stood (not Idle)."""
    counts_by_day: dict[date, float] = {}
    for value, local_day in stand_records:
        if value != STAND_HOUR_STOOD_VALUE:
            continue
        counts_by_day[local_day] = counts_by_day.get(local_day, 0.0) + 1
    return counts_by_day


SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
MINDFUL_TYPE = "HKCategoryTypeIdentifierMindfulSession"
STAND_HOUR_TYPE = "HKCategoryTypeIdentifierAppleStandHour"

APPLE_HEALTH_METRIC_TYPES: list[str] = [
    *[metric_type for metric_type, _, _ in HK_QUANTITY_MAP.values()],
    "sleep_duration",  # distinct from Garmin's sleep_score -- see HK_QUANTITY_MAP's comment above
    "mindful_minutes",
    "stand_hours",
]

_SLEEP_UNIT = "hr"
_MINDFUL_UNIT = "min"
_STAND_HOUR_UNIT = "count"


class AppleHealthProvider:
    name = SOURCE

    def ingest(self, payload: bytes) -> Iterator[MetricReading]:
        xml_bytes = self._extract_export_xml(payload)

        quantity_samples: dict[str, dict[date, list[float]]] = {}
        sleep_stage_records: list[tuple[str, datetime, datetime, date]] = []
        mindful_records: list[tuple[datetime, datetime, date]] = []
        stand_records: list[tuple[str, date]] = []

        for _, elem in ET.iterparse(BytesIO(xml_bytes), events=("end",)):
            if elem.tag != "Record":
                elem.clear()
                continue

            record_type = elem.get("type")

            if record_type in HK_QUANTITY_MAP:
                metric_type, _, _ = HK_QUANTITY_MAP[record_type]
                local_day = apple_health_local_date(elem.get("startDate"))
                value = float(elem.get("value"))
                quantity_samples.setdefault(metric_type, {}).setdefault(local_day, []).append(value)
            elif record_type == SLEEP_TYPE:
                start = parse_apple_health_timestamp(elem.get("startDate"))
                end = parse_apple_health_timestamp(elem.get("endDate"))
                local_end_date = apple_health_local_date(elem.get("endDate"))
                sleep_stage_records.append((elem.get("value"), start, end, local_end_date))
            elif record_type == MINDFUL_TYPE:
                start = parse_apple_health_timestamp(elem.get("startDate"))
                end = parse_apple_health_timestamp(elem.get("endDate"))
                local_start_date = apple_health_local_date(elem.get("startDate"))
                mindful_records.append((start, end, local_start_date))
            elif record_type == STAND_HOUR_TYPE:
                local_start_date = apple_health_local_date(elem.get("startDate"))
                stand_records.append((elem.get("value"), local_start_date))
            # else: unrecognized type -- skip silently, expected for a real export.

            elem.clear()

        for metric_type, samples_by_day in quantity_samples.items():
            aggregation = next(agg for mt, _, agg in HK_QUANTITY_MAP.values() if mt == metric_type)
            unit = next(u for mt, u, _ in HK_QUANTITY_MAP.values() if mt == metric_type)
            daily_values = aggregate_daily(samples_by_day, aggregation)
            for day, value in daily_values.items():
                yield MetricReading(
                    source=SOURCE,
                    metric_type=metric_type,
                    timestamp=datetime.combine(day, time.min),
                    value=value,
                    unit=unit,
                )

        for day, hours in aggregate_sleep_hours(sleep_stage_records).items():
            yield MetricReading(
                source=SOURCE, metric_type="sleep_duration", timestamp=datetime.combine(day, time.min),
                value=hours, unit=_SLEEP_UNIT,
            )

        for day, minutes in aggregate_mindful_minutes(mindful_records).items():
            yield MetricReading(
                source=SOURCE, metric_type="mindful_minutes", timestamp=datetime.combine(day, time.min),
                value=minutes, unit=_MINDFUL_UNIT,
            )

        for day, count in aggregate_stand_hours(stand_records).items():
            yield MetricReading(
                source=SOURCE, metric_type="stand_hours", timestamp=datetime.combine(day, time.min),
                value=count, unit=_STAND_HOUR_UNIT,
            )

    @staticmethod
    def _extract_export_xml(payload: bytes) -> bytes:
        with zipfile.ZipFile(BytesIO(payload)) as zf:
            for name in zf.namelist():
                if name.endswith("export.xml"):
                    return zf.read(name)
        raise ValueError("uploaded zip does not contain an export.xml")
