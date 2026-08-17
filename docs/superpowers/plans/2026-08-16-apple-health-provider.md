# Apple Health Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Apple Health as a second, import-based data provider alongside Garmin's pull-based sync, so one user can connect either or both and get merged trends/anomalies/dashboard widgets from their own multiple wearables.

**Architecture:** A new `ImportProvider` protocol (push/import, contrasted with the existing pull `Provider`) backs `core/providers/apple_health.py`, which streams Apple's exported XML via `iterparse`, maps HealthKit record types onto Athlytics `metric_type` strings, and aggregates high-frequency samples to one row per `(metric_type, date)`. A new `metric_source_priority` table plus a window-function change to `repository.get_readings()` resolves metric_types both Garmin and Apple Health report for the same day. Onboarding's connect step becomes a provider choice; the dashboard filters each persona's metric list down to whichever source(s) are actually connected.

**Tech Stack:** Python 3.11, FastAPI, SQLite (stdlib `sqlite3`), stdlib `xml.etree.ElementTree` + `zipfile` (no new dependencies), pytest, Jinja2.

**Spec:** `docs/superpowers/specs/2026-08-16-apple-health-provider.md`

## Global Constraints

- No new production dependencies — XML parsing and zip extraction use only `xml.etree.ElementTree` and `zipfile` (stdlib).
- `MetricReading.timestamp` must always be naive UTC, calendar-day granularity for wellness metrics (existing contract, `core/storage/models.py`) — every Apple Health reading yielded by the provider must already satisfy this before construction.
- `core/` never imports from `app/` (existing layering — `core` is the shared library, `app`/`mcp_server` are the two front doors).
- Every new/changed route stays gated by `require_admin_page` exactly like existing settings/onboarding routes — no new auth model for the manual-upload path (only the future push endpoint would need one, and that's out of scope here).
- Follow existing code style: type hints on public functions, dataclasses for structured returns, module-level constants for fixed tables (matching `core/settings.py`'s `PERSONA_METRIC_TYPES`, `core/providers/garmin.py`'s `_registry` pattern).

---

### Task 1: `ImportProvider` protocol

**Files:**
- Modify: `core/providers/base.py`
- Test: `tests/providers/test_base.py` (new file)

**Interfaces:**
- Produces: `ImportProvider` protocol with `name: str` and `ingest(self, payload: bytes) -> Iterator[MetricReading]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_base.py
from datetime import datetime
from typing import Iterator

from core.providers.base import ImportProvider
from core.storage.models import MetricReading


class _FakeImportProvider:
    name = "fake_import"

    def ingest(self, payload: bytes) -> Iterator[MetricReading]:
        yield MetricReading(
            source="fake_import", metric_type="steps", timestamp=datetime(2026, 1, 1), value=100.0, unit="count"
        )


def test_import_provider_protocol_is_satisfied_structurally():
    provider: ImportProvider = _FakeImportProvider()
    results = list(provider.ingest(b"whatever"))
    assert results[0].metric_type == "steps"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_base.py -v`
Expected: FAIL with `ImportError: cannot import name 'ImportProvider'`

- [ ] **Step 3: Add the protocol**

In `core/providers/base.py`, add alongside the existing `Provider` protocol:

```python
from typing import Iterator, Protocol


class ImportProvider(Protocol):
    name: str

    def ingest(self, payload: bytes) -> Iterator[MetricReading]:
        ...
```

(Add `Iterator` to the existing `from typing import Protocol` import line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_base.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/base.py tests/providers/test_base.py
git commit -m "feat: add ImportProvider protocol for push/import-based data sources"
```

---

### Task 2: Extract `GARMIN_METRIC_TYPES` as a static, import-safe constant

**Files:**
- Modify: `core/providers/garmin.py`
- Test: `tests/providers/test_garmin.py` (existing file already has `test_supported_metric_types_reflects_registered_parsers` — must keep passing unchanged)

**Interfaces:**
- Produces: `GARMIN_METRIC_TYPES: list[str]`, importable from `core.providers.garmin` without instantiating `GarminProvider` (no login).

**Context:** `GarminProvider.supported_metric_types()` (garmin.py:589) currently returns `list(self._registry.keys())`, and `_registry` is only built in `__init__`, which performs a real Garmin login. Later tasks (dashboard per-source filtering) need to know Garmin's metric type list *without* logging in. This task hoists the list to a module-level constant and has both `_registry`'s construction and `supported_metric_types()` reference it, so there is exactly one source of truth.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/providers/test_garmin.py
from core.providers.garmin import GARMIN_METRIC_TYPES


def test_garmin_metric_types_constant_is_importable_without_instantiation():
    # No CredentialStore, no login -- this must work as a bare import.
    assert GARMIN_METRIC_TYPES == [
        "resting_hr", "hrv", "vo2max", "body_battery", "weight", "sleep_score",
        "steps", "stress", "respiration", "spo2", "training_load",
        "race_predictor_5k", "race_predictor_10k", "race_predictor_half_marathon",
        "race_predictor_marathon", "activity_duration", "activity_distance", "activity_calories",
    ]


def test_garmin_metric_types_constant_matches_live_registry(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)

    assert GARMIN_METRIC_TYPES == list(provider._registry.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_garmin.py -v -k garmin_metric_types`
Expected: FAIL with `ImportError: cannot import name 'GARMIN_METRIC_TYPES'`

- [ ] **Step 3: Add the constant and wire it in**

In `core/providers/garmin.py`, add near the top of the module (after imports, before `class GarminAuthError`):

```python
GARMIN_METRIC_TYPES: list[str] = [
    "resting_hr",
    "hrv",
    "vo2max",
    "body_battery",
    "weight",
    "sleep_score",
    "steps",
    "stress",
    "respiration",
    "spo2",
    "training_load",
    "race_predictor_5k",
    "race_predictor_10k",
    "race_predictor_half_marathon",
    "race_predictor_marathon",
    "activity_duration",
    "activity_distance",
    "activity_calories",
]
```

Then in `GarminProvider.supported_metric_types()`, change:

```python
def supported_metric_types(self) -> list[str]:
    return list(self._registry.keys())
```

to:

```python
def supported_metric_types(self) -> list[str]:
    return list(GARMIN_METRIC_TYPES)
```

Leave `_registry`'s own construction in `__init__` untouched — it still maps each key to its fetch callable; only `supported_metric_types()`'s return value changes source. The two tests above enforce that `GARMIN_METRIC_TYPES` and `_registry`'s keys never drift apart.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_garmin.py -v`
Expected: All PASS, including the pre-existing `test_supported_metric_types_reflects_registered_parsers`.

- [ ] **Step 5: Commit**

```bash
git add core/providers/garmin.py tests/providers/test_garmin.py
git commit -m "refactor: extract GARMIN_METRIC_TYPES as a static, login-free constant"
```

---

### Task 3: `metric_source_priority` table + repository get/set helpers

**Files:**
- Modify: `core/storage/db.py`
- Modify: `core/storage/repository.py`
- Test: `tests/storage/test_repository.py`

**Interfaces:**
- Produces: `repository.get_source_priority(conn, metric_type: str) -> str | None`, `repository.set_source_priority(conn, metric_type: str, source: str) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/storage/test_repository.py
def test_get_source_priority_returns_none_when_unset(tmp_path):
    conn = connect(tmp_path / "test.db")
    assert repository.get_source_priority(conn, "resting_hr") is None


def test_set_and_get_source_priority_roundtrip(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_source_priority(conn, "resting_hr", "garmin")

    assert repository.get_source_priority(conn, "resting_hr") == "garmin"


def test_set_source_priority_overwrites_existing_value(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_source_priority(conn, "resting_hr", "garmin")
    repository.set_source_priority(conn, "resting_hr", "apple_health")

    assert repository.get_source_priority(conn, "resting_hr") == "apple_health"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_repository.py -v -k source_priority`
Expected: FAIL — `metric_source_priority` table doesn't exist / `get_source_priority` not defined.

- [ ] **Step 3: Add the schema and helpers**

In `core/storage/db.py`, add to the `SCHEMA` string (after the `sync_checkpoint` table definition):

```sql
CREATE TABLE IF NOT EXISTS metric_source_priority (
    metric_type TEXT PRIMARY KEY,
    preferred_source TEXT NOT NULL
);
```

In `core/storage/repository.py`, add after `set_checkpoint`:

```python
def get_source_priority(conn: sqlite3.Connection, metric_type: str) -> str | None:
    row = conn.execute(
        "SELECT preferred_source FROM metric_source_priority WHERE metric_type = ?",
        (metric_type,),
    ).fetchone()
    return row[0] if row else None


def set_source_priority(conn: sqlite3.Connection, metric_type: str, source: str) -> None:
    conn.execute(
        """
        INSERT INTO metric_source_priority (metric_type, preferred_source)
        VALUES (?, ?)
        ON CONFLICT(metric_type) DO UPDATE SET preferred_source = excluded.preferred_source
        """,
        (metric_type, source),
    )
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_repository.py -v -k source_priority`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/storage/db.py core/storage/repository.py tests/storage/test_repository.py
git commit -m "feat: add metric_source_priority table and repository helpers"
```

---

### Task 4: Source-priority reconciliation in `get_readings()`

**Files:**
- Modify: `core/storage/repository.py`
- Test: `tests/storage/test_repository.py`

**Interfaces:**
- Consumes: `get_source_priority` from Task 3.
- Produces: `get_readings()` keeps its existing signature and return type (`list[MetricReading]`), but now returns at most one row per `(metric_type, date)` when multiple sources report that day.

**Context:** Per the spec, `DEFAULT_SOURCE_PRIORITY = ["garmin", "apple_health"]` covers any `metric_type` with no explicit override row. When two sources both have a reading for the same `(metric_type, date)`, keep only the preferred source's row; if only one source has data that day, keep it regardless of preference.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/storage/test_repository.py
from datetime import date, datetime

def test_get_readings_prefers_garmin_by_default_on_overlap(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_reading = MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 50.0, "bpm")
    apple_reading = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 1), 55.0, "bpm")
    repository.upsert_readings(conn, [garmin_reading, apple_reading])

    result = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [garmin_reading]


def test_get_readings_respects_explicit_source_priority_override(tmp_path):
    conn = connect(tmp_path / "test.db")
    garmin_reading = MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 50.0, "bpm")
    apple_reading = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 1), 55.0, "bpm")
    repository.upsert_readings(conn, [garmin_reading, apple_reading])
    repository.set_source_priority(conn, "resting_hr", "apple_health")

    result = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [apple_reading]


def test_get_readings_uses_the_only_available_source_regardless_of_priority(tmp_path):
    conn = connect(tmp_path / "test.db")
    apple_only = MetricReading("apple_health", "steps", datetime(2026, 1, 1), 8000.0, "count")
    repository.upsert_readings(conn, [apple_only])

    result = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [apple_only]


def test_get_readings_reconciles_per_day_independently(tmp_path):
    conn = connect(tmp_path / "test.db")
    day1_garmin = MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 50.0, "bpm")
    day1_apple = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 1), 55.0, "bpm")
    day2_apple_only = MetricReading("apple_health", "resting_hr", datetime(2026, 1, 2), 52.0, "bpm")
    repository.upsert_readings(conn, [day1_garmin, day1_apple, day2_apple_only])

    result = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date(2026, 1, 2))

    assert result == [day1_garmin, day2_apple_only]


def test_get_readings_keeps_multiple_same_source_readings_on_overlapping_day(tmp_path):
    # Regression guard: reconciliation must only drop rows from a strictly
    # lower-priority source, never collapse several same-source, same-day
    # readings (e.g. intraday steps entries) down to one.
    conn = connect(tmp_path / "test.db")
    garmin_morning = MetricReading("garmin", "steps", datetime(2026, 1, 1, 8, 0), 100.0, "count")
    garmin_evening = MetricReading("garmin", "steps", datetime(2026, 1, 1, 20, 0), 200.0, "count")
    apple_reading = MetricReading("apple_health", "steps", datetime(2026, 1, 1, 12, 0), 9000.0, "count")
    repository.upsert_readings(conn, [garmin_morning, garmin_evening, apple_reading])

    result = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))

    assert result == [garmin_morning, garmin_evening]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_repository.py -v -k "priority or reconciles or only_available"`
Expected: FAIL — current `get_readings()` returns both rows for the overlapping day.

- [ ] **Step 3: Implement the reconciliation query**

Replace `get_readings()` in `core/storage/repository.py` with:

**Note on the SQL below (already corrected from an earlier draft that used `ROW_NUMBER() ... WHERE rn = 1`):** a plain `ROW_NUMBER()` assigns a *distinct* number even to multiple rows from the same source on the same day (e.g. several intraday steps entries), and filtering to `rn = 1` would silently drop all but one of them — breaking the pre-existing `test_build_metric_detail_averages_multiple_readings_on_same_day`. Use `MIN(rank) OVER (...)` instead and keep every row matching that best rank: this drops rows only from a strictly lower-priority source when a higher-priority source is also present that day, and leaves same-source multi-reading days untouched (they all share the same rank, so `rank = best_rank` keeps all of them).

```python
DEFAULT_SOURCE_PRIORITY: list[str] = ["garmin", "apple_health"]


def get_readings(conn: sqlite3.Connection, metric_type: str, start: date, end: date) -> list[MetricReading]:
    override_row = conn.execute(
        "SELECT preferred_source FROM metric_source_priority WHERE metric_type = ?",
        (metric_type,),
    ).fetchone()
    preferred_source = override_row[0] if override_row else None

    # Rank each row's source: the override (if any) ranks first, then
    # DEFAULT_SOURCE_PRIORITY in order, then anything else last. Expressed
    # as a SQL CASE so reconciliation happens in one query rather than
    # post-filtering in Python.
    priority_order = [preferred_source] if preferred_source else []
    priority_order += [s for s in DEFAULT_SOURCE_PRIORITY if s != preferred_source]

    case_clauses = " ".join(
        f"WHEN source = ? THEN {rank}" for rank, _ in enumerate(priority_order)
    )
    case_params = list(priority_order)
    fallback_rank = len(priority_order)
    rank_expr = f"(CASE {case_clauses} ELSE {fallback_rank} END)"

    rows = conn.execute(
        f"""
        SELECT source, metric_type, timestamp, value, unit
        FROM (
            SELECT *,
                {rank_expr} AS rank,
                MIN({rank_expr}) OVER (
                    PARTITION BY metric_type, date(timestamp)
                ) AS best_rank
            FROM metric_reading
            WHERE metric_type = ? AND date(timestamp) BETWEEN date(?) AND date(?)
        )
        WHERE rank = best_rank
        ORDER BY timestamp ASC
        """,
        (*case_params, *case_params, metric_type, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        MetricReading(
            source=row[0],
            metric_type=row[1],
            timestamp=datetime.fromisoformat(row[2]),
            value=row[3],
            unit=row[4],
        )
        for row in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_repository.py -v`
Expected: All PASS, including every pre-existing `get_readings` test (single-source cases are unaffected — `ROW_NUMBER()` is always 1 when only one row exists per day).

- [ ] **Step 5: Commit**

```bash
git add core/storage/repository.py tests/storage/test_repository.py
git commit -m "feat: reconcile overlapping multi-source readings by per-metric priority"
```

---

### Task 5: `has_synced_data()` helper

**Files:**
- Modify: `core/storage/repository.py`
- Test: `tests/storage/test_repository.py`

**Interfaces:**
- Produces: `repository.has_synced_data(conn, source: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/storage/test_repository.py
def test_has_synced_data_false_when_no_checkpoint_for_source(tmp_path):
    conn = connect(tmp_path / "test.db")
    assert repository.has_synced_data(conn, "apple_health") is False


def test_has_synced_data_true_after_a_checkpoint_is_set(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_checkpoint(conn, "apple_health", "steps", date(2026, 1, 1))

    assert repository.has_synced_data(conn, "apple_health") is True


def test_has_synced_data_is_source_specific(tmp_path):
    conn = connect(tmp_path / "test.db")
    repository.set_checkpoint(conn, "garmin", "steps", date(2026, 1, 1))

    assert repository.has_synced_data(conn, "apple_health") is False
    assert repository.has_synced_data(conn, "garmin") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_repository.py -v -k has_synced_data`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement**

Add to `core/storage/repository.py`, after `set_checkpoint`:

```python
def has_synced_data(conn: sqlite3.Connection, source: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sync_checkpoint WHERE source = ? LIMIT 1",
        (source,),
    ).fetchone()
    return row is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_repository.py -v -k has_synced_data`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/storage/repository.py tests/storage/test_repository.py
git commit -m "feat: add repository.has_synced_data for per-source connection checks"
```

---

### Task 6: `core/providers/apple_health.py` — timestamp parsing + quantity-record mapping

**Files:**
- Create: `core/providers/apple_health.py`
- Test: `tests/providers/test_apple_health.py` (new file)

**Interfaces:**
- Produces: `parse_apple_health_timestamp(value: str) -> datetime`, `HK_QUANTITY_MAP: dict[str, tuple[str, str, str]]` (identifier → (metric_type, unit, aggregation)), `aggregate_daily(readings_by_day: dict[date, list[float]], aggregation: str) -> dict[date, float]`.

**Context:** This task builds the pure-function pieces (timestamp conversion, the quantity-record type map, daily aggregation) with no XML parsing yet — that's Task 8. Quantity records in Apple's export look like `<Record type="HKQuantityTypeIdentifierStepCount" unit="count" startDate="2026-05-01 07:30:00 -0400" endDate="..." value="120"/>` — a numeric `value` attribute, read directly.

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_apple_health.py
from datetime import date, datetime

from core.providers.apple_health import HK_QUANTITY_MAP, aggregate_daily, parse_apple_health_timestamp


def test_parse_apple_health_timestamp_converts_offset_to_naive_utc():
    result = parse_apple_health_timestamp("2026-05-01 07:30:00 -0400")

    assert result == datetime(2026, 5, 1, 11, 30, 0)
    assert result.tzinfo is None


def test_parse_apple_health_timestamp_handles_utc_offset():
    result = parse_apple_health_timestamp("2026-05-01 07:30:00 +0000")

    assert result == datetime(2026, 5, 1, 7, 30, 0)


def test_hk_quantity_map_covers_shared_and_apple_only_metric_types():
    assert HK_QUANTITY_MAP["HKQuantityTypeIdentifierRestingHeartRate"] == ("resting_hr", "bpm", "mean")
    assert HK_QUANTITY_MAP["HKQuantityTypeIdentifierStepCount"] == ("steps", "count", "sum")
    assert HK_QUANTITY_MAP["HKQuantityTypeIdentifierAppleExerciseTime"] == ("exercise_minutes", "min", "sum")


def test_aggregate_daily_sums_cumulative_values():
    readings = {date(2026, 1, 1): [100.0, 200.0, 50.0]}

    result = aggregate_daily(readings, "sum")

    assert result == {date(2026, 1, 1): 350.0}


def test_aggregate_daily_averages_point_in_time_values():
    readings = {date(2026, 1, 1): [50.0, 54.0]}

    result = aggregate_daily(readings, "mean")

    assert result == {date(2026, 1, 1): 52.0}


def test_aggregate_daily_handles_multiple_days_independently():
    readings = {date(2026, 1, 1): [50.0], date(2026, 1, 2): [60.0, 70.0]}

    result = aggregate_daily(readings, "mean")

    assert result == {date(2026, 1, 1): 50.0, date(2026, 1, 2): 65.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_apple_health.py -v`
Expected: FAIL — `core/providers/apple_health.py` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# core/providers/apple_health.py
"""Apple Health XML import provider.

Streams Apple's exported apple_health_export/export.xml and yields
MetricReading objects, mapping HealthKit record types onto Athlytics's
canonical metric_type vocabulary. See docs/superpowers/specs/
2026-08-16-apple-health-provider.md for the full design.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

APPLE_HEALTH_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S %z"

SOURCE = "apple_health"

# HealthKit quantity-type identifier -> (metric_type, unit, aggregation).
# aggregation is "sum" for cumulative daily totals, "mean" for point-in-time
# readings averaged across the day's samples. For metric_types Garmin also
# reports (resting_hr, hrv, vo2max, weight, spo2, respiration, steps), the
# unit string here MUST exactly match the literal GarminProvider already
# uses for that metric_type (core/providers/garmin.py) -- same metric_type
# with two different unit strings would corrupt MetricSummary.unit and any
# UI/MCP text that assumes one unit per metric_type. walking_asymmetry and
# walking_steadiness have no Garmin equivalent, so their unit is free to
# choose ("percent", matching HealthKit's own percentage semantics).
HK_QUANTITY_MAP: dict[str, tuple[str, str, str]] = {
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_hr", "bpm", "mean"),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv", "ms", "mean"),
    "HKQuantityTypeIdentifierVO2Max": ("vo2max", "ml/kg/min", "mean"),
    "HKQuantityTypeIdentifierBodyMass": ("weight", "kg", "mean"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("spo2", "percent", "mean"),
    "HKQuantityTypeIdentifierRespiratoryRate": ("respiration", "breaths_per_min", "mean"),
    "HKQuantityTypeIdentifierStepCount": ("steps", "count", "sum"),
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": ("walking_asymmetry", "percent", "mean"),
    "HKQuantityTypeIdentifierAppleWalkingSteadiness": ("walking_steadiness", "percent", "mean"),
    "HKQuantityTypeIdentifierAppleExerciseTime": ("exercise_minutes", "min", "sum"),
}


def parse_apple_health_timestamp(value: str) -> datetime:
    """Apple Health timestamps are offset-aware strings like
    "2026-05-01 07:30:00 -0400". Convert to naive UTC per MetricReading's
    timezone contract (core/storage/models.py)."""
    ts_aware = datetime.strptime(value, APPLE_HEALTH_TIMESTAMP_FMT)
    return ts_aware.astimezone(timezone.utc).replace(tzinfo=None)


def aggregate_daily(readings_by_day: dict[date, list[float]], aggregation: str) -> dict[date, float]:
    """Reduce each day's raw sample list to a single value. "sum" for
    cumulative types (steps, exercise minutes); "mean" for point-in-time
    types (resting heart rate, weight)."""
    if aggregation == "sum":
        return {day: sum(values) for day, values in readings_by_day.items()}
    if aggregation == "mean":
        return {day: sum(values) / len(values) for day, values in readings_by_day.items()}
    raise ValueError(f"unknown aggregation: {aggregation!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_apple_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/apple_health.py tests/providers/test_apple_health.py
git commit -m "feat: add Apple Health timestamp parsing and quantity-record mapping"
```

---

### Task 7: Category-record handling (sleep, mindful minutes, stand hours)

**Files:**
- Modify: `core/providers/apple_health.py`
- Test: `tests/providers/test_apple_health.py`

**Interfaces:**
- Consumes: `parse_apple_health_timestamp` from Task 6.
- Produces: `SLEEP_ASLEEP_VALUES: set[str]`, `aggregate_sleep_hours(stage_records: list[tuple[str, datetime, datetime]]) -> dict[date, float]`, `aggregate_mindful_minutes(session_records: list[tuple[datetime, datetime]]) -> dict[date, float]`, `aggregate_stand_hours(stand_records: list[tuple[str, datetime]]) -> dict[date, float]`.

**Context:** Category records don't carry a numeric `value` attribute the way quantity records do — sleep/mindful-session duration comes from `endDate - startDate`, and stand-hour is a per-hour marker (`value="HKCategoryValueAppleStandHourStood"` or `"...Idle"`) counted as 1 if stood, 0 if idle, summed per day.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/providers/test_apple_health.py
from datetime import date, datetime

from core.providers.apple_health import (
    SLEEP_ASLEEP_VALUES,
    aggregate_mindful_minutes,
    aggregate_sleep_hours,
    aggregate_stand_hours,
)


def test_sleep_asleep_values_excludes_awake_and_in_bed():
    assert "HKCategoryValueSleepAnalysisAsleepCore" in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisAsleepDeep" in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisAsleepREM" in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisAwake" not in SLEEP_ASLEEP_VALUES
    assert "HKCategoryValueSleepAnalysisInBed" not in SLEEP_ASLEEP_VALUES


def test_aggregate_sleep_hours_sums_only_asleep_stages():
    stage_records = [
        ("HKCategoryValueSleepAnalysisAsleepCore", datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 2, 1, 0)),
        ("HKCategoryValueSleepAnalysisAsleepDeep", datetime(2026, 1, 2, 1, 0), datetime(2026, 1, 2, 3, 0)),
        ("HKCategoryValueSleepAnalysisAwake", datetime(2026, 1, 2, 3, 0), datetime(2026, 1, 2, 3, 15)),
    ]

    result = aggregate_sleep_hours(stage_records)

    # Bucketed by the night's ending date (2026-01-02): 2h Core + 2h Deep = 4.0 hours.
    assert result == {date(2026, 1, 2): 4.0}


def test_aggregate_mindful_minutes_sums_session_durations():
    session_records = [
        (datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 8, 10)),
        (datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 1, 20, 5)),
    ]

    result = aggregate_mindful_minutes(session_records)

    assert result == {date(2026, 1, 1): 15.0}


def test_aggregate_stand_hours_counts_only_stood_hours():
    stand_records = [
        ("HKCategoryValueAppleStandHourStood", datetime(2026, 1, 1, 9, 0)),
        ("HKCategoryValueAppleStandHourIdle", datetime(2026, 1, 1, 10, 0)),
        ("HKCategoryValueAppleStandHourStood", datetime(2026, 1, 1, 11, 0)),
    ]

    result = aggregate_stand_hours(stand_records)

    assert result == {date(2026, 1, 1): 2.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_apple_health.py -v -k "sleep or mindful or stand"`
Expected: FAIL — names not defined.

- [ ] **Step 3: Implement**

Add to `core/providers/apple_health.py`:

```python
SLEEP_ASLEEP_VALUES: set[str] = {
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
}

STAND_HOUR_STOOD_VALUE = "HKCategoryValueAppleStandHourStood"


def aggregate_sleep_hours(stage_records: list[tuple[str, datetime, datetime]]) -> dict[date, float]:
    """stage_records: (category value, startDate, endDate) per raw sleep-stage
    record. Only Asleep* stages count; Awake/InBed are excluded. Bucketed by
    endDate's calendar date, since a night's sleep is conventionally
    attributed to the morning it ends."""
    hours_by_day: dict[date, float] = {}
    for value, start, end in stage_records:
        if value not in SLEEP_ASLEEP_VALUES:
            continue
        day = end.date()
        duration_hours = (end - start).total_seconds() / 3600
        hours_by_day[day] = hours_by_day.get(day, 0.0) + duration_hours
    return hours_by_day


def aggregate_mindful_minutes(session_records: list[tuple[datetime, datetime]]) -> dict[date, float]:
    """session_records: (startDate, endDate) per HKCategoryTypeIdentifierMindfulSession
    record. Bucketed by startDate's calendar date."""
    minutes_by_day: dict[date, float] = {}
    for start, end in session_records:
        day = start.date()
        duration_minutes = (end - start).total_seconds() / 60
        minutes_by_day[day] = minutes_by_day.get(day, 0.0) + duration_minutes
    return minutes_by_day


def aggregate_stand_hours(stand_records: list[tuple[str, datetime]]) -> dict[date, float]:
    """stand_records: (category value, startDate) per HKCategoryTypeIdentifierAppleStandHour
    record. Counts only hours marked Stood (not Idle)."""
    counts_by_day: dict[date, float] = {}
    for value, start in stand_records:
        if value != STAND_HOUR_STOOD_VALUE:
            continue
        day = start.date()
        counts_by_day[day] = counts_by_day.get(day, 0.0) + 1
    return counts_by_day
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_apple_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/apple_health.py tests/providers/test_apple_health.py
git commit -m "feat: add Apple Health sleep/mindful-minutes/stand-hours aggregation"
```

---

### Task 8: `AppleHealthProvider.ingest()` — streaming XML parser wiring it all together

**Files:**
- Modify: `core/providers/apple_health.py`
- Test: `tests/providers/test_apple_health.py`
- Test fixture: `tests/fixtures/apple_health_export.xml` (new file)

**Interfaces:**
- Consumes: everything from Tasks 6-7.
- Produces: `AppleHealthProvider` class implementing `ImportProvider` (`name = "apple_health"`, `ingest(self, payload: bytes) -> Iterator[MetricReading]`), `APPLE_HEALTH_METRIC_TYPES: list[str]`.

**Context:** `payload` is the raw bytes of the *uploaded zip* (not a path — matches `ImportProvider.ingest(payload: bytes)` from Task 1). Extracts `apple_health_export/export.xml` from the zip in memory, then streams it with `iterparse`, clearing each element after processing so memory stays bounded regardless of export size. Unrecognized `type`/category values are skipped, not errors.

- [ ] **Step 1: Write the failing test**

First, create the fixture. **Note:** the second `RestingHeartRate` sample uses `16:00:00 -0500` (not `19:00:00 -0500` as an earlier draft had it) — `19:00 -0500` converts to `2026-01-02 00:00:00` UTC, crossing the UTC calendar-day boundary the parser buckets by, which would land it on a different day than the `07:00 -0500` sample and break the mean-of-two-samples assertion below. `16:00 -0500` stays on `2026-01-01` UTC alongside it.

```xml
<!-- tests/fixtures/apple_health_export.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Apple Watch" unit="count/min" startDate="2026-01-01 07:00:00 -0500" endDate="2026-01-01 07:00:00 -0500" value="50"/>
  <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Apple Watch" unit="count/min" startDate="2026-01-01 16:00:00 -0500" endDate="2026-01-01 16:00:00 -0500" value="54"/>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-01 08:00:00 -0500" endDate="2026-01-01 09:00:00 -0500" value="1200"/>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-01 12:00:00 -0500" endDate="2026-01-01 13:00:00 -0500" value="800"/>
  <Record type="HKCategoryTypeIdentifierMindfulSession" sourceName="iPhone" startDate="2026-01-01 08:00:00 -0500" endDate="2026-01-01 08:10:00 -0500" value="HKCategoryValueNotApplicable"/>
  <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Apple Watch" startDate="2026-01-01 23:00:00 -0500" endDate="2026-01-02 01:00:00 -0500" value="HKCategoryValueSleepAnalysisAsleepCore"/>
  <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Apple Watch" startDate="2026-01-02 01:00:00 -0500" endDate="2026-01-02 01:15:00 -0500" value="HKCategoryValueSleepAnalysisAwake"/>
  <Record type="HKCategoryTypeIdentifierAppleStandHour" sourceName="Apple Watch" startDate="2026-01-01 09:00:00 -0500" endDate="2026-01-01 10:00:00 -0500" value="HKCategoryValueAppleStandHourStood"/>
  <Record type="HKQuantityTypeIdentifierUVExposure" sourceName="iPhone" unit="count" startDate="2026-01-01 12:00:00 -0500" endDate="2026-01-01 12:00:00 -0500" value="3"/>
</HealthData>
```

Note the last record (`HKQuantityTypeIdentifierUVExposure`) is deliberately unmapped — it tests that unrecognized types are skipped, not erroring.

```python
# add to tests/providers/test_apple_health.py
import zipfile
from io import BytesIO
from pathlib import Path

from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES, AppleHealthProvider

FIXTURE_XML = (Path(__file__).parent.parent / "fixtures" / "apple_health_export.xml").read_bytes()


def _zip_payload(xml_bytes: bytes) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml_bytes)
    return buf.getvalue()


def test_apple_health_metric_types_includes_shared_and_apple_only_types():
    assert "resting_hr" in APPLE_HEALTH_METRIC_TYPES
    assert "steps" in APPLE_HEALTH_METRIC_TYPES
    assert "mindful_minutes" in APPLE_HEALTH_METRIC_TYPES
    assert "sleep_duration" in APPLE_HEALTH_METRIC_TYPES
    assert "stand_hours" in APPLE_HEALTH_METRIC_TYPES


def test_ingest_yields_aggregated_daily_readings_from_zip():
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    by_type = {r.metric_type: r for r in readings}

    assert by_type["resting_hr"].value == 52.0  # mean of 50, 54
    assert by_type["resting_hr"].source == "apple_health"
    assert by_type["resting_hr"].unit == "bpm"

    assert by_type["steps"].value == 2000.0  # sum of 1200, 800

    assert by_type["mindful_minutes"].value == 10.0

    assert by_type["sleep_duration"].value == 2.0  # 2 hours Core, Awake excluded
    assert by_type["sleep_duration"].unit == "hr"

    assert by_type["stand_hours"].value == 1.0


def test_ingest_never_writes_sleep_data_under_garmins_sleep_score_type():
    # Garmin's sleep_score is a 0-100 quality score; Apple Health's sleep data
    # here is hours-asleep -- a different physical quantity that must never
    # land under the same metric_type (see spec's Metric Mapping section).
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    metric_types = {r.metric_type for r in readings}
    assert "sleep_score" not in metric_types
    assert "sleep_duration" in metric_types


def test_ingest_skips_unrecognized_record_types_without_erroring():
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    metric_types = {r.metric_type for r in readings}
    assert "uv_exposure" not in metric_types  # HKQuantityTypeIdentifierUVExposure has no mapping


def test_ingest_produces_naive_utc_midnight_timestamps():
    provider = AppleHealthProvider()
    readings = list(provider.ingest(_zip_payload(FIXTURE_XML)))

    for r in readings:
        assert r.timestamp.tzinfo is None
        assert r.timestamp.hour == 0 and r.timestamp.minute == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_apple_health.py -v -k ingest`
Expected: FAIL — `AppleHealthProvider`/`APPLE_HEALTH_METRIC_TYPES` not defined.

- [ ] **Step 3: Implement**

Add to `core/providers/apple_health.py` (needs `import zipfile`, `import xml.etree.ElementTree as ET`, `from datetime import time`, `from io import BytesIO`, `from typing import Iterator`, `from core.storage.models import MetricReading` at the top):

```python
import zipfile
import xml.etree.ElementTree as ET
from datetime import time
from io import BytesIO
from typing import Iterator

from core.storage.models import MetricReading

SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
MINDFUL_TYPE = "HKCategoryTypeIdentifierMindfulSession"
STAND_HOUR_TYPE = "HKCategoryTypeIdentifierAppleStandHour"

APPLE_HEALTH_METRIC_TYPES: list[str] = [
    *[metric_type for metric_type, _, _ in HK_QUANTITY_MAP.values()],
    "sleep_duration",  # distinct from Garmin's sleep_score -- see HK_QUANTITY_MAP's comment above
    "mindful_minutes",
    "stand_hours",
]

_SLEEP_UNIT = "hr"
_MINDFUL_UNIT = "min"
_STAND_HOUR_UNIT = "count"


class AppleHealthProvider:
    name = SOURCE

    def ingest(self, payload: bytes) -> Iterator[MetricReading]:
        xml_bytes = self._extract_export_xml(payload)

        quantity_samples: dict[str, dict[date, list[float]]] = {}
        sleep_stage_records: list[tuple[str, datetime, datetime]] = []
        mindful_records: list[tuple[datetime, datetime]] = []
        stand_records: list[tuple[str, datetime]] = []

        for _, elem in ET.iterparse(BytesIO(xml_bytes), events=("end",)):
            if elem.tag != "Record":
                elem.clear()
                continue

            record_type = elem.get("type")

            if record_type in HK_QUANTITY_MAP:
                metric_type, _, _ = HK_QUANTITY_MAP[record_type]
                start = parse_apple_health_timestamp(elem.get("startDate"))
                value = float(elem.get("value"))
                quantity_samples.setdefault(metric_type, {}).setdefault(start.date(), []).append(value)
            elif record_type == SLEEP_TYPE:
                start = parse_apple_health_timestamp(elem.get("startDate"))
                end = parse_apple_health_timestamp(elem.get("endDate"))
                sleep_stage_records.append((elem.get("value"), start, end))
            elif record_type == MINDFUL_TYPE:
                start = parse_apple_health_timestamp(elem.get("startDate"))
                end = parse_apple_health_timestamp(elem.get("endDate"))
                mindful_records.append((start, end))
            elif record_type == STAND_HOUR_TYPE:
                start = parse_apple_health_timestamp(elem.get("startDate"))
                stand_records.append((elem.get("value"), start))
            # else: unrecognized type -- skip silently, expected for a real export.

            elem.clear()

        for metric_type, samples_by_day in quantity_samples.items():
            aggregation = next(agg for mt, _, agg in HK_QUANTITY_MAP.values() if mt == metric_type)
            unit = next(u for mt, u, _ in HK_QUANTITY_MAP.values() if mt == metric_type)
            daily_values = aggregate_daily(samples_by_day, aggregation)
            for day, value in daily_values.items():
                yield MetricReading(
                    source=SOURCE,
                    metric_type=metric_type,
                    timestamp=datetime.combine(day, time.min),
                    value=value,
                    unit=unit,
                )

        for day, hours in aggregate_sleep_hours(sleep_stage_records).items():
            yield MetricReading(
                source=SOURCE, metric_type="sleep_duration", timestamp=datetime.combine(day, time.min),
                value=hours, unit=_SLEEP_UNIT,
            )

        for day, minutes in aggregate_mindful_minutes(mindful_records).items():
            yield MetricReading(
                source=SOURCE, metric_type="mindful_minutes", timestamp=datetime.combine(day, time.min),
                value=minutes, unit=_MINDFUL_UNIT,
            )

        for day, count in aggregate_stand_hours(stand_records).items():
            yield MetricReading(
                source=SOURCE, metric_type="stand_hours", timestamp=datetime.combine(day, time.min),
                value=count, unit=_STAND_HOUR_UNIT,
            )

    @staticmethod
    def _extract_export_xml(payload: bytes) -> bytes:
        with zipfile.ZipFile(BytesIO(payload)) as zf:
            for name in zf.namelist():
                if name.endswith("export.xml"):
                    return zf.read(name)
        raise ValueError("uploaded zip does not contain an export.xml")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_apple_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/apple_health.py tests/providers/test_apple_health.py tests/fixtures/apple_health_export.xml
git commit -m "feat: add AppleHealthProvider streaming XML ingest"
```

---

### Task 9: Apple Health import route

**Files:**
- Modify: `app/data_sources.py`
- Modify: `app/routes/data_sources.py`
- Test: `tests/app/test_data_sources.py`

**Interfaces:**
- Consumes: `AppleHealthProvider` from Task 8, `repository.upsert_readings`/`repository.set_checkpoint` (existing).
- Produces: `import_apple_health(conn, payload: bytes) -> dict[str, str]` (metric_type -> "imported: N" summary, mirroring `sync_all_metrics`'s return shape) in `app/data_sources.py`; `POST /api/data-sources/apple-health/import` route.

**Context:** Following the existing convention where `app/data_sources.py` holds the plain functions and `app/routes/data_sources.py` wraps them as HTTP routes (see `connect_garmin`). Batches upserts every 500 readings per the spec, and updates `sync_checkpoint` per `metric_type` to the latest date seen in the upload — reusing the same table Garmin's scheduler already writes, giving "last imported" status for free (Task 11 reads it).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/app/test_data_sources.py
import zipfile
from io import BytesIO


def _apple_health_zip() -> bytes:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2026-01-01 08:00:00 -0500" endDate="2026-01-01 09:00:00 -0500" value="1200"/>
</HealthData>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml)
    return buf.getvalue()


def test_import_apple_health_upserts_readings_and_sets_checkpoint(tmp_path):
    from app.data_sources import import_apple_health
    from core.storage.db import connect
    from core.storage import repository
    from datetime import date

    conn = connect(tmp_path / "test.db")

    result = import_apple_health(conn, _apple_health_zip())

    assert result["steps"] == "imported: 1"
    readings = repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 1))
    assert len(readings) == 1
    assert readings[0].value == 1200.0
    assert repository.has_synced_data(conn, "apple_health") is True


def test_apple_health_import_route_requires_admin_login(client):
    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", _apple_health_zip(), "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_apple_health_import_route_succeeds_and_returns_summary(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", _apple_health_zip(), "application/zip")},
    )

    assert response.status_code == 200
    assert response.json()["steps"] == "imported: 1"


def test_apple_health_import_route_returns_400_for_invalid_zip(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", b"not a zip", "application/zip")},
    )

    assert response.status_code == 400


def test_apple_health_import_route_returns_400_for_malformed_xml_inside_valid_zip(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", b"<HealthData><Record not closed")

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", buf.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/app/test_data_sources.py -v -k apple_health`
Expected: FAIL — `import_apple_health` and the route don't exist yet.

- [ ] **Step 3: Implement**

In `app/data_sources.py`, add — **do not modify the existing `SUPPORTED_PROVIDERS = {"garmin"}` line.** That set gates the generic `POST /api/data-sources/{provider}/connect` route, which unconditionally calls `connect_garmin(...)` for any provider value that passes the check. Apple Health never uses that route (it gets its own dedicated import route below, which doesn't consult `SUPPORTED_PROVIDERS` at all) — adding `"apple_health"` to that set would let `POST /api/data-sources/apple_health/connect` slip past the 404 guard and wrongly attempt a Garmin login with whatever form fields were posted, and would break the existing test `test_connect_route_returns_404_for_unsupported_provider` in `tests/app/test_data_sources.py`. Leave that line exactly as it is; add only the following below it:

```python
from collections import defaultdict

from core.providers.apple_health import AppleHealthProvider
from core.storage import repository


def import_apple_health(conn, payload: bytes, batch_size: int = 500) -> dict[str, str]:
    """Streams payload (a zip's raw bytes) through AppleHealthProvider,
    batching upserts every batch_size readings, and updates sync_checkpoint
    per metric_type to the latest date seen -- giving Apple Health the same
    "last synced" status signal Garmin's scheduler already provides."""
    provider = AppleHealthProvider()
    counts: dict[str, int] = defaultdict(int)
    latest_date_by_type: dict[str, object] = {}
    batch: list = []

    for reading in provider.ingest(payload):
        batch.append(reading)
        counts[reading.metric_type] += 1
        day = reading.timestamp.date()
        if reading.metric_type not in latest_date_by_type or day > latest_date_by_type[reading.metric_type]:
            latest_date_by_type[reading.metric_type] = day

        if len(batch) >= batch_size:
            repository.upsert_readings(conn, batch)
            batch = []

    if batch:
        repository.upsert_readings(conn, batch)

    for metric_type, latest_date in latest_date_by_type.items():
        repository.set_checkpoint(conn, provider.name, metric_type, latest_date)

    return {metric_type: f"imported: {count}" for metric_type, count in counts.items()}
```

In `app/routes/data_sources.py`, add. The spec's Error Handling table requires malformed XML inside a valid zip to also return a clean 400, not a 500 — `xml.etree.ElementTree.iterparse` (used inside `AppleHealthProvider.ingest`, Task 8) raises `xml.etree.ElementTree.ParseError` on malformed XML, and `zipfile.ZipFile` raises `zipfile.BadZipFile` (a subclass of `OSError`, not `ValueError`) on an invalid zip — both must be caught alongside the `ValueError`/`KeyError` cases:

```python
import xml.etree.ElementTree as ET
import zipfile

from fastapi import File, UploadFile


@router.post("/api/data-sources/apple-health/import")
def import_apple_health_route(
    request: Request,
    export_file: UploadFile = File(...),
    conn=Depends(require_admin_page),
):
    payload = export_file.file.read()
    try:
        result = import_apple_health(conn, payload)
    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise HTTPException(status_code=400, detail=f"could not import Apple Health export: {exc}") from exc
    return result
```

(Add `import_apple_health` to the existing `from app.data_sources import SUPPORTED_PROVIDERS, connect_garmin` line at the top of `app/routes/data_sources.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/app/test_data_sources.py -v`
Expected: All PASS, including pre-existing Garmin tests and `test_connect_route_returns_404_for_unsupported_provider` (unaffected — `SUPPORTED_PROVIDERS` is untouched by this task).

- [ ] **Step 5: Commit**

```bash
git add app/data_sources.py app/routes/data_sources.py tests/app/test_data_sources.py
git commit -m "feat: add Apple Health import route and batched ingest orchestration"
```

---

### Task 10: `onboarding_status()` gate generalizes to "any connected source"

**Files:**
- Modify: `app/dependencies.py`
- Test: `tests/app/conftest.py` (no change expected) / new tests in `tests/app/test_onboarding_flow.py`

**Interfaces:**
- Consumes: `repository.has_synced_data` (Task 5).
- Produces: `onboarding_status()` keeps its existing signature and return values (`"admin"`/`"persona"`/`"theme"`/`"connect"`/`"complete"`), but the `"connect"` check now passes if *either* Garmin credentials exist *or* Apple Health has synced data.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/app/test_onboarding_flow.py
def test_root_redirects_to_dashboard_when_only_apple_health_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    from core.storage import repository
    from core.storage.db import connect
    from datetime import date
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "steps", date(2026, 1, 1))

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == "/dashboard"


def test_root_still_redirects_to_connect_when_neither_source_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    response = client.get("/", follow_redirects=False)

    assert response.headers["location"] == "/onboarding/connect"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/app/test_onboarding_flow.py -v -k apple_health`
Expected: FAIL — Apple-Health-only case still redirects to `/onboarding/connect` since `onboarding_status()` only checks `credential_store`.

- [ ] **Step 3: Implement**

In `app/dependencies.py`, change:

```python
def onboarding_status(conn: sqlite3.Connection, credential_store: CredentialStore) -> str:
    ...
    if get_theme(conn) is None:
        return "theme"
    if credential_store.load() is None:
        return "connect"
    return "complete"
```

to:

```python
def onboarding_status(conn: sqlite3.Connection, credential_store: CredentialStore) -> str:
    ...
    if get_theme(conn) is None:
        return "theme"
    garmin_connected = credential_store.load() is not None
    apple_health_connected = repository.has_synced_data(conn, "apple_health")
    if not garmin_connected and not apple_health_connected:
        return "connect"
    return "complete"
```

Add `from core.storage import repository` to the imports at the top of `app/dependencies.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/app/test_onboarding_flow.py -v`
Expected: All PASS, including every pre-existing onboarding test (Garmin-only path is unaffected — `garmin_connected` alone still satisfies the check).

- [ ] **Step 5: Commit**

```bash
git add app/dependencies.py tests/app/test_onboarding_flow.py
git commit -m "feat: onboarding connect step accepts either Garmin or Apple Health"
```

---

### Task 11: Onboarding connect page becomes a provider choice

**Files:**
- Modify: `app/templates/onboarding_connect.html`
- Test: `tests/app/test_data_sources.py`

**Interfaces:**
- Consumes: `POST /api/data-sources/apple-health/import` (Task 9), existing `POST /api/data-sources/garmin/connect`.

**Context:** The page currently only shows the Garmin login form. It gains a second option — an Apple Health upload form posting to the same import route Settings will use (Task 13) — so completing either satisfies onboarding per Task 10's new gate.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/app/test_data_sources.py
def test_onboarding_connect_page_shows_both_provider_options(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/onboarding/connect")

    assert response.status_code == 200
    assert "garmin" in response.text.lower()
    assert "apple health" in response.text.lower()
    assert 'action="/api/data-sources/apple-health/import"' in response.text


def test_completing_onboarding_via_apple_health_upload_reaches_dashboard(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    response = client.post(
        "/api/data-sources/apple-health/import",
        files={"export_file": ("export.zip", _apple_health_zip(), "application/zip")},
        follow_redirects=False,
    )
    assert response.status_code == 200  # import route returns a JSON summary, not a redirect

    root_response = client.get("/", follow_redirects=False)
    assert root_response.headers["location"] == "/dashboard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/app/test_data_sources.py -v -k "both_provider or via_apple_health"`
Expected: FAIL — template only has the Garmin form.

- [ ] **Step 3: Implement**

Replace the content of `app/templates/onboarding_connect.html`'s `{% block content %}` with a provider choice. Keep the existing Garmin form as-is (same action/fields), and add an Apple Health card below it:

```html
{% extends "base.html" %}
{% block title %}Connect a Data Source — Athlytics{% endblock %}
{% block content %}
<div class="auth-wrapper">
  <div class="auth-card">
    <div class="auth-header">
      <div class="app-icon">🔗</div>
      <h1 class="auth-title">Connect a Data Source</h1>
      <p class="auth-subtitle">Step 4 of 4 &bull; Choose Garmin, Apple Health, or both (you can connect the other later from Settings)</p>
    </div>

    {% if error %}
    <div class="error-banner">{{ error }}</div>
    {% endif %}

    <h2 class="section-title" style="font-size: 1rem; margin: 1.5rem 0 0.75rem;">Garmin</h2>
    <form method="post" action="/api/data-sources/garmin/connect">
      <div class="form-group">
        <label class="form-label" for="email">Garmin Account Email</label>
        <input class="form-input" type="email" id="email" name="email" placeholder="you@example.com" required>
      </div>

      <div class="form-group">
        <label class="form-label" for="password">Garmin Password</label>
        <input class="form-input" type="password" id="password" name="password" placeholder="••••••••••••" required>
      </div>

      <button type="submit" class="btn-primary">
        <span>Connect Garmin &amp; Begin Sync</span>
      </button>
    </form>

    <h2 class="section-title" style="font-size: 1rem; margin: 1.75rem 0 0.75rem;">Apple Health</h2>
    <form method="post" action="/api/data-sources/apple-health/import" enctype="multipart/form-data" id="apple-health-onboarding-form">
      <div class="form-group">
        <label class="form-label" for="export_file">Health App Export (.zip)</label>
        <input class="form-input" type="file" id="export_file" name="export_file" accept=".zip" required>
      </div>
      <p style="font-size: 0.78rem; color: var(--fg-muted); margin: 0.25rem 0 1rem;">
        Health app &rarr; Profile &rarr; Export All Health Data.
      </p>
      <button type="submit" class="btn-primary">
        <span>Import Apple Health &amp; Continue</span>
      </button>
    </form>

    <script>
      document.getElementById("apple-health-onboarding-form").addEventListener("submit", async (evt) => {
        evt.preventDefault();
        const form = evt.target;
        const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
        if (response.ok) {
          window.location.href = "/dashboard";
        } else {
          const body = await response.json().catch(() => ({}));
          alert(body.detail || "Import failed. Please check the file and try again.");
        }
      });
    </script>

    <div style="margin-top: 1.5rem; padding: 0.85rem 1rem; background: var(--surface-secondary); border-radius: var(--radius-md); border: 1px solid var(--border); font-size: 0.78rem; color: var(--fg-muted); display: flex; align-items: center; gap: 0.5rem;">
      <span>🔒</span>
      <span>Credentials and uploaded data are encrypted/stored locally and never leave your server.</span>
    </div>
  </div>
</div>
{% endblock %}
```

The `import_apple_health_route` (Task 9) returns a plain JSON summary, not a redirect — the inline script above bridges that to the same "land on /dashboard" behavior the Garmin form's server-side redirect already gives, since the import route is shared with Settings (Task 13) where a JSON response, not a redirect, is exactly what's wanted.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/app/test_data_sources.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/templates/onboarding_connect.html tests/app/test_data_sources.py
git commit -m "feat: onboarding connect step offers Garmin or Apple Health"
```

---

### Task 12: Per-source dashboard metric filtering

**Files:**
- Modify: `app/routes/dashboard.py`
- Test: `tests/app/test_dashboard.py`

**Interfaces:**
- Consumes: `GARMIN_METRIC_TYPES` (Task 2), `APPLE_HEALTH_METRIC_TYPES` (Task 8), `repository.has_synced_data` (Task 5).
- Produces: no new function — `dashboard_page()`'s `metric_types` local variable is now filtered before being passed to `build_dashboard_widgets`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/app/test_dashboard.py
def test_dashboard_shows_only_garmin_metrics_when_only_garmin_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "mindful_minutes" not in response.text  # Apple-only metric_type, not connected


def test_dashboard_shows_apple_only_metrics_when_only_apple_health_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "full_overview"})
    client.post("/onboarding/theme", data={"theme": "light"})

    from core.storage import repository
    from core.storage.db import connect
    from datetime import date
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "mindful_minutes", date(2026, 1, 1))

    response = client.get("/dashboard")

    assert response.status_code == 200
    # training_load is Garmin-only per PERSONA_METRIC_TYPES["full_overview"]; must not appear.
    assert "training_load" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/app/test_dashboard.py -v -k "only_garmin_connected or only_apple_health_connected"`
Expected: FAIL — dashboard currently shows every metric_type in the persona's list regardless of connection.

- [ ] **Step 3: Implement**

In `app/routes/dashboard.py`, add imports and filter `metric_types`:

```python
from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES
from core.providers.garmin import GARMIN_METRIC_TYPES
from core.storage import repository

PROVIDER_METRIC_TYPES = {"garmin": GARMIN_METRIC_TYPES, "apple_health": APPLE_HEALTH_METRIC_TYPES}
```

In `dashboard_page()`, change:

```python
metric_types = PERSONA_METRIC_TYPES[persona]
widgets = build_dashboard_widgets(conn, metric_types)
```

to:

```python
connected_sources = set()
if request.app.state.credential_store.load() is not None:
    connected_sources.add("garmin")
if repository.has_synced_data(conn, "apple_health"):
    connected_sources.add("apple_health")

metric_types = [
    mt for mt in PERSONA_METRIC_TYPES[persona]
    if any(mt in PROVIDER_METRIC_TYPES[s] for s in connected_sources)
]
widgets = build_dashboard_widgets(conn, metric_types)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/app/test_dashboard.py -v`
Expected: All PASS, including pre-existing dashboard tests (Garmin-connected-only fixtures already in those tests continue to show Garmin metrics normally).

- [ ] **Step 5: Commit**

```bash
git add app/routes/dashboard.py tests/app/test_dashboard.py
git commit -m "feat: filter dashboard widgets to connected data sources"
```

---

### Task 13: New metric_types wired into personas and dashboard icons

**Files:**
- Modify: `app/settings.py`
- Modify: `app/templates/dashboard.html`
- Test: `tests/app/test_dashboard.py`
- Test: `tests/app/test_settings.py` (updates a pre-existing test — see Context below)

**Interfaces:**
- Produces: `PERSONA_METRIC_TYPES["sleep_recovery_focus"]` and `["full_overview"]` gain `"sleep_duration"`, `"mindful_minutes"`, `"stand_hours"`; `PERSONA_METRIC_TYPES["strength_general_fitness"]` and `["full_overview"]` gain `"exercise_minutes"`, `"walking_asymmetry"`, `"walking_steadiness"`.

**Context — pre-existing test conflict:** `tests/app/test_settings.py` has
`test_full_overview_includes_all_eighteen_garmin_metric_types`, which asserts
`set(PERSONA_METRIC_TYPES["full_overview"]) == all_18` (exactly the 18 Garmin
types, no more). Adding the 5 new metric_types to `full_overview` breaks that
exact-equality assertion. This task updates that test alongside the
production change — not a regression to fix later, part of this task's own
Step 3/4.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/app/test_dashboard.py
from datetime import date

def test_dashboard_shows_mindful_minutes_widget_for_sleep_recovery_persona_when_apple_connected(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    client.post("/onboarding/persona", data={"persona": "sleep_recovery_focus"})
    client.post("/onboarding/theme", data={"theme": "light"})

    from core.storage import repository
    from core.storage.db import connect
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "mindful_minutes", date(2026, 1, 1))

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "mindful_minutes" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/app/test_dashboard.py -v -k mindful_minutes_widget`
Expected: FAIL — `mindful_minutes` isn't in `PERSONA_METRIC_TYPES["sleep_recovery_focus"]` yet, so Task 12's filter drops it even though Apple Health is connected.

- [ ] **Step 3: Implement**

In `app/settings.py`, update `PERSONA_METRIC_TYPES`:

```python
PERSONA_METRIC_TYPES: dict[str, list[str]] = {
    "endurance_runner": [
        "vo2max", "resting_hr", "hrv", "training_load",
        "race_predictor_5k", "race_predictor_10k", "race_predictor_half_marathon", "race_predictor_marathon",
    ],
    "strength_general_fitness": [
        "weight", "steps", "activity_calories", "activity_duration", "training_load", "resting_hr",
        "exercise_minutes", "walking_asymmetry", "walking_steadiness",
    ],
    "sleep_recovery_focus": [
        "sleep_score", "hrv", "body_battery", "stress", "resting_hr", "respiration",
        "sleep_duration", "mindful_minutes", "stand_hours",
    ],
    "full_overview": [
        "resting_hr", "hrv", "vo2max", "body_battery", "weight", "sleep_score", "steps", "stress",
        "respiration", "spo2", "training_load", "race_predictor_5k", "race_predictor_10k",
        "race_predictor_half_marathon", "race_predictor_marathon", "activity_duration",
        "activity_distance", "activity_calories",
        "sleep_duration", "mindful_minutes", "stand_hours", "exercise_minutes", "walking_asymmetry", "walking_steadiness",
    ],
}
```

`sleep_duration` (Apple Health's hours-asleep, aggregated in Task 7/8) is a distinct metric_type from Garmin's `sleep_score` (a 0-100 quality score) — both are listed here; they never overlap or reconcile against each other since Source-Priority Reconciliation (Task 4) only engages when two sources report the *same* metric_type. No dashboard icon change is needed for `sleep_duration` — `dashboard.html`'s existing `{% elif 'sleep' in metric_type %}{{ feather_icon('moon') }}` branch already matches it (the substring `"sleep"` is in `"sleep_duration"` too), so it automatically gets the same moon icon `sleep_score` already uses, with no code change required.

In `app/templates/dashboard.html`, extend the existing `feather_icon` elif chain (around line 52-63) by adding branches before the final `{% else %}`:

```html
{% elif 'mindful' in metric_type %}{{ feather_icon('wind') }}
{% elif 'walking' in metric_type or 'stand_hours' in metric_type or 'exercise' in metric_type %}{{ feather_icon('activity') }}
```

(Insert these two lines among the existing `{% elif %}` branches, before the final `{% else %}{{ feather_icon('bar-chart-2') }}{% endif %}`.)

Update the pre-existing conflicting test in `tests/app/test_settings.py` — rename it and change its assertion from exact-equality to a superset check, since `full_overview` now includes 6 new Apple-Health-only metric_types alongside all 18 Garmin ones:

```python
# in tests/app/test_settings.py, replace test_full_overview_includes_all_eighteen_garmin_metric_types with:
def test_full_overview_includes_all_eighteen_garmin_metric_types_plus_apple_health_only_types():
    all_18_garmin = {
        "resting_hr", "hrv", "vo2max", "body_battery", "weight", "sleep_score",
        "steps", "stress", "respiration", "spo2", "training_load",
        "race_predictor_5k", "race_predictor_10k", "race_predictor_half_marathon",
        "race_predictor_marathon", "activity_duration", "activity_distance", "activity_calories",
    }
    apple_health_only = {
        "sleep_duration", "mindful_minutes", "stand_hours", "exercise_minutes",
        "walking_asymmetry", "walking_steadiness",
    }

    assert set(PERSONA_METRIC_TYPES["full_overview"]) == all_18_garmin | apple_health_only
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/app/test_dashboard.py tests/app/test_settings.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add app/settings.py app/templates/dashboard.html tests/app/test_dashboard.py tests/app/test_settings.py
git commit -m "feat: wire new Apple Health metric types into personas and dashboard icons"
```

---

### Task 14: Settings — Apple Health Import card + source-priority picker

**Files:**
- Modify: `app/routes/settings.py`
- Modify: `app/templates/settings.html`
- Modify: `app/routes/data_sources.py` (new priority-picker route)
- Test: `tests/app/test_settings.py`

**Interfaces:**
- Consumes: `repository.has_synced_data`, `repository.get_source_priority`/`set_source_priority` (Tasks 3, 5), `GARMIN_METRIC_TYPES`, `APPLE_HEALTH_METRIC_TYPES` (Tasks 2, 8).
- Produces: `POST /settings/apple-health/priority` route; Settings page gains an "Apple Health Import" card mirroring the existing "Garmin Integration" card, plus a priority picker shown only for metric_types both connected sources report.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/app/test_settings.py
def test_settings_page_shows_apple_health_card(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/settings")

    assert "Apple Health" in response.text
    assert 'action="/api/data-sources/apple-health/import"' in response.text


def test_settings_shows_priority_picker_only_for_overlapping_metric_types(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    app.state.credential_store.save({"email": "a@example.com", "password": "x"})

    from core.storage import repository
    from core.storage.db import connect
    from datetime import date
    conn = connect(app.state.db_path)
    repository.set_checkpoint(conn, "apple_health", "resting_hr", date(2026, 1, 1))  # overlaps Garmin
    repository.set_checkpoint(conn, "apple_health", "mindful_minutes", date(2026, 1, 1))  # Apple-only

    response = client.get("/settings")

    assert 'name="priority_resting_hr"' in response.text
    assert 'name="priority_mindful_minutes"' not in response.text  # no overlap, no picker row


def test_set_source_priority_route_persists_choice(app, client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.post(
        "/settings/apple-health/priority", data={"metric_type": "resting_hr", "preferred_source": "apple_health"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    from core.storage import repository
    from core.storage.db import connect
    conn = connect(app.state.db_path)
    assert repository.get_source_priority(conn, "resting_hr") == "apple_health"
```

This matches the existing DB-access idiom already used throughout `tests/app/test_settings.py` (e.g. `test_onboarding_persona_post_sets_persona_and_redirects_to_theme`): take `app` as a pytest fixture parameter and call `connect(app.state.db_path)` directly, not through `client`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/app/test_settings.py -v -k "apple_health or priority"`
Expected: FAIL — no Apple Health card, no priority route.

- [ ] **Step 3: Implement**

In `app/routes/data_sources.py`, add:

```python
@router.post("/settings/apple-health/priority")
def set_apple_health_priority(
    request: Request,
    metric_type: str = Form(...),
    preferred_source: str = Form(...),
    conn=Depends(require_admin_page),
):
    from core.storage import repository
    repository.set_source_priority(conn, metric_type, preferred_source)
    return RedirectResponse(url="/settings", status_code=303)
```

(Add `from fastapi import Form` if not already imported in that file — check the existing import line at the top and extend it.)

In `app/routes/settings.py`, extend `_settings_context()` to compute the overlap and pass Apple Health status through. `_settings_context()` currently only receives `conn`, not `request` — it has no way to check `credential_store` itself — so `garmin_connected` is passed in as a new required keyword parameter by each call site (shown below) rather than computed inside the function. Add the new imports at the top of the file:

```python
from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES
from core.providers.garmin import GARMIN_METRIC_TYPES
from core.storage.repository import get_source_priority, has_synced_data
```

Then update `_settings_context`'s definition to:

```python
def _settings_context(conn, *, theme, garmin_connected, persona_error=None, theme_error=None, unit_error=None, skin_error=None, profile_error=None):
    apple_connected = has_synced_data(conn, "apple_health")
    overlapping_metric_types = []
    if garmin_connected and apple_connected:
        overlapping_metric_types = sorted(set(GARMIN_METRIC_TYPES) & set(APPLE_HEALTH_METRIC_TYPES))

    return {
        "authenticated": True,
        "personas": PERSONAS,
        "themes": THEMES,
        "skins": SKINS,
        "units": UNITS,
        "current_persona": get_persona(conn) or DEFAULT_PERSONA,
        "current_theme": theme,
        "theme": theme,
        "current_skin": get_skin(conn) or DEFAULT_SKIN,
        "skin": get_skin(conn) or DEFAULT_SKIN,
        "current_unit": get_unit(conn) or DEFAULT_UNIT,
        "athlete_name": get_athlete_name(conn),
        "athlete_age": get_athlete_age(conn),
        "persona_error": persona_error,
        "theme_error": theme_error,
        "unit_error": unit_error,
        "skin_error": skin_error,
        "profile_error": profile_error,
        "garmin_connected": garmin_connected,
        "apple_health_connected": apple_connected,
        "overlapping_metric_types": overlapping_metric_types,
        "source_priority": {mt: get_source_priority(conn, mt) or "garmin" for mt in overlapping_metric_types},
    }
```

`app/routes/settings.py` has five call sites of `_settings_context(conn, theme=...)` that all need `garmin_connected=request.app.state.credential_store.load() is not None` added as an argument. Each is inside a route handler that already receives `request: Request`, so `request.app.state.credential_store` is in scope at every one. Update each exactly as follows:

```python
# GET /settings (settings_page) — was: context=_settings_context(conn, theme=theme),
context=_settings_context(
    conn, theme=theme, garmin_connected=request.app.state.credential_store.load() is not None
),

# POST /settings/unit error branch — was: context=_settings_context(conn, theme=theme, unit_error=str(exc)),
context=_settings_context(
    conn, theme=theme, unit_error=str(exc),
    garmin_connected=request.app.state.credential_store.load() is not None,
),

# POST /settings/persona error branch — was: context=_settings_context(conn, theme=theme, persona_error=str(exc)),
context=_settings_context(
    conn, theme=theme, persona_error=str(exc),
    garmin_connected=request.app.state.credential_store.load() is not None,
),

# POST /settings/theme error branch — was: context=_settings_context(conn, theme=current_theme, theme_error=str(exc)),
context=_settings_context(
    conn, theme=current_theme, theme_error=str(exc),
    garmin_connected=request.app.state.credential_store.load() is not None,
),

# POST /settings/skin error branch — was: context=_settings_context(conn, theme=theme, skin_error=str(exc)),
context=_settings_context(
    conn, theme=theme, skin_error=str(exc),
    garmin_connected=request.app.state.credential_store.load() is not None,
),
```

In `app/templates/settings.html`, add a new card after the existing "Garmin Integration" card (mirroring its structure):

```html
<!-- Card: Apple Health Import -->
<div class="settings-card">
  <div class="settings-card-header">
    <div class="app-icon" style="width: 34px; height: 34px; border-radius: var(--radius-sm);">{{ feather_icon('heart') }}</div>
    <div>
      <h2 class="section-title" style="margin: 0; font-size: 1.15rem;">Apple Health</h2>
      <span style="font-size: 0.78rem; color: var(--fg-muted);">Import from a Health app export</span>
    </div>
  </div>
  <div style="margin-top: 1rem; display: flex; flex-direction: column; flex: 1; justify-content: space-between;">
    <div>
      <p style="font-size: 0.82rem; color: var(--fg-muted); margin: 0 0 1rem; line-height: 1.45;">
        Health app &rarr; Profile &rarr; Export All Health Data, then upload the resulting .zip here.
        {% if apple_health_connected %}Status: connected.{% else %}Status: not yet connected.{% endif %}
      </p>
      <form method="post" action="/api/data-sources/apple-health/import" enctype="multipart/form-data" id="apple-health-settings-form">
        <div class="form-group">
          <input class="form-input" type="file" id="apple_export_file" name="export_file" accept=".zip" required>
        </div>
        <button type="submit" class="btn-primary" style="width: 100%; padding: 0.65rem 1rem;">Import Export</button>
      </form>
      <script>
        document.getElementById("apple-health-settings-form").addEventListener("submit", async (evt) => {
          evt.preventDefault();
          const form = evt.target;
          const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
          if (response.ok) { window.location.reload(); }
          else {
            const body = await response.json().catch(() => ({}));
            alert(body.detail || "Import failed. Please check the file and try again.");
          }
        });
      </script>

      {% if overlapping_metric_types %}
      <div style="margin-top: 1.25rem;">
        <label class="form-label">Source Priority (metrics both providers report)</label>
        {% for mt in overlapping_metric_types %}
        <form method="post" action="/settings/apple-health/priority" style="display: flex; align-items: center; gap: 0.5rem; margin-top: 0.5rem;">
          <input type="hidden" name="metric_type" value="{{ mt }}">
          <span style="font-size: 0.82rem; flex: 1;">{{ mt.replace('_', ' ').title() }}</span>
          <select class="form-input" name="preferred_source" style="width: auto;" onchange="this.form.submit()">
            <option value="garmin" {% if source_priority[mt] == 'garmin' %}selected{% endif %}>Garmin</option>
            <option value="apple_health" {% if source_priority[mt] == 'apple_health' %}selected{% endif %}>Apple Health</option>
          </select>
        </form>
        {% endfor %}
      </div>
      {% endif %}
    </div>
  </div>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/app/test_settings.py -v`
Expected: All PASS, including every pre-existing settings test (the four `_settings_context` call sites all now pass `garmin_connected` explicitly, so no call site is left with a missing required argument).

- [ ] **Step 5: Commit**

```bash
git add app/routes/settings.py app/routes/data_sources.py app/templates/settings.html tests/app/test_settings.py
git commit -m "feat: add Apple Health import card and per-metric source priority picker to Settings"
```

---

### Task 15: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -v`
Expected: All tests PASS — no regressions in Garmin, analytics, MCP, or deployment tests from any of the above changes.

- [ ] **Step 2: If anything fails, fix forward**

Do not skip or comment out a failing test. Trace it to the specific task above whose change caused it, fix the implementation (not the test) unless the test itself was wrong, and re-run the full suite.

- [ ] **Step 3: Final commit (only if fixes were needed)**

```bash
git add -A
git commit -m "fix: address regressions found in full-suite check"
```
