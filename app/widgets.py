from datetime import date, timedelta

from core.analytics import detect_anomalies_for_metrics, get_trend
from core.storage import repository

WIDGET_WINDOW_DAYS = 7


def build_dashboard_widgets(conn, metric_types: list[str], as_of: date | None = None) -> dict:
    """Trend + anomaly data for a persona's metric_types, ready for template
    rendering. Every dashboard widget operates on one metric_type at a time
    through core.analytics's own per-metric-type functions -- no
    cross-metric correlation logic lives here or anywhere else in this
    plan (design doc Non-Goals: "Pre-built cross-metric correlation views
    in the dashboard").
    """
    trends = {metric_type: get_trend(conn, metric_type, WIDGET_WINDOW_DAYS, as_of) for metric_type in metric_types}
    anomalies = detect_anomalies_for_metrics(conn, metric_types, since=as_of, as_of=as_of) if metric_types else []
    return {"trends": trends, "anomalies": anomalies}


def build_metric_detail(conn, metric_type: str, days: int = WIDGET_WINDOW_DAYS, as_of: date | None = None) -> dict:
    """One point per calendar day over the trailing `days` ending at as_of
    (inclusive) for a single metric_type, for the dashboard's click-to-expand
    detail chart. Days with no reading get value=None (gaps are shown, never
    interpolated) rather than being dropped, so the chart's x-axis is always
    a dense, evenly-spaced 7-day window. A day with multiple readings (e.g.
    steps logged per-activity) is averaged into a single point.
    """
    as_of = as_of or date.today()
    start = as_of - timedelta(days=days - 1)
    readings = repository.get_readings(conn, metric_type, start, as_of)

    values_by_day: dict[date, list[float]] = {}
    unit = None
    for reading in readings:
        values_by_day.setdefault(reading.timestamp.date(), []).append(reading.value)
        unit = reading.unit

    points = []
    day = start
    while day <= as_of:
        day_values = values_by_day.get(day)
        value = sum(day_values) / len(day_values) if day_values else None
        points.append({"date": day.isoformat(), "value": value})
        day += timedelta(days=1)

    return {"metric_type": metric_type, "unit": unit, "points": points}


def format_activity_for_display(activity, unit: str = "km") -> dict:
    type_display_map = {
        "running": ("Running", "flag"),
        "cycling": ("Cycling", "disc"),
        "swimming": ("Swimming", "droplet"),
        "walking": ("Walking", "navigation"),
        "hiking": ("Hiking", "compass"),
        "strength_training": ("Strength", "zap"),
        "cardio": ("Cardio", "activity"),
        "yoga": ("Yoga", "sun"),
        "other": ("Workout", "award"),
    }
    label, icon = type_display_map.get(
        activity.activity_type,
        (activity.activity_type.replace("_", " ").title(), "activity"),
    )

    dt = activity.start_time
    date_str = dt.strftime("%a, %d %b")
    time_str = dt.strftime("%H:%M")

    dur_sec = int(activity.duration_seconds)
    hours = dur_sec // 3600
    minutes = (dur_sec % 3600) // 60
    seconds = dur_sec % 60
    if hours > 0:
        duration_formatted = f"{hours}h {minutes}m"
    elif minutes > 0:
        duration_formatted = f"{minutes}m {seconds:02d}s" if seconds > 0 else f"{minutes}m"
    else:
        duration_formatted = f"{seconds}s"

    distance_formatted = None
    distance_val = None
    distance_unit = "km"
    if activity.distance_meters is not None and activity.distance_meters > 0:
        if unit == "mi":
            mi = activity.distance_meters * 0.000621371
            distance_val = mi
            distance_unit = "mi"
            distance_formatted = f"{mi:.2f} mi"
        else:
            km = activity.distance_meters / 1000.0
            distance_val = km
            distance_unit = "km"
            distance_formatted = f"{km:.2f} km"

    pace_or_speed_label = None
    pace_or_speed_val = None
    if activity.avg_speed is not None and activity.avg_speed > 0.5:
        if activity.activity_type in ("running", "walking", "hiking"):
            pace_or_speed_label = "Pace"
            if unit == "mi":
                sec_per_mi = int(1609.344 / activity.avg_speed)
                p_min = sec_per_mi // 60
                p_sec = sec_per_mi % 60
                pace_or_speed_val = f"{p_min}:{p_sec:02d} /mi"
            else:
                sec_per_km = int(1000.0 / activity.avg_speed)
                p_min = sec_per_km // 60
                p_sec = sec_per_km % 60
                pace_or_speed_val = f"{p_min}:{p_sec:02d} /km"
        elif activity.activity_type in ("cycling",):
            pace_or_speed_label = "Avg Speed"
            if unit == "mi":
                mph = activity.avg_speed * 2.23694
                pace_or_speed_val = f"{mph:.1f} mph"
            else:
                kmh = activity.avg_speed * 3.6
                pace_or_speed_val = f"{kmh:.1f} km/h"

    avg_hr = int(round(activity.avg_hr)) if activity.avg_hr is not None else None
    calories = int(round(activity.calories)) if activity.calories is not None else None

    elev_gain = None
    if activity.elevation_gain is not None and activity.elevation_gain > 0:
        if unit == "mi":
            elev_gain = f"+{int(round(activity.elevation_gain * 3.28084))} ft"
        else:
            elev_gain = f"+{int(round(activity.elevation_gain))} m"

    return {
        "id": activity.id,
        "activity_id": activity.activity_id,
        "name": activity.activity_name,
        "type": activity.activity_type,
        "sport_type": activity.sport_type,
        "sport_label": label,
        "icon": icon,
        "date_str": date_str,
        "time_str": time_str,
        "duration_formatted": duration_formatted,
        "distance_formatted": distance_formatted,
        "distance_val": distance_val,
        "distance_unit": distance_unit,
        "pace_or_speed_label": pace_or_speed_label,
        "pace_or_speed_val": pace_or_speed_val,
        "avg_hr": avg_hr,
        "calories": calories,
        "elevation_gain": elev_gain,
        "raw_start_time": activity.start_time.isoformat(),
    }


def build_recent_activities(conn, unit: str = "km", limit: int = 20) -> list[dict]:
    """Fetch and format recent workout activities for dashboard display."""
    activities = repository.get_activities(conn, limit=limit)
    return [format_activity_for_display(act, unit=unit) for act in activities]
