# Athlytics — Design Doc

Date: 2026-08-16
Status: Draft, pending review

## Purpose

A self-hosted personal health and fitness analytics platform. Pulls data from wearable/health
data providers (Garmin Connect in v1), stores it permanently, computes trends and anomalies,
and exposes it two ways: a web dashboard, and an MCP server so the user can ask an AI
assistant (Claude, Gemini, etc.) natural-language questions about their own data.

Core motivation: spot trends, track training progress, and catch abnormal patterns
(recovery, sleep, HR, etc.) that aren't easily visible in the stock Garmin Connect app —
without handing Garmin (or any third party) credentials to a hosted service.

## Users

Anyone technical enough to self-host it (Docker) and run it for themselves. Each instance is
single-tenant: one admin login, one connected data-source account. Not a multi-tenant SaaS.

## Goals

- Long-term, permanent storage of health/fitness data, independent of what the source
  provider retains or exposes historically.
- Trend and anomaly detection across training, recovery, sleep, and other health metrics.
- A dashboard usable without any AI client.
- An MCP server so any MCP-compatible AI client can query the same data conversationally.
- Provider-agnostic core, so a second data source (e.g. Apple Health) can be added later
  without reworking storage, analytics, or the MCP layer.

## Non-Goals (v1)

- Multi-user / multi-tenant support.
- Any data source beyond Garmin Connect (Apple Health etc. are future work — see below).
- ML-based anomaly detection (statistical baselines only).
- Pre-built cross-metric correlation views in the dashboard (handled ad hoc via MCP chat
  instead).
- An in-app chat UI with BYO API keys (MCP server via existing AI clients covers this).
- Remote/HTTP MCP transport (stdio only — see MCP Layer).

## Architecture

Single Python codebase, single Docker container. A shared `core` library is used by two thin
front doors that never duplicate logic:

```
┌────────────────────────────────────────────────┐
│  core/                                          │
│   - providers/   data source adapters           │
│   - storage/      SQLite, canonical metric schema│
│   - analytics/    baselines, deltas, anomalies   │
│   - scheduler/    background sync job            │
└───────────────┬───────────────┬──────────────────┘
                │               │
        ┌───────▼──────┐  ┌─────▼─────────┐
        │ FastAPI + SPA │  │  MCP server    │
        │  (dashboard)  │  │  (stdio)       │
        └───────────────┘  └────────────────┘
              ▲                    ▲
              │                    │
           Browser          Claude Desktop /
                             Claude Code /
                             Gemini CLI
```

## Data Providers

`core/providers/` defines a common interface so a second source can be added later without
touching storage, analytics, or the MCP layer:

- **Pull-based** (Garmin, v1): the scheduler calls the provider on a schedule; the provider
  fetches from Garmin Connect's API and returns normalized records.
- **Push/import-based** (future, e.g. Apple Health): Apple exposes no cloud API — data only
  leaves via the Health app's export (a zip of XML) or a companion iOS Shortcut/app posting
  HealthKit data to an import endpoint. The interface only needs an `ingest(payload) →
  normalized records` shape to support this later; nothing beyond the interface shape is
  built in v1.

v1 ships exactly one provider: Garmin, via the `garminconnect` Python library.

### Canonical storage schema

Metrics are stored generically, not in Garmin-specific shapes:

```
metric_reading(source, metric_type, timestamp, value, unit)
```

Analytics, the dashboard, and MCP tools all operate on `metric_type` (e.g. `resting_hr`,
`hrv`, `sleep_score`, `body_battery`, `vo2max`, `stress`, `steps`, `weight`, ...), so a future
second provider's data joins the same series without special-casing.

### v1 data coverage

Everything `garminconnect` exposes: activities/workouts, sleep, HRV, resting HR, body
battery, stress, steps, VO2 max, race predictor, respiration, SpO2, weight/body composition,
and training status/load.

### Credential handling

Garmin credentials/session tokens are stored encrypted at rest, keyed off a secret generated
on first run (`.env`, mounted as a Docker secret/volume). Prefer caching Garmin's session/OAuth
token over storing the raw password where the library supports it.

## Sync

- **Initial connect:** full history backfill runs as a background job — resumable, and
  paced to respect Garmin's (unofficial, aggressively rate-limited) API. Onboarding does not
  block on this; the user can browse the dashboard while it runs in the background.
- **Ongoing:** in-process daily scheduler pulls incremental updates after the initial backfill
  completes.
- **Permanence:** once synced, raw metric readings and any generated reports/summaries are
  kept permanently in local storage — available even if Garmin later prunes or rate-limits
  access to that history.

## Onboarding Flow

1. First run: create local admin login.
2. Choose a **persona preset** — controls which dashboard widgets are shown by default:
   - Endurance Runner
   - Strength & General Fitness
   - Sleep & Recovery Focus
   - Full Overview
3. Choose a **visual theme** (independent of persona — layout/color/density).
4. Connect a data source (Garmin only in v1; framed as "connect a data source," not
   Garmin-specific, so future providers slot into the same step).
5. Backfill starts in the background; dashboard is usable immediately with data appearing as
   it syncs.

Both persona and theme are changeable later from settings — the onboarding choice is just the
default.

## Analytics (v1 scope)

Statistical baselines, computed in `core/analytics`:

- Rolling averages per metric.
- Week-over-week and month-over-month deltas.
- Threshold/z-score anomaly flags against each metric's personal rolling baseline
  (e.g. "resting HR is 2 std devs above your 90-day baseline").

No ML-based detection, no pre-built correlation views. Correlation-style questions (e.g. "did
HRV dip more after leg day or long runs?") are intentionally left to the MCP/chat path, where
the LLM reasons over raw series data returned by the tools rather than the dashboard
pre-computing every possible cross-metric view.

## MCP Layer

- **Transport: stdio only.** Claude Desktop, Claude Code, and Gemini CLI all launch local MCP
  servers as a subprocess over stdio — zero network exposure, consistent with never sending
  credentials or data off the user's machine. Remote/HTTP transport (needed for claude.ai or
  gemini.google.com web clients) is explicitly out of scope for v1.
- **Tools** are thin wrappers over `core`, read-only:
  - `list_metrics` — available metric types and date ranges.
  - `get_metric_series(metric_type, start, end)` — raw readings.
  - `get_trend(metric_type, window)` — rolling average / delta.
  - `get_anomalies(since)` — current threshold/z-score flags.
  - `get_report(id)` — a previously generated stored report.
- Deliberately no `correlate(a, b)` tool — the model gets raw series via the above and reasons
  over it in-conversation, which covers open-ended questions no fixed tool could anticipate.

## Error Handling

- Garmin auth failures and MFA challenges are surfaced in a sync-status panel in the
  dashboard, not silently retried forever.
- Rate-limit responses trigger backoff-and-resume rather than failing the sync outright.
- Per-metric-type sync failures are isolated and retried independently (e.g. a sleep-data
  fetch failure doesn't block activities from syncing).

## Testing

- Unit tests for `core/analytics` (baseline/delta/anomaly math) against fixture time series.
- Integration tests for the API with `garminconnect` calls mocked.
- Contract tests for MCP tool schemas and responses.

## Deployment

Single `docker compose up`. One container running the FastAPI app (serving the built SPA as
static assets) plus the in-process scheduler; SQLite on a mounted volume. The MCP server is a
separate entrypoint in the same image, launched by the user's AI client config
(`command`/`args` pointing at the container or a local binary) rather than run as a
long-lived service itself.

## Future Considerations (explicitly out of scope now)

- Apple Health as a second provider (push/import-based, per the interface above).
- Remote/HTTP MCP transport with auth, for claude.ai / gemini.google.com web clients.
- An in-app chat panel (itself an MCP client + provider adapter) as an alternative to
  external AI clients.
- Pre-built correlation views in the dashboard, if ad hoc MCP queries prove insufficient.
