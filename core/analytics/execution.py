"""Training-execution helpers: turn stored activity detail (HR zone seconds,
laps, training effect/load) into the numbers the weekly check-in reasons about.

Pure functions over stored data, units in miles / min:sec per mile to match
the training plan. See docs/provider-detail-data.md for why these fields are
kept and what each one is for.
"""

from datetime import date, timedelta
import sqlite3

from core.storage import repository
from core.storage.models import Activity, ActivityLap

METERS_PER_MILE = 1609.344

_ZONE_FIELDS = ("hr_zone_1_s", "hr_zone_2_s", "hr_zone_3_s", "hr_zone_4_s", "hr_zone_5_s")
_MIN_DRIFT_LAP_METERS = 804.0  # half a mile: skips a short final lap
_MIN_DRIFT_TOTAL_METERS = 4800.0  # about 3 miles of full laps before drift means anything
_MIN_DRIFT_LAPS = 4


def format_pace_per_mile(speed_mps: float | None) -> str | None:
    """Pace as "m:ss" per mile from a speed in m/s, or None without a positive speed."""
    if speed_mps is None or speed_mps <= 0:
        return None
    total_seconds = round(METERS_PER_MILE / speed_mps)
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


_AVERAGED_SERIES_FIELDS = ("heart_rate", "speed_mps", "cadence_spm", "power_watts", "elevation_m")


def downsample_series(samples: list[dict], resolution_seconds: int) -> list[dict]:
    """Collapse raw per-second-ish samples into one point per `resolution_seconds`.

    Each sample is a dict with `elapsed_s`, `distance_m` and the fields in
    _AVERAGED_SERIES_FIELDS (any may be None). A bucket's heart rate, speed,
    cadence, power and elevation are averaged over the samples that have a value
    (None when none do); distance is the last known value in the bucket. Output
    adds `distance_miles` and a derived `pace_per_mile`. Samples need not be sorted.
    """
    buckets: dict[int, list[dict]] = {}
    for sample in sorted(samples, key=lambda s: s["elapsed_s"]):
        buckets.setdefault(int(sample["elapsed_s"] // resolution_seconds), []).append(sample)

    points = []
    for bucket in sorted(buckets):
        group = buckets[bucket]
        averaged = {}
        for field in _AVERAGED_SERIES_FIELDS:
            values = [s[field] for s in group if s.get(field) is not None]
            averaged[field] = sum(values) / len(values) if values else None
        distances = [s["distance_m"] for s in group if s.get("distance_m") is not None]
        points.append(
            {
                "t_seconds": bucket * resolution_seconds,
                "distance_miles": round(distances[-1] / METERS_PER_MILE, 3) if distances else None,
                "heart_rate": round(averaged["heart_rate"], 1) if averaged["heart_rate"] is not None else None,
                "pace_per_mile": format_pace_per_mile(averaged["speed_mps"]),
                "cadence_spm": round(averaged["cadence_spm"], 1) if averaged["cadence_spm"] is not None else None,
                "power_watts": round(averaged["power_watts"], 1) if averaged["power_watts"] is not None else None,
                "elevation_m": round(averaged["elevation_m"], 1) if averaged["elevation_m"] is not None else None,
            }
        )
    return points


def _local_date(activity: Activity) -> date:
    return (activity.start_time_local or activity.start_time).date()


def _zone_summary(activity: Activity) -> dict | None:
    """Seconds and percent of time per HR zone, or None unless all five zones are known."""
    zones = [getattr(activity, field) for field in _ZONE_FIELDS]
    if any(z is None for z in zones):
        return None
    total = sum(zones)
    if total <= 0:
        return None
    return {
        "zone_seconds": [round(z) for z in zones],
        "pct_zone_1_2": round((zones[0] + zones[1]) / total * 100, 1),
        "pct_zone_3": round(zones[2] / total * 100, 1),
        "pct_zone_4_5": round((zones[3] + zones[4]) / total * 100, 1),
    }


def _drift(laps: list[ActivityLap]) -> dict | None:
    """First-half vs last-half comparison over full laps (heart-rate drift and pace change).

    Needs at least 4 full laps and about 3 miles of them; the middle lap of an odd
    count is skipped so the two halves are the same size. `pace_change_pct` is
    positive when the later laps were slower.
    """
    full = [l for l in laps if l.distance_meters and l.distance_meters >= _MIN_DRIFT_LAP_METERS and l.duration_seconds]
    if len(full) < _MIN_DRIFT_LAPS or sum(l.distance_meters for l in full) < _MIN_DRIFT_TOTAL_METERS:
        return None
    half = len(full) // 2
    first, last = full[:half], full[-half:]

    def avg_hr(chunk: list[ActivityLap]) -> float | None:
        rated = [(l.avg_hr, l.duration_seconds) for l in chunk if l.avg_hr is not None]
        if not rated:
            return None
        return sum(hr * seconds for hr, seconds in rated) / sum(seconds for _, seconds in rated)

    def speed(chunk: list[ActivityLap]) -> float:
        return sum(l.distance_meters for l in chunk) / sum(l.duration_seconds for l in chunk)

    first_hr, last_hr = avg_hr(first), avg_hr(last)
    first_speed, last_speed = speed(first), speed(last)
    return {
        "first_half_avg_hr": round(first_hr, 1) if first_hr is not None else None,
        "second_half_avg_hr": round(last_hr, 1) if last_hr is not None else None,
        "hr_drift_bpm": round(last_hr - first_hr, 1) if first_hr is not None and last_hr is not None else None,
        "first_half_pace_per_mile": format_pace_per_mile(first_speed),
        "second_half_pace_per_mile": format_pace_per_mile(last_speed),
        "pace_change_pct": round((first_speed / last_speed - 1) * 100, 1),
        "laps_used": 2 * half,
    }


def _summarize_run(activity: Activity, laps: list[ActivityLap]) -> dict:
    zones = _zone_summary(activity)
    local_date = _local_date(activity)
    return {
        "activity_id": activity.id,
        "date": local_date.isoformat(),
        "weekday": local_date.strftime("%a"),
        "date_basis": "local" if activity.start_time_local else "utc",
        "sport_type": activity.sport_type,
        "is_treadmill": "treadmill" in (activity.sport_type or "").lower(),
        "distance_miles": round(activity.distance_meters / METERS_PER_MILE, 2) if activity.distance_meters is not None else None,
        "duration_minutes": round(activity.duration_seconds / 60, 1),
        "moving_minutes": round(activity.moving_duration_seconds / 60, 1) if activity.moving_duration_seconds is not None else None,
        "avg_pace_per_mile": format_pace_per_mile(activity.avg_speed),
        "avg_hr": activity.avg_hr,
        "max_hr": activity.max_hr,
        "zone_seconds": zones["zone_seconds"] if zones else None,
        "pct_zone_1_2": zones["pct_zone_1_2"] if zones else None,
        "pct_zone_3": zones["pct_zone_3"] if zones else None,
        "pct_zone_4_5": zones["pct_zone_4_5"] if zones else None,
        "aerobic_training_effect": activity.aerobic_training_effect,
        "anaerobic_training_effect": activity.anaerobic_training_effect,
        "training_effect_label": activity.training_effect_label,
        "training_load": round(activity.activity_training_load, 1) if activity.activity_training_load is not None else None,
        "avg_cadence_spm": activity.avg_cadence,
        "avg_power_watts": activity.avg_power,
        "laps_available": bool(laps),
        "drift": _drift(laps),
    }


def build_training_execution(
    conn: sqlite3.Connection, start_date: date, end_date: date, activity_type: str = "running"
) -> dict:
    """How each session in [start_date, end_date] was actually run, plus a weekly roll-up.

    Days are the activity's own local calendar days (never an assumed timezone);
    units are miles and min:sec per mile. Anything the data does not have (older
    activities without zone seconds, runs without laps) is None and noted, never zero.
    """
    candidates = repository.get_activities(
        conn, start_date=start_date - timedelta(days=1), end_date=end_date + timedelta(days=1),
        activity_type=activity_type, limit=1000,
    )
    in_range = sorted(
        (a for a in candidates if start_date <= _local_date(a) <= end_date),
        key=lambda a: a.start_time_local or a.start_time,
    )
    runs = [_summarize_run(a, repository.get_activity_laps(conn, a.id)) for a in in_range]

    with_zones = [a for a in in_range if _zone_summary(a) is not None]
    zone_totals = [sum(getattr(a, field) for a in with_zones) for field in _ZONE_FIELDS]
    zone_total = sum(zone_totals)
    loads = [a.activity_training_load for a in in_range if a.activity_training_load is not None]
    weekly = {
        "run_count": len(in_range),
        "total_distance_miles": round(sum((a.distance_meters or 0) for a in in_range) / METERS_PER_MILE, 2),
        "total_duration_minutes": round(sum(a.duration_seconds for a in in_range) / 60, 1),
        "total_training_load": round(sum(loads), 1) if loads else None,
        "runs_with_zone_data": len(with_zones),
        "runs_mostly_above_zone_2": sum(1 for r in runs if r["pct_zone_1_2"] is not None and r["pct_zone_1_2"] < 50),
        "pct_zone_1_2": round((zone_totals[0] + zone_totals[1]) / zone_total * 100, 1) if zone_total else None,
        "pct_zone_3": round(zone_totals[2] / zone_total * 100, 1) if zone_total else None,
        "pct_zone_4_5": round((zone_totals[3] + zone_totals[4]) / zone_total * 100, 1) if zone_total else None,
    }

    notes = []
    if any(r["is_treadmill"] for r in runs):
        notes.append("Treadmill pace and distance come from the treadmill or a foot pod and are less reliable; judge treadmill runs by heart rate and time.")
    missing_zones = len(in_range) - len(with_zones)
    if missing_zones:
        notes.append(f"{missing_zones} of {len(in_range)} runs have no HR zone data (not backfilled yet, or no heart rate recorded).")
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "activity_type": activity_type,
        "runs": runs,
        "weekly": weekly,
        "notes": notes,
    }
