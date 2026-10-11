# Provider Detail Data: What to Keep and Why

Every provider gives Athlytics a **summary** of each workout or night. Most also offer far richer **detail** (laps, per-second streams, sleep stages, heart-rate zone time). This page is the checklist for deciding which detail to store, using Garmin activity detail as the worked example. Apply it when adding detail for any other provider (Apple Health, Strava, Mi Fitness, ...).

Garmin is the reference because it was evaluated against a live account: a ~7 mi outdoor run returns ~109 summary fields, of which Athlytics originally kept 14. Most of what a coach needs to judge *how* a session was run was already in the response and discarded; the rest cost one extra call.

## Principles

1. **Three tiers, three costs.**
   - *Summary*: always synced, one row per session. Evaluate everything the list endpoint returns "for free" here first.
   - *Hydrated detail*: small, structured, queryable (laps, zone seconds, sleep stages). Fetched after the main sync, stored in its own table.
   - *On-demand series*: second-by-second streams, GPS, raw samples. Fetched live when asked, **not stored**.
2. **Every stored field needs a named consumer.** Keep a field only if a specific analysis rule (a tool or the check-in skill) will read it. Does it change a coaching decision? Is it comparable across sources? Are its semantics stable? Is it cheap to get? "Might be useful" is not a reason, because re-fetching is cheap.
3. **Do not store raw provider payloads.** The provider is the source of truth and every provider has a refetch/backfill path. Raw payloads carry data we do not want at rest (owner name and ID, profile image URLs, GPS start/end, location), cannot be queried, and drift when the provider changes its schema. Document the field mapping instead.
4. **Neutral units, both clocks.** Store SI units (seconds, meters, m/s, bpm, watts); convert at presentation (pace for foot sports, km/h for cycling). Store UTC **and** the provider's local start time with the UTC offset. Never infer the local day from an assumed timezone.
5. **Null is not zero.** New columns are nullable. Missing data (older activities, a sensor the device lacks, a failed hydration) stays null, and the consumer reports "no data" instead of fabricating a 0.
6. **Hydration mechanics** (see Garmin sleep detail and Tonal strength sets for working examples): its own fetch path; its own checkpoint; a trailing grace-window re-walk because providers finalize data late; a capped first-run lookback; skipped on full-history syncs; one shared call site invoked from every sync entry point (MCP tool and background pass); per-item error isolation; paced calls with rate-limit errors propagating; idempotent upserts; failures isolated from the main sync result.
7. **Aggregate in code, not in the model.** Provide a tool that returns computed summaries (like `get_sleep_pattern`) so the AI coach reads results instead of crunching rows by hand.
8. **Validate with sums.** Zone seconds should sum to about the duration; lap distances to the activity distance; sleep-stage seconds to the night total. Build fixtures from real captures and test on the library version the container actually runs.
9. **No signal without a rule.** Each added signal gets a rule in the weekly check-in (threshold plus a "no data" fallback), or it is not added.

## Worked example: Garmin activities

**Kept** (all already in the activity-list response, no extra call): HR zone seconds (zones 1-5), aerobic/anaerobic training effect and label, per-activity training load, average cadence, average and normalized power, moving duration, local start time and UTC offset. **Laps** (`get_activity_splits`) are one extra call per activity, stored in their own table. The per-second **series** (`get_activity_details`) is a live fetch only.

**Skipped:** per-run VO2max, body battery cost, steps, intensity minutes, fastest splits, flags, device info, calorie breakdown (duplicates of stored daily metrics or no decision value); power zones, ground contact, stride length, vertical oscillation (no rule consumes them yet); owner/profile fields, GPS, location (personal data). **Deferred:** weather (one extra call per outdoor run).

The full design, schema, task plan and check-in changes are kept with the other design notes under `docs/superpowers/` (local, gitignored).

## Notes for other providers

- **Apple Health** is a *push* import (export file or iOS Shortcut), so "hydration" happens at parse time, not by calling back. Map to the same target columns: workout summary (duration, distance, energy, average HR from workout statistics) into `activity`; HR zone seconds are **computed locally** from the workout's HR samples against the athlete's max HR/zones, since Apple does not provide them; the workout route (GPS) and raw HR samples are the series tier (not stored, or downsampled); HealthKit sleep stages map to the sleep tables. Apple HRV is SDNN, not Garmin's overnight value, so keep `source` on every row and do not compare across sources without a note.
- **Strava**: summary list into `activity`; the laps endpoint into the laps table; streams are the series tier (on demand). The summary has no zone seconds; compute from streams only if a rule needs them.
- **Tonal**: the existing reference for hydrated detail (`strength_set` per set, `tonal_workout_meta` per workout).
- **Mi Fitness**: apply the same checklist when its detail endpoints are evaluated.
