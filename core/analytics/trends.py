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


@dataclass(frozen=True)
class Delta:
    """The change in a metric's rolling average between the window ending at
    as_of and the equal-length window immediately preceding it (no gap, no
    overlap: `previous.window_end == current.window_start - 1 day`).

    `absolute_change`/`percent_change` are None whenever either window has
    no data (current.average or previous.average is None), and
    `percent_change` is also None when previous.average == 0 (division by
    zero is undefined, not "infinite percent change").
    """

    metric_type: str
    window_days: int
    current: RollingAverage
    previous: RollingAverage
    absolute_change: float | None
    percent_change: float | None


def compute_delta(conn, metric_type: str, window_days: int, as_of: date | None = None) -> Delta:
    """The current window (window_days ending at as_of) vs. the equal-length
    window immediately preceding it.
    """
    as_of = as_of or date.today()
    current = rolling_average(conn, metric_type, window_days, as_of)
    previous_as_of = current.window_start - timedelta(days=1)
    previous = rolling_average(conn, metric_type, window_days, previous_as_of)

    if current.average is None or previous.average is None:
        absolute_change = None
        percent_change = None
    else:
        absolute_change = current.average - previous.average
        percent_change = (absolute_change / previous.average * 100) if previous.average != 0 else None

    return Delta(
        metric_type=metric_type,
        window_days=window_days,
        current=current,
        previous=previous,
        absolute_change=absolute_change,
        percent_change=percent_change,
    )


def week_over_week_delta(conn, metric_type: str, as_of: date | None = None) -> Delta:
    """compute_delta with a 7-day window -- this week's average vs. last week's."""
    return compute_delta(conn, metric_type, 7, as_of)


def month_over_month_delta(conn, metric_type: str, as_of: date | None = None) -> Delta:
    """compute_delta with a 30-day window -- this 30-day period's average vs. the prior one."""
    return compute_delta(conn, metric_type, 30, as_of)


@dataclass(frozen=True)
class Trend:
    """The combined "current average + delta vs. the prior period" view for
    one metric_type and window. This is the return shape a future MCP
    `get_trend(metric_type, window)` tool (design doc's MCP Layer section)
    wraps almost directly: that tool's `window` parameter maps to
    `window_days` below.
    """

    metric_type: str
    window_days: int
    current: RollingAverage
    delta: Delta


def get_trend(conn, metric_type: str, window_days: int, as_of: date | None = None) -> Trend:
    """The rolling average and period-over-period delta for metric_type over
    window_days ending at as_of (default: today). Backs the future MCP
    get_trend(metric_type, window) tool and dashboard trend widgets.
    """
    delta = compute_delta(conn, metric_type, window_days, as_of)
    return Trend(metric_type=metric_type, window_days=window_days, current=delta.current, delta=delta)
