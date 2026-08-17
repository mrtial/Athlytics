# Athlytics — Apple Health Provider — Design Doc

Date: 2026-08-16
Status: Draft, pending review
Parent Spec: `docs/superpowers/specs/2026-08-16-athlytics-design.md`

## Purpose

Add Apple Health as a second data source, per the parent spec's Future Considerations
("Apple Health as a second provider (push/import-based, per the interface above)"). A
single user can now connect Garmin, Apple Health, or both — the same person's data,
from different wearables/devices, merged into one set of trends and anomalies.

Apple exposes no cloud API. The only egress path this spec covers is a **manual export**:
the Health app's *Profile → Export All Health Data* produces a zip containing
`apple_health_export/export.xml`, which the user uploads through Athlytics.

A **companion-app/Shortcut push endpoint** (more automated, posts HealthKit data on a
schedule) is real future work — anticipated by this design's `ImportProvider` interface —
but is explicitly out of scope for this pass. See Future Considerations.

## Non-Goals (this pass)

- The push/Shortcut ingestion path (interface anticipates it; not built now).
- Parsing HealthKit clinical records (FHIR payloads some exports embed) or `Workout` GPX
  route attachments.
- Multi-user support (out of scope for the whole app per the parent spec).
- Any change to Garmin's own sync behavior.

## Architecture

```
                         Browser (Settings, or onboarding's connect step)
                                        |
                     POST /settings/apple-health/import
                     multipart/form-data (export.zip)
                                        |
                          app/routes/settings.py
                                        |
                    core/providers/apple_health.py
                      AppleHealthProvider.ingest(payload: bytes)
                        -> Iterator[MetricReading]
                      (streaming iterparse; daily-aggregates
                       high-frequency samples per §Metric Mapping)
                                        |
                    core/storage/repository.upsert_readings()
                      (same call Garmin's sync already uses,
                       batched every ~500 readings)
                                        |
                              SQLite: metric_reading
                              (source='apple_health')
                                        |
                    core/storage/repository.get_readings()
                      (reconciles source overlaps per
                       §Source-Priority Reconciliation)
                                        |
                exists analytics / dashboard / MCP — unchanged
```

## Provider Interface

`core/providers/base.py` currently defines only a **pull** protocol:

```python
class Provider(Protocol):
    name: str
    def supported_metric_types(self) -> list[str]: ...
    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]: ...
```

This spec adds a parallel **push/import** protocol alongside it:

```python
class ImportProvider(Protocol):
    name: str
    def ingest(self, payload: bytes) -> Iterator[MetricReading]: ...
```

`ingest()` returns an iterator, not a list. Apple Health's raw XML contains high-frequency
samples (e.g. heart rate every few minutes) for a HealthKit record scope far larger than
Garmin's 18 daily-aggregate types — a multi-year export can be very large. Streaming keeps
memory bounded regardless of export size, and lets the route batch upserts as readings
arrive rather than materializing the whole parsed history before writing anything.

## `core/providers/apple_health.py`

New module implementing `ImportProvider`. Responsibilities:

1. **Unzip** the uploaded payload in memory, locate `apple_health_export/export.xml`.
2. **Stream-parse** via `xml.etree.ElementTree.iterparse`, clearing each `<Record>`/
   `<Workout>` element after processing so memory stays constant regardless of file size.
3. **Map** each HealthKit `type` attribute to an Athlytics `metric_type` string (table
   below). Unrecognized types are skipped — logged at debug level, never raised as an
   error, since a real export legitimately contains dozens of record types Athlytics has
   no use for.
4. **Aggregate to daily grain.** `MetricReading`'s timezone contract (see
   `core/storage/models.py`) is calendar-day granularity for wellness metrics — one row
   per `(source, metric_type, date)`. Apple's raw samples are far more frequent than that,
   so the parser buckets same-day, same-`metric_type` records and reduces them before
   yielding:
   - **Sum** for cumulative types: `steps`, `active_calories`, `mindful_minutes`,
     `exercise_minutes`, `stand_hours` (count of distinct stood hours that day).
   - **Mean** for point-in-time/state types: `resting_hr`, `hrv`, `weight`, `spo2`,
     `walking_asymmetry`, `walking_steadiness`.
5. **Yield** one `MetricReading(source="apple_health", ...)` per `(metric_type, date)`
   bucket, with `timestamp` = midnight UTC for that calendar date, per the existing
   `MetricReading` contract for calendar-date-keyed metrics.

### Metric Mapping

Types already shared with Garmin's vocabulary (map onto the same `metric_type` string, so
existing analytics/widgets/personas need no changes):

| HealthKit identifier | `metric_type` | Aggregation |
| :--- | :--- | :--- |
| `HKQuantityTypeIdentifierRestingHeartRate` | `resting_hr` | mean |
| `HKQuantityTypeIdentifierHeartRateVariabilitySDNN` | `hrv` | mean |
| `HKQuantityTypeIdentifierVO2Max` | `vo2max` | mean |
| `HKQuantityTypeIdentifierBodyMass` | `weight` | mean |
| `HKQuantityTypeIdentifierOxygenSaturation` | `spo2` | mean |
| `HKQuantityTypeIdentifierRespiratoryRate` | `respiration` | mean |
| `HKQuantityTypeIdentifierStepCount` | `steps` | sum |
| `HKCategoryTypeIdentifierSleepAnalysis` (asleep stages) | `sleep_score` | sum hours asleep |

New, Apple-Health-only types (no Garmin equivalent):

| HealthKit identifier | `metric_type` | Aggregation |
| :--- | :--- | :--- |
| `HKCategoryTypeIdentifierMindfulSession` | `mindful_minutes` | sum duration |
| `HKQuantityTypeIdentifierWalkingAsymmetryPercentage` | `walking_asymmetry` | mean |
| `HKQuantityTypeIdentifierAppleWalkingSteadiness` | `walking_steadiness` | mean |
| `HKCategoryTypeIdentifierAppleStandHour` | `stand_hours` | count of stood hours |
| `HKQuantityTypeIdentifierAppleExerciseTime` | `exercise_minutes` | sum |

This table is the v1 whitelist; unlisted HealthKit types are silently skipped. It can grow
later without any interface change — adding a row here is the entire cost of supporting a
new HealthKit type, since the mapping is data, not code.

**Sleep aggregation detail:** Apple Health stores sleep as many per-night stage records
(Core/Deep/REM/Awake/InBed). Only `Asleep*` stages count toward the day's total; `Awake`
and `InBed` are excluded — matching Garmin's `sleep_score` semantics closely enough that
existing analytics need no special-casing.

## Timestamp Handling

Apple Health timestamps look like `2026-05-01 07:30:00 -0400` (offset-aware). Parsed via
`datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")`, then converted to naive UTC before
constructing `MetricReading`:

```python
ts_utc = ts_aware.astimezone(timezone.utc).replace(tzinfo=None)
```

This satisfies `MetricReading.__post_init__`'s existing timezone contract without any
changes to that contract.

## Ingestion / Upload Flow

New Settings card ("Apple Health Import") alongside "Garmin Integration," with a
`<input type="file" accept=".zip">` form posting to `POST /settings/apple-health/import`,
gated by `require_admin_page` like every other settings route. No credentials or login —
unlike Garmin, a manual export needs no auth beyond "you're the logged-in admin uploading
a file."

The route: reads the upload into memory, hands its bytes to
`AppleHealthProvider.ingest()`, and batches yielded readings into
`repository.upsert_readings()` calls (every ~500 readings) rather than one call per
reading or one call for the whole file — the same batching shape `sync_all_metrics`
already uses per date-range chunk, just driven by the generator instead of a date loop.

**Status tracking reuses the existing `sync_checkpoint(source, metric_type,
last_synced_date)` table** — no new table needed. After an import, each `metric_type`'s
checkpoint is set to the latest date seen in that upload, giving "last imported: <date>"
status parity with Garmin's sync-status panel for free.

The upload runs synchronously within the request (no background job queue) — this is a
one-shot user action, not a recurring scheduled sync, so `BackgroundSyncScheduler`'s
complexity doesn't apply. The UI shows a spinner while the request is in flight.

## Source-Priority Reconciliation

When both Garmin and Apple Health report the same `metric_type` for the same day, today's
`repository.get_readings()` would return both rows and let callers average them together —
silently double-weighting that day relative to a single-source day. This spec fixes that
at the one seam every consumer (trends, widgets, MCP tools) already calls through.

New table, storing only user **overrides**:

```sql
CREATE TABLE IF NOT EXISTS metric_source_priority (
    metric_type TEXT PRIMARY KEY,
    preferred_source TEXT NOT NULL
);
```

Most `metric_type`s will never have a row here. A hardcoded default order in code —
`DEFAULT_SOURCE_PRIORITY = ["garmin", "apple_health"]` — covers everything unset, so the
system behaves sensibly before the user ever visits this setting.

`repository.get_readings()` changes: for each `(metric_type, date)`, if readings exist
from more than one source, keep only the preferred source's row (per the override table,
falling back to `DEFAULT_SOURCE_PRIORITY`); if only one source has data that day, use it
regardless of preference. Implemented as a SQL window function —
`ROW_NUMBER() OVER (PARTITION BY metric_type, date(timestamp) ORDER BY <priority rank>)`,
keeping rank 1 — so it stays a single query rather than post-filtering in Python.

**Settings UI:** a small per-`metric_type` priority picker in the Apple Health Import
card, but only for `metric_type`s that currently have data from **both** connected
sources (the intersection of what each connected source supports — see Per-Source Metric
Visibility below) — not a dropdown for every possible metric_type up front.

## Per-Source Metric Visibility

If a user has connected only Garmin, the dashboard should show only Garmin-backed
metrics; only Apple Health connected → only Apple-backed metrics; both connected → the
union, with the priority table above resolving overlaps.

**"Connected" signal:**
- Garmin: `credential_store.load() is not None` (unchanged — already how
  `onboarding_status` checks it).
- Apple Health: new `repository.has_synced_data(conn, "apple_health") -> bool`, an
  `EXISTS` check against `sync_checkpoint` for that source — reusing the same rows the
  import route already writes, no new table.

**Static per-provider metric lists**, importable without instantiating a provider:
- `GarminProvider.supported_metric_types()` (`core/providers/garmin.py:589`) currently
  returns `self._registry.keys()`, and `_registry` is only built in `__init__` — which
  performs a **real Garmin login**. Calling it just to decide what to show on a dashboard
  page load would mean authenticating to Garmin on every page render. This spec extracts
  a plain module-level constant, `GARMIN_METRIC_TYPES`, from the existing registry's keys
  (a small, targeted fix — necessary for this feature, not scope creep), and has both
  `_registry` and `supported_metric_types()` reference it instead of duplicating the list.
- `APPLE_HEALTH_METRIC_TYPES` — a similar constant in the new module, built from the
  Metric Mapping table above.

**Dashboard filtering** (`app/routes/dashboard.py`, the one place
`PERSONA_METRIC_TYPES[persona]` is read):

```python
connected_sources = set()
if credential_store.load() is not None:
    connected_sources.add("garmin")
if repository.has_synced_data(conn, "apple_health"):
    connected_sources.add("apple_health")

PROVIDER_METRIC_TYPES = {"garmin": GARMIN_METRIC_TYPES, "apple_health": APPLE_HEALTH_METRIC_TYPES}
visible = [mt for mt in PERSONA_METRIC_TYPES[persona]
           if any(mt in PROVIDER_METRIC_TYPES[s] for s in connected_sources)]
```

Since onboarding still requires at least one source connected before the dashboard is
reachable (see below), `connected_sources` is never empty by the time this runs — no
"nothing connected yet" placeholder branch is needed here. The empty-widget state a user
sees immediately after connecting (before the first sync/import completes) is the
existing, already-handled "no data in range" case (`RollingAverage.average=None,
sample_count=0`, per `core/analytics/trends.py`'s existing contract) — not new UI.

The same connected-sources check is reused for the priority picker in the previous
section: it lists exactly the `metric_type`s in the **intersection** of connected
sources' supported types (both currently report it), not the union.

## Onboarding Change

`/onboarding/connect` (`app/routes/onboarding.py`) changes from a Garmin-only step to a
**choice**: Garmin (existing login form, unchanged) or Apple Health (the upload form
above, presented inline during onboarding rather than deferred to Settings). Completing
either one satisfies the step.

`onboarding_status()` (`app/dependencies.py:56`, currently
`if get_theme(conn) is None: return "theme"` / Garmin-specific `credential_store.load()`
check) generalizes its "connect" gate from Garmin-specific `credential_store.load() is
None` to `not connected_sources` (the same check defined above) — "connect" is satisfied
once *either* provider is connected, not just Garmin.

After onboarding, Settings lets the user connect the second provider whenever they choose:
Garmin's existing "Reconnect Garmin Account" link becomes "Connect Garmin Account" when
not yet linked; the Apple Health Import card is always present regardless of which
provider was chosen during onboarding.

## New Canonical Metric Types

Introduced by this spec (see Metric Mapping table for source/aggregation):
`mindful_minutes`, `walking_asymmetry`, `walking_steadiness`, `stand_hours`,
`exercise_minutes`.

**Persona wiring** (`app/settings.py`'s `PERSONA_METRIC_TYPES`): `mindful_minutes` and
`stand_hours` added to `sleep_recovery_focus` and `full_overview`; `exercise_minutes`,
`walking_asymmetry`, and `walking_steadiness` added to `strength_general_fitness` and
`full_overview`.

**Dashboard icons** (`dashboard.html`'s existing `feather_icon` elif chain): a matching
branch per new metric_type, reusing existing icons already used elsewhere in the app
(e.g. `wind` for mindful minutes, `activity` for the walking metrics) — no new icon
assets.

No other change is needed: `build_dashboard_widgets`/`build_metric_detail`
(`app/widgets.py`) are already fully generic over whatever `metric_types: list[str]` they
receive.

Since these five metric_types have no Garmin equivalent, they're never subject to
Source-Priority Reconciliation — that logic only engages for metric_types more than one
connected source can report.

## Error Handling

| Scenario | Behavior |
| :--- | :--- |
| Upload is not a valid zip | 400, descriptive message |
| Zip has no `apple_health_export/export.xml` | 400, descriptive message |
| `export.xml` is malformed XML | 400; nothing partially committed (batches only upsert after successful parse of each buffered chunk) |
| Unrecognized HealthKit record type | Skipped silently, logged at debug — expected, not an error |
| Re-upload of the same export | Idempotent: `upsert_readings`'s existing `ON CONFLICT ... DO UPDATE` (source, metric_type, timestamp) overwrites with the same values |

## Testing

- Unit tests for `AppleHealthProvider.ingest()` against small fixture XML snippets — one
  per mapped HealthKit type, plus one unrecognized type confirming it's skipped rather
  than raising.
- Unit tests for the daily-aggregation logic (sum vs. mean per type, including the sleep
  asleep/awake stage split).
- Unit tests for the source-priority `get_readings()` window-function query: both-sources-
  present (override set / unset, falling back to default order), single-source-present,
  and neither-present cases.
- Integration test for the upload route: valid export, corrupt zip, zip missing
  `export.xml` — confirms 400s are clean, not 500s.
- Integration test for the onboarding connect-choice step (Garmin path unchanged; Apple
  Health path completes onboarding via upload).

## Future Considerations (explicitly out of scope now)

- **Push/Shortcut ingestion.** The `ImportProvider.ingest(payload: bytes)` shape is
  designed to accept either a full export file (this pass) or a smaller incremental
  payload from a companion iOS Shortcut/app hitting a new authenticated endpoint later —
  same interface, different caller and a smaller/more frequent payload. That later
  endpoint needs its own auth model (a token distinct from the browser session, since a
  Shortcut isn't a logged-in browser) — not designed here, deferred until it's built.
- Broader HealthKit record-type coverage (clinical/FHIR records, workout GPX routes)
  beyond the Metric Mapping table's v1 whitelist.
