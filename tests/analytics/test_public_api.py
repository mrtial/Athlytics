import core.analytics as analytics


def test_public_api_exports_all_trend_and_anomaly_symbols():
    expected = {
        "Anomaly",
        "Baseline",
        "compute_baseline",
        "detect_anomalies",
        "detect_anomalies_for_metrics",
        "Delta",
        "RollingAverage",
        "Trend",
        "compute_delta",
        "get_trend",
        "month_over_month_delta",
        "rolling_average",
        "rolling_average_series",
        "week_over_week_delta",
    }

    assert set(analytics.__all__) == expected
    for name in expected:
        assert hasattr(analytics, name)
