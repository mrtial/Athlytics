# Athlytics

A self-hosted personal health and fitness analytics platform and actionable AI Coach. Athlytics pulls health, recovery, and workout data directly from wearable providers (Garmin Connect first), stores it permanently in a local SQLite database, calculates sports-science trends and statistical anomalies, and exposes both a responsive web dashboard and a bidirectional Model Context Protocol (MCP) server for AI assistants (Claude, Google Gemini).

Athlytics gives you complete data ownership, local encrypted credential storage, and an evidence-based AI coach capable of reading recovery state and writing structured training plans and targets directly to your dashboard.

---

## Features

- **Privacy-First & Self-Hosted**: All data is stored in a local SQLite database. Credentials are encrypted at rest with AES-128-CBC / HMAC-SHA256 (Fernet) keys generated on first run. Zero telemetry or third-party cloud dependence.
- **Comprehensive Garmin Connect Ingestion**: Headless, resumable, rate-limit-aware synchronization across 18 canonical metric types:
  - *Recovery & Wellness*: Resting Heart Rate, HRV, Sleep Score, Body Battery, Stress, Respiration, SpO2, Weight.
  - *Training & Performance*: VO2 Max, Training Load, Race Predictions (5K, 10K, Half Marathon, Marathon).
  - *Activities & Volume*: Steps, Activity Distance, Duration, Active Calories.
- **Sports-Science Analytics**:
  - Trailing rolling averages (7-day, 14-day, 30-day).
  - Period-over-period delta and percentage change calculations.
  - Statistical anomaly detection using 30-day rolling Gaussian baselines ($|z| \ge 2.0$).
- **Responsive Web Dashboard (`app/`)**:
  - Clean, modern UI with Dark, Light, and System themes.
  - Athlete personas (`endurance_runner`, `strength_general_fitness`, `sleep_recovery_focus`, `full_overview`).
  - Live sync status panel with real-time polling, one-click manual sync trigger, and authentication error alerts.
  - Interactive onboarding flow for admin creation, persona configuration, and data source connection.
- **Actionable AI Coach & MCP Server (`mcp_server/`)**:
  - **Living Context Resources**: `athlytics://athlete/snapshot`, `athlytics://training/current-state`, `athlytics://coach/context`.
  - **8 Read Tools**: Query metric series, rolling trends, statistical anomalies, athlete targets, training plans, reports, and coaching logs.
  - **5 Action / Write Tools**: `set_target`, `delete_target`, `save_training_plan`, `update_plan_status`, `log_coach_note`.
  - **On-Demand Sync Tool**: `sync_garmin_data(days=30)` to pull fresh provider data directly from your AI conversation.
  - **3 Workflow Prompts**: Readiness check-in (recovery-gated intensity), weekly review retrospective, and periodized training plan builder (10% volume progression rule & deloads).
  - **Bundled Playbooks**: Pre-configured skills and system instructions for Claude Desktop, Claude Code, and Google Gemini.

---

## Deployment (Docker Compose)

The recommended way to run Athlytics is with Docker Compose.

### Prerequisites

- Docker Engine with the Compose plugin (`docker compose version`). Docker Desktop on macOS/Windows includes this; on Linux, install `docker-compose-plugin` alongside Docker Engine.

### First Run

From the project root:

```bash
docker compose up -d --build
```

This builds the image from the local `Dockerfile`, starts one container named `athlytics`, and binds it to `http://localhost:8000`. 

- **Port Override**: Set `ATHLYTICS_PORT=8001 docker compose up -d --build`, or create a `.env` file containing `ATHLYTICS_PORT=8001`.
- **First-Time Setup**: Open `http://localhost:8000` in your browser. Create your admin account, choose your persona and theme, then connect your Garmin account.
- **MFA Challenge**: If your Garmin account requires an MFA verification code on first connection, run `docker exec -it athlytics python scripts/login_garmin.py` once to complete the prompt and save session tokens.
- **Background Sync**: Once connected, full-history backfill starts automatically in the background.

### Where Your Data Lives

All application data persists in a single named Docker volume, `athlytics_data`, mounted at `/data` inside the container:

- `/data/athlytics.db` — SQLite database (metric readings, sync checkpoints, admin account, sessions, targets, training plans, coach notes, sync status).
- `/data/.env` — Generated Fernet encryption secret key.
- `/data/garmin_credentials.enc` — Encrypted Garmin credentials.
- `/data/garmin_tokens/` — Garmin cached OAuth/session tokens.

Recreating the container (`docker compose up -d --build` or `docker compose restart`) preserves this volume.

To back up the volume to a local archive:

```bash
docker run --rm -v athlytics_data:/data -v "$(pwd)":/backup alpine \
  tar czf /backup/athlytics-backup.tar.gz -C /data .
```

### Encryption Secret Provisioning

Secret provisioning is completely automatic. On first start, `core.config.get_or_create_secret_key` checks for `/data/.env` inside the mounted persistent volume and generates a cryptographically secure key with `0600` permissions if absent. No manual secret provisioning or Swarm setup is needed.

### Updating

```bash
docker compose down          # stops container, preserves volume
docker compose up -d --build # rebuilds image and starts fresh
```

### Clean Reset / Uninstalling

```bash
docker compose down -v       # stops container AND deletes data volume
```

*Warning: This permanently deletes your local database, encryption secret, and cached Garmin session.*

### Advanced: Running without Compose

```bash
docker build -t athlytics:latest .
docker run -d --name athlytics -p 8000:8000 \
  -v athlytics_data:/data \
  -e ATHLYTICS_DATA_DIR=/data \
  -e ATHLYTICS_DB_PATH=/data/athlytics.db \
  athlytics:latest
```

---

## Connecting Your AI Coach (MCP)

Athlytics includes an actionable Model Context Protocol (MCP) server running over `stdio`. The MCP server is launched on demand by your AI client via `docker exec` (or a one-off `docker run`), sharing the same database volume as the web app.

### Claude Desktop
Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "docker",
      "args": ["exec", "-i", "athlytics", "python", "-m", "mcp_server.server"]
    }
  }
}
```

### Claude Code
Run from the terminal:

```bash
claude mcp add athlytics -- docker exec -i athlytics python -m mcp_server.server
```

### Google Gemini CLI & Antigravity
Add to `~/.gemini/config/mcp_config.json` or `.agents/mcp_config.json`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "docker",
      "args": ["exec", "-i", "athlytics", "python", "-m", "mcp_server.server"]
    }
  }
}
```

### One-Off Run Fallback (If Container is Not Running Continuously)

If you only run the MCP server intermittently without keeping the web server up, use `docker run` with `--rm`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "athlytics_data:/data",
        "-e", "ATHLYTICS_DB_PATH=/data/athlytics.db",
        "athlytics:latest",
        "python", "-m", "mcp_server.server"
      ]
    }
  }
}
```

---

## Local Development (No Docker)

### 1. Environment & Dependencies

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Interactive MFA Login Script

```bash
python scripts/login_garmin.py
```

### 3. Running the Web Application

```bash
uvicorn app.main:create_production_app --factory --reload --port 8000
```

### 4. Running the MCP Server

```bash
python -m mcp_server.server
```

### 5. Running the Test Suite

```bash
pytest -v
```

The test suite covers unit tests, SQLite storage contracts, mock provider sync, Garmin response parsing, analytics algorithms, FastAPI route flows, and full MCP JSON-RPC protocol simulations (215+ tests, 100% offline).

---

## Python API Usage

You can also use Athlytics directly as a Python library:

```python
from datetime import date
from pathlib import Path

from core.config import get_or_create_secret_key
from core.providers.garmin import GarminProvider
from core.scheduler.sync import sync_all_metrics
from core.security.credentials import CredentialStore
from core.storage import repository
from core.storage.db import connect
from core.analytics import get_trend, detect_anomalies

# 1. Initialize encrypted credentials and connect to database
secret_key = get_or_create_secret_key(Path(".env"))
credential_store = CredentialStore(secret_key, Path("garmin_credentials.enc"))
conn = connect(Path("athlytics.db"))

# 2. Build provider & run sync
provider = GarminProvider(credential_store, token_cache_dir=Path(".garmin_tokens"))
results = sync_all_metrics(conn, provider, backfill_start=date(2026, 1, 1), end=date.today())

# 3. Analyze rolling trends and recovery anomalies
rhr_trend = get_trend(conn, "resting_hr", window_days=7)
print(f"7-day Resting HR: {rhr_trend.current.average:.1f} bpm (delta: {rhr_trend.delta.absolute_change:+.1f})")

anomalies = detect_anomalies(conn, "resting_hr", baseline_window_days=30)
for a in anomalies:
    print(f"Flagged {a.timestamp.date()}: {a.value} bpm (z-score: {a.z_score:+.2f})")
```

---

## Project Structure

```
athlytics/
├── core/
│   ├── storage/          # SQLite schema, models (readings, targets, plans, notes), repo
│   ├── providers/        # Provider interface, Garmin Connect adapter, FakeProvider
│   ├── analytics/        # Rolling baselines, deltas, z-score anomaly detection
│   ├── security/         # Fernet encrypted credential storage
│   └── scheduler/        # Resumable, paced multi-metric sync orchestrator
├── app/                  # FastAPI web dashboard, auth, onboarding, widget builder
│   ├── routes/           # Root, auth, onboarding, dashboard, settings, sync APIs
│   ├── static/           # Vanilla CSS design system and JavaScript live poller
│   └── templates/        # Semantic HTML / Jinja2 templates
├── mcp_server/           # Actionable Model Context Protocol server over stdio
│   ├── server.py         # MCP server instance with 8 read and 6 write/sync tools
│   ├── resources.py      # Living context generators (athlytics:// snapshot & plans)
│   └── prompts.py        # Workflow prompts (readiness, weekly review, plan builder)
├── scripts/              # Helper scripts (interactive MFA login, fixture capture)
├── .claude/              # Claude Code and Desktop coaching skills
├── .agents/              # Antigravity and Gemini MCP configurations
├── docs/
│   ├── superpowers/specs # System architecture & AI Coach design specifications
│   ├── superpowers/plans # Implementation plans across all subsystems
│   └── coach/            # Client setup guides & Gemini system instructions
├── tests/                # 215+ unit, integration, and contract tests
├── Dockerfile            # Single-stage container image
├── docker-compose.yml    # One-click Compose deployment with persistent volume
└── pyproject.toml        # Package manifest and dependencies
```

---

## Documentation

- **Design Specification**: [`docs/superpowers/specs/2026-08-16-athlytics-design.md`](docs/superpowers/specs/2026-08-16-athlytics-design.md)
- **AI Coach & MCP Architecture**: [`docs/superpowers/specs/2026-08-16-athlytics-ai-coach-design.md`](docs/superpowers/specs/2026-08-16-athlytics-ai-coach-design.md)
- **AI Client Setup Guide**: [`docs/coach/client-setup.md`](docs/coach/client-setup.md)
- **Gemini Coaching Instructions**: [`docs/coach/gemini-system-instructions.md`](docs/coach/gemini-system-instructions.md)
