"""Rolling averages and period-over-period deltas over stored metric readings.

Every function operates on a single metric_type at a time. This matches the
design doc's future `get_trend(metric_type, window)` MCP tool exactly, and
keeps trend computation independent of which metric types exist in storage
(see `core.analytics.anomalies` for the same design decision applied to
anomaly detection).
"""
import bisect
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from core.storage import repository


@dataclass(frozen=True)
class RollingAverage:
    """The mean of a metric_type's readings over a trailing window of days.

    `window_start`/`window_end` are inclusive calendar dates: the window
    covers every reading with `window_start <= timestamp.date() <=
    window_end`, i.e. `window_end - window_start + 1 == window_days`.

    `average` is None and `sample_count` is 0 when no readings fall in the
    window -- callers (dashboard widgets, MCP tool wrappers) must handle
    this "no data" case rather than assume a numeric value is always
    present, since real accounts have gaps (a metric not yet backfilled,
    a sync failure, a day the device wasn't worn).
    """

    metric_type: str
    window_days: int
    window_start: date
    window_end: date
    average: float | None
    sample_count: int


def rolling_average(conn, metric_type: str, window_days: int, as_of: date | None = None) -> RollingAverage:
    """The rolling average of metric_type over the window_days ending at
    as_of (inclusive). as_of defaults to today. window_days must be >= 1.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")
    as_of = as_of or date.today()
    return rolling_average_series(conn, metric_type, window_days, as_of, as_of)[0]


def rolling_average_series(
    conn, metric_type: str, window_days: int, start: date, end: date
) -> list[RollingAverage]:
    """One RollingAverage per calendar day in [start, end], each covering the
    trailing window_days ending that day (inclusive).

    Powers dashboard trend-chart/sparkline widgets that need the whole
    curve over time, not just the latest value (rolling_average() returns
    only the single point at as_of).

    Fetches once over the widened range [start - (window_days - 1), end]
    and slides a window across the sorted readings using bisect, rather
    than issuing one query per day.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")

    fetch_start = start - timedelta(days=window_days - 1)
    readings = repository.get_readings(conn, metric_type, fetch_start, end)
    reading_dates = [r.timestamp.date() for r in readings]
    reading_values = [r.value for r in readings]

    points = []
    day = start
    while day <= end:
        window_start = day - timedelta(days=window_days - 1)
        lo = bisect.bisect_left(reading_dates, window_start)
        hi = bisect.bisect_right(reading_dates, day)
        window_values = reading_values[lo:hi]
        average = statistics.mean(window_values) if window_values else None
        points.append(
            RollingAverage(
                metric_type=metric_type,
                window_days=window_days,
                window_start=window_start,
                window_end=day,
                average=average,
                sample_count=len(window_values),
            )
        )
        day += timedelta(days=1)
    return points
