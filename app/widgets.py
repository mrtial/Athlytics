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
