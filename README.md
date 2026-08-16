# Athlytics

A self-hosted personal health and fitness analytics platform. It pulls data from
wearable/health providers (Garmin Connect first), stores it permanently in a local
database, computes trends and anomalies, and — eventually — exposes it through a web
dashboard and an MCP server so you can ask an AI assistant natural-language questions
about your own data.

The core motivation: spot trends, track training progress, and catch abnormal
patterns (recovery, sleep, HR, etc.) that aren't easily visible in the stock Garmin
Connect app — without handing your Garmin credentials to a hosted third party.

Full design doc: [`docs/superpowers/specs/2026-08-16-athlytics-design.md`](docs/superpowers/specs/2026-08-16-athlytics-design.md)

## Status

This project is under active, incremental development. What exists today:

- ✅ **Core storage** — a canonical, provider-agnostic SQLite schema for metric
  readings (`core/storage/`).
- ✅ **Provider interface** — a pull-based `Provider` protocol any data source
  implements (`core/providers/base.py`), plus a real **Garmin Connect adapter**
  (`core/providers/garmin.py`) covering resting HR, HRV, sleep, VO2 max, body
  battery, weight, steps, stress, respiration, SpO2, training load, race
  predictions (5K/10K/half/marathon), and activity duration/distance/calories.
- ✅ **Encrypted credential storage** — Garmin credentials are encrypted at rest
  with a key generated on first run (`core/security/`, `core/config.py`).
- ✅ **Sync orchestrator** — resumable, rate-limit-aware, per-metric-isolated
  backfill and incremental sync (`core/scheduler/sync.py`).
- ✅ **Analytics Engine** — rolling averages, deltas, and z-score anomaly detection (`core/analytics/`).
- ✅ **Dashboard & API** — FastAPI web dashboard with onboarding, widgets, and background sync (`app/`).
- ✅ **AI Coach & MCP Server** — actionable stdio MCP server with read/write tools, dynamic resources, and workflow prompts (`mcp_server/`).
- ✅ **Docker Deployment** — single `docker compose up` deployment packaging the dashboard, scheduler, and on-demand MCP entrypoint (`Dockerfile`, `docker-compose.yml`).

Running the finished application is a single `docker compose up` -- see
[`DEPLOYMENT.md`](DEPLOYMENT.md) for the full first-run runbook (secret
provisioning, where your data lives, and how to point an AI client at the MCP
server).

## Requirements

- Python 3.11+
- A Garmin Connect account (only needed if you want to sync real data — all tests
  run against fakes/fixtures and need no credentials)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the tests

```bash
pytest
```

All storage, provider, security, and scheduler logic is tested against a real
SQLite database and either a `FakeProvider` test double or fixture files captured
from a real Garmin account (`tests/fixtures/garmin/`) — no network calls happen
during the test run.

## Configuration

Copy `.env.example` to `.env` if you want to control where the encryption secret
lives; otherwise one is generated automatically the first time
`core.config.get_or_create_secret_key()` runs. The generated `.env` and any saved
credential file are both written with `0600` permissions.

## Usage

There's no CLI yet, so you drive the sync pipeline directly from Python. A minimal
end-to-end example — connect a Garmin account, back-fill a date range, then read
the data back out:

```python
from datetime import date
from pathlib import Path

from core.config import get_or_create_secret_key
from core.providers.garmin import GarminProvider
from core.scheduler.sync import sync_all_metrics
from core.security.credentials import CredentialStore
from core.storage import repository
from core.storage.db import connect

# 1. Set up encrypted credential storage and save your Garmin login once.
secret_key = get_or_create_secret_key(Path(".env"))
credential_store = CredentialStore(secret_key, Path("garmin_credentials.enc"))
credential_store.save({"email": "you@example.com", "password": "your-garmin-password"})

# 2. Connect to local storage.
conn = connect(Path("athlytics.db"))

# 3. Build the Garmin provider (logs in, caches the session token).
provider = GarminProvider(credential_store, token_cache_dir=Path(".garmin_tokens"))

# 4. Sync — resumable, paced, and isolated per metric type. Safe to re-run daily;
#    it only fetches what's changed since the last run.
results = sync_all_metrics(
    conn,
    provider,
    backfill_start=date(2026, 1, 1),
    end=date.today(),
)
print(results)  # e.g. {"resting_hr": "complete", "hrv": "complete", ...}

# 5. Read the data back out.
readings = repository.get_readings(conn, "resting_hr", date(2026, 1, 1), date.today())
for r in readings:
    print(r.timestamp.date(), r.value, r.unit)
```

If your Garmin account has MFA enabled, `GarminProvider`'s construction will raise
`GarminAuthError` asking you to complete an interactive login once (via the
`garminconnect` library directly) to populate the cached session token before
retrying headlessly.

### Capturing Garmin API fixtures (for development)

If you're extending the Garmin adapter with a new metric, capture a real API
response as a test fixture rather than guessing its shape:

```bash
python scripts/capture_garmin_fixtures.py --email you@example.com
```

You'll be prompted for your password interactively. This writes pretty-printed
JSON into `tests/fixtures/garmin/`, safe to commit — it's your own health data on
your own self-hosted instance, never your credentials.

## Project layout

```
core/
  storage/      canonical metric_reading schema, repository, sync checkpoints
  providers/    Provider protocol, FakeProvider (tests), GarminProvider (real)
  security/     Fernet-encrypted credential storage
  scheduler/    resumable/paced/per-metric-isolated sync orchestrator
  config.py     encryption secret bootstrap
scripts/
  capture_garmin_fixtures.py   one-off script to capture real API responses
tests/
  fixtures/garmin/             captured real Garmin API responses
docs/
  superpowers/specs/           design doc
  superpowers/plans/           implementation plans, one per subsystem
```

## Roadmap

See `docs/superpowers/plans/` for the implementation plans: analytics (rolling
averages/deltas/anomaly baselines), dashboard & API (FastAPI, onboarding,
persona/theme, sync status), actionable AI Coach MCP server, and Docker deployment.
