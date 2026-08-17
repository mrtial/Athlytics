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


@dataclass(frozen=True)
class Target:
    id: str
    metric_type: str
    target_value: float
    operator: str  # 'gte', 'lte', 'eq'
    target_window: str  # 'daily', 'weekly_sum', 'weekly_avg', 'by_date'
    start_date: date
    end_date: date | None
    status: str  # 'active', 'completed', 'abandoned'
    notes: str | None
    created_at: datetime


@dataclass(frozen=True)
class TrainingPlan:
    id: str
    title: str
    goal_description: str | None
    start_date: date
    target_date: date
    plan_json: str  # JSON-encoded string
    status: str  # 'active', 'paused', 'completed', 'archived'
    created_at: datetime


@dataclass(frozen=True)
class CoachNote:
    id: str
    date: date
    category: str  # 'injury', 'nutrition', 'feeling', 'gear', 'milestone', 'general'
    note: str
    tags_json: str | None
    created_at: datetime


@dataclass(frozen=True)
class Activity:
    """A normalized workout/activity record (run, ride, swim, gym, etc.).

    Timezone contract: `start_time` and `created_at` MUST be naive `datetime`s
    representing UTC wall-clock time, consistent with MetricReading.
    """

    id: str
    source: str
    activity_id: str
    activity_name: str
    activity_type: str  # normalized: 'running', 'cycling', 'swimming', 'walking', 'strength_training', 'cardio', 'hiking', 'yoga', 'other'
    sport_type: str  # raw provider type key (e.g. 'treadmill_running', 'road_biking')
    start_time: datetime
    duration_seconds: float
    distance_meters: float | None
    calories: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_speed: float | None  # meters per second
    max_speed: float | None  # meters per second
    elevation_gain: float | None  # meters
    elevation_loss: float | None  # meters
    created_at: datetime

    def __post_init__(self) -> None:
        if self.start_time.tzinfo is not None:
            raise ValueError(
                "Activity.start_time must be a naive datetime representing UTC wall-clock time; "
                f"got a timezone-aware value: {self.start_time!r}."
            )
        if self.created_at.tzinfo is not None:
            raise ValueError(
                "Activity.created_at must be a naive datetime representing UTC wall-clock time; "
                f"got a timezone-aware value: {self.created_at!r}."
            )

