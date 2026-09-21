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


@dataclass(frozen=True)
class StrengthSet:
    """A single set within a strength-training workout (currently Tonal-only),
    holding per-set detail (weight, reps, 1RM, power, ROM, struggling score)
    that Activity's cardio-shaped columns (distance/speed/elevation) can't
    represent.

    Timezone contract: `created_at` and `occurred_at` MUST be naive `datetime`s
    representing UTC wall-clock time, consistent with Activity/MetricReading.
    """

    id: str  # f"{source}:{activity_id}:{set_index}"
    activity_id: str  # FK-by-convention to activity.id (f"{source}:{activity_id}")
    movement_id: str
    movement_name: str | None
    set_index: int  # ordering within the workout
    is_warm_up: bool
    reps: int | None
    weight_lbs: float | None
    volume_lbs: float | None
    one_rep_max: float | None
    max_power_watts: float | None
    rom_inches: float | None
    struggling_score: float | None
    side: str | None  # 'Left' | 'Right' | 'Both'
    created_at: datetime  # write-time bookkeeping only -- NOT the workout date
    occurred_at: datetime  # the real set/workout timestamp; use this for chronology

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is not None:
            raise ValueError(
                "StrengthSet.created_at must be a naive datetime representing UTC wall-clock time; "
                f"got a timezone-aware value: {self.created_at!r}."
            )
        if self.occurred_at.tzinfo is not None:
            raise ValueError(
                "StrengthSet.occurred_at must be a naive datetime representing UTC wall-clock time; "
                f"got a timezone-aware value: {self.occurred_at!r}."
            )


@dataclass(frozen=True)
class SleepSession:
    """One night's Garmin sleep summary, from the rich get_sleep_data()
    endpoint (distinct from the flat sleep_score/sleep_duration
    MetricReadings, which come from the summary get_sleep_daily() endpoint).

    Timezone contract: all *_utc fields MUST be naive datetimes representing
    UTC wall-clock time, consistent with MetricReading/Activity. The
    *_local fields are naive datetimes representing local wall-clock time
    as Garmin reported it -- no further timezone conversion is performed;
    they exist for bedtime/wake-time display and consistency analysis only.
    """

    id: str
    calendar_date: date
    sleep_start_utc: datetime | None
    sleep_end_utc: datetime | None
    sleep_start_local: datetime | None
    sleep_end_local: datetime | None
    total_sleep_seconds: float | None
    nap_time_seconds: float | None
    deep_sleep_seconds: float | None
    light_sleep_seconds: float | None
    rem_sleep_seconds: float | None
    awake_sleep_seconds: float | None
    unmeasurable_sleep_seconds: float | None
    awake_count: int | None
    restless_moments_count: int | None
    avg_sleep_stress: float | None
    avg_heart_rate: float | None
    avg_overnight_hrv: float | None
    avg_respiration: float | None
    lowest_respiration: float | None
    highest_respiration: float | None
    overall_score: float | None
    overall_score_qualifier: str | None
    duration_qualifier: str | None
    stress_qualifier: str | None
    awake_count_qualifier: str | None
    restlessness_qualifier: str | None
    rem_percentage: float | None
    rem_percentage_qualifier: str | None
    light_percentage: float | None
    light_percentage_qualifier: str | None
    deep_percentage: float | None
    deep_percentage_qualifier: str | None
    sleep_need_baseline_minutes: int | None
    sleep_need_actual_minutes: int | None
    sleep_need_feedback: str | None
    score_feedback: str | None
    score_insight: str | None
    score_personalized_insight: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.sleep_start_utc is not None and self.sleep_start_utc.tzinfo is not None:
            raise ValueError(
                "SleepSession.sleep_start_utc must be a naive datetime representing "
                f"UTC wall-clock time; got a timezone-aware value: {self.sleep_start_utc!r}."
            )
        if self.sleep_end_utc is not None and self.sleep_end_utc.tzinfo is not None:
            raise ValueError(
                "SleepSession.sleep_end_utc must be a naive datetime representing "
                f"UTC wall-clock time; got a timezone-aware value: {self.sleep_end_utc!r}."
            )
        if self.created_at.tzinfo is not None:
            raise ValueError(
                "SleepSession.created_at must be a naive datetime representing "
                f"UTC wall-clock time; got a timezone-aware value: {self.created_at!r}."
            )


@dataclass(frozen=True)
class SleepStageSegment:
    id: str
    sleep_session_id: str
    segment_index: int
    stage: str  # 'deep' | 'light' | 'rem' | 'awake'
    start_utc: datetime
    end_utc: datetime
    duration_seconds: float

    def __post_init__(self) -> None:
        if self.start_utc.tzinfo is not None:
            raise ValueError(
                "SleepStageSegment.start_utc must be a naive datetime representing "
                f"UTC wall-clock time; got a timezone-aware value: {self.start_utc!r}."
            )
        if self.end_utc.tzinfo is not None:
            raise ValueError(
                "SleepStageSegment.end_utc must be a naive datetime representing "
                f"UTC wall-clock time; got a timezone-aware value: {self.end_utc!r}."
            )


@dataclass(frozen=True)
class SleepRestlessMoment:
    sleep_session_id: str
    occurred_at_utc: datetime
    value: int

    def __post_init__(self) -> None:
        if self.occurred_at_utc.tzinfo is not None:
            raise ValueError(
                "SleepRestlessMoment.occurred_at_utc must be a naive datetime representing "
                f"UTC wall-clock time; got a timezone-aware value: {self.occurred_at_utc!r}."
            )


@dataclass(frozen=True)
class TonalWorkoutMeta:
    """Program/guided-workout metadata for one Tonal activity (currently
    Tonal-only, same rationale as StrengthSet), sourced from the per-workout
    detail endpoint's `contentCard` object -- absent from the bulk
    workout-history list, so this is only populated for workouts that have
    had their detail fetched (on-demand via get_workout_detail, or eagerly
    during sync via hydrate_recent_strength_sets).

    None fields mean either "not fetched yet" or "this was a free-lift
    workout with no Tonal program attached" (contentCard itself is null for
    those) -- the two aren't distinguished at this layer.

    Timezone contract: `created_at` MUST be a naive `datetime` representing
    UTC wall-clock time, consistent with Activity/StrengthSet.
    """

    activity_id: str  # FK-by-convention to activity.id (f"{source}:{activity_id}")
    program_name: str | None
    workout_title: str | None
    target_area: str | None
    level: str | None
    program_week: int | None
    program_day: int | None
    is_guided_workout: bool
    percent_completed: int | None
    active_duration_seconds: int | None
    created_at: datetime  # write-time bookkeeping only -- NOT the workout date

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is not None:
            raise ValueError(
                "TonalWorkoutMeta.created_at must be a naive datetime representing UTC wall-clock time; "
                f"got a timezone-aware value: {self.created_at!r}."
            )

