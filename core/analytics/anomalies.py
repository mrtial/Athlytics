"""Personal-baseline z-score anomaly detection over stored metric readings.

Statistical only (mean + sample standard deviation via the stdlib
`statistics` module) -- no ML, per the design doc's Non-Goals ("ML-based
anomaly detection (statistical baselines only)").
"""
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from core.storage import repository


@dataclass(frozen=True)
class Baseline:
    """A metric's personal baseline: the mean and sample standard deviation
    of its readings over a trailing window_days ending at as_of.

    Sample standard deviation (statistics.stdev, i.e. Bessel-corrected,
    ddof=1) is used rather than population stdev, since a rolling window of
    real-world readings is a sample of the metric's underlying variability,
    not the entire population.
    """

    metric_type: str
    window_days: int
    window_start: date
    window_end: date
    mean: float
    stdev: float
    sample_count: int


def compute_baseline(
    conn, metric_type: str, window_days: int = 90, as_of: date | None = None
) -> Baseline | None:
    """The personal baseline for metric_type over the window_days ending at
    as_of (inclusive; default today). Returns None if fewer than 2 readings
    fall in the window -- statistics.stdev is undefined for n < 2, and a
    "baseline" of a single point has no meaningful spread.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")
    as_of = as_of or date.today()
    window_start = as_of - timedelta(days=window_days - 1)
    readings = repository.get_readings(conn, metric_type, window_start, as_of)
    values = [r.value for r in readings]

    if len(values) < 2:
        return None

    return Baseline(
        metric_type=metric_type,
        window_days=window_days,
        window_start=window_start,
        window_end=as_of,
        mean=statistics.mean(values),
        stdev=statistics.stdev(values),
        sample_count=len(values),
    )


@dataclass(frozen=True)
class Anomaly:
    """One reading flagged as an outlier against its metric's personal
    baseline. The design doc's own example -- "resting HR is 2 std devs
    above your 90-day baseline" -- is exactly
    `f"{metric_type} is {abs(z_score):.1f} std devs {direction} your
    {baseline_window_days}-day baseline"` built from this dataclass's
    fields, with no recomputation needed by the caller.
    """

    metric_type: str
    timestamp: datetime
    value: float
    baseline_mean: float
    baseline_stdev: float
    z_score: float
    direction: str  # "above" or "below"
    baseline_window_days: int


def detect_anomalies(
    conn,
    metric_type: str,
    baseline_window_days: int = 90,
    z_threshold: float = 2.0,
    since: date | None = None,
    as_of: date | None = None,
) -> list[Anomaly]:
    """Flags metric_type readings between `since` and `as_of` (inclusive)
    whose z-score against the CURRENT baseline (the trailing
    baseline_window_days-day window ending at as_of) has absolute value >=
    z_threshold.

    `as_of` defaults to today. `since` defaults to the baseline's own
    window_start, i.e. by default this flags outliers within the same
    window used to compute the baseline's mean/stdev -- which is what
    "your resting HR is 2 std devs above your 90-day baseline" means: the
    baseline and the flagged reading come from the same rolling window.
    Passing an explicit `since` narrows which readings are *checked*
    without changing the baseline itself (the baseline is always the full
    baseline_window_days window ending at as_of).

    Returns [] if the baseline can't be computed (fewer than 2 readings in
    the window) or has zero variance (all readings identical -- z-score is
    undefined when stdev is 0, and "anomaly" is meaningless with no spread).

    Raises ValueError if an explicit since is after as_of.
    """
    as_of = as_of or date.today()
    if since is not None and since > as_of:
        raise ValueError(f"since ({since}) must not be after as_of ({as_of})")

    baseline = compute_baseline(conn, metric_type, baseline_window_days, as_of)
    if baseline is None or baseline.stdev == 0:
        return []

    since = since or baseline.window_start
    readings = repository.get_readings(conn, metric_type, since, as_of)

    anomalies = []
    for reading in readings:
        z_score = (reading.value - baseline.mean) / baseline.stdev
        if abs(z_score) >= z_threshold:
            anomalies.append(
                Anomaly(
                    metric_type=metric_type,
                    timestamp=reading.timestamp,
                    value=reading.value,
                    baseline_mean=baseline.mean,
                    baseline_stdev=baseline.stdev,
                    z_score=z_score,
                    direction="above" if z_score > 0 else "below",
                    baseline_window_days=baseline_window_days,
                )
            )
    return anomalies


def detect_anomalies_for_metrics(
    conn,
    metric_types: Iterable[str],
    baseline_window_days: int = 90,
    z_threshold: float = 2.0,
    since: date | None = None,
    as_of: date | None = None,
) -> list[Anomaly]:
    """detect_anomalies() for each metric_type in metric_types, flattened
    and sorted by (timestamp, metric_type).

    This is the function a future MCP `get_anomalies(since)` tool (design
    doc's MCP Layer section) wraps: that tool's signature has no
    metric_type parameter, implying it spans every metric the user has
    data for. This module deliberately does NOT own "which metric_types
    exist" -- that's a storage-layer/MCP-layer concern (the design doc's
    separate `list_metrics` tool already owns "available metric types");
    duplicating it here would create two sources of truth. The caller
    (the future get_anomalies MCP wrapper) is responsible for supplying
    the metric_types to scan, e.g. from that same list_metrics lookup.
    """
    anomalies = [
        anomaly
        for metric_type in metric_types
        for anomaly in detect_anomalies(conn, metric_type, baseline_window_days, z_threshold, since, as_of)
    ]
    return sorted(anomalies, key=lambda a: (a.timestamp, a.metric_type))
