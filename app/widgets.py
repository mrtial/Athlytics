from datetime import date

from core.analytics import detect_anomalies_for_metrics, get_trend

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
