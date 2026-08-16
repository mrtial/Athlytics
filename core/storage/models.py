from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class MetricReading:
    """A single normalized health/fitness metric reading.

    Timezone contract: `timestamp` MUST be a naive `datetime` (tzinfo is
    None) representing wall-clock UTC. Enforced by __post_init__.

    Rationale: readings are stored as `timestamp.isoformat()` text and
    queried back out via SQLite's `date(timestamp)` (see
    core/storage/repository.py). SQLite's date()/datetime() functions
    silently normalize any timezone offset present in the string to UTC
    before computing the calendar day, while Python's
    `datetime.fromisoformat(...).date()` on the same string does NOT apply
    that normalization -- it just drops the offset and keeps the original
    date. Mixing aware and naive timestamps, or storing local-time-with-
    offset strings, would make the storage layer (SQLite) and the Python
    object disagree about which calendar day a reading belongs to.
    Requiring every timestamp to already be naive UTC removes the
    ambiguity entirely: there is no offset for either side to normalize,
    so "the calendar day" means the same thing in SQL and in Python.

    Producers of MetricReading must convert at the adapter boundary:
    - Calendar-date-keyed daily/wellness metrics (no time-of-day in the
      source data, e.g. Garmin's `calendarDate`-keyed daily endpoints):
      use midnight UTC (`datetime.combine(the_date, time.min)`) for that
      calendar date.
    - Metrics with a real event timestamp (e.g. activity start times):
      convert the source's local/offset-aware timestamp to UTC, then
      strip tzinfo (`.astimezone(timezone.utc).replace(tzinfo=None)`).
    """

    source: str
    metric_type: str
    timestamp: datetime
    value: float
    unit: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is not None:
            raise ValueError(
                "MetricReading.timestamp must be a naive datetime representing "
                f"UTC wall-clock time; got a timezone-aware value: {self.timestamp!r}. "
                "Convert to UTC and strip tzinfo before constructing MetricReading "
                "(see the class docstring's timezone contract)."
            )


@dataclass(frozen=True)
class MetricSummary:
    metric_type: str
    earliest_date: date
    latest_date: date
    reading_count: int
    unit: str


@dataclass(frozen=True)
class Report:
    id: int
    created_at: datetime
    title: str
    content: str
