# Developer & Setup Guide

This guide walks you through setting up **Athlytics** for local development, running the application and MCP server, managing the local database, and contributing to the project.

---

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quickstart with Docker (Recommended)](#quickstart-with-docker-recommended)
3. [Local Development (Without Docker)](#local-development-without-docker)
4. [Running the MCP Server](#running-the-mcp-server)
5. [Database Architecture & Dev Sandbox](#database-architecture--dev-sandbox)
6. [Testing & Quality Assurance](#testing--quality-assurance)
7. [Codebase Architecture](#codebase-architecture)
8. [Contributing Guidelines](#contributing-guidelines)

---

## 🛠️ Prerequisites

- **Python**: 3.11 or higher (3.12+ recommended)
- **Docker & Docker Compose**: Docker Desktop (macOS/Windows) or Docker Engine with `docker-compose-plugin` (Linux)
- **Git**: For version control

---

## 🐳 Quickstart with Docker (Recommended)

Docker Compose provides a complete, isolated environment with zero manual configuration.

### 1. Start the Container

From the repository root:

```bash
docker compose up -d --build
```

This builds the Docker image from the local `Dockerfile` and starts a container named `athlytics`, accessible at `http://localhost:8000`.

### 2. Port & Environment Customization

- **Custom Port**: To run on a different port (e.g. 8001), specify `ATHLYTICS_PORT`:
  ```bash
  ATHLYTICS_PORT=8001 docker compose up -d --build
  ```
  Or add `ATHLYTICS_PORT=8001` to a `.env` file in the project root.
- **Secure Cookies (HTTPS)**: Set `ATHLYTICS_SECURE_COOKIES=true` when running behind a reverse proxy with TLS (e.g. Caddy, Nginx, Cloudflare Tunnel).

### 3. Container Management

```bash
# View logs in real-time
docker compose logs -f

# Restart container
docker compose restart

# Stop container (preserves database volume)
docker compose down

# Update to latest code and rebuild
docker compose up -d --build
```

---

## 💻 Local Development (Without Docker)

If you prefer running Python directly on your host machine for fast development iteration:

### 1. Create a Virtual Environment & Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Start the FastAPI Web Dashboard

Run the app with hot-reload enabled via `uvicorn`:

```bash
uvicorn app.main:create_production_app --factory --reload --port 8000
```

Open `http://localhost:8000` in your browser.

- Default SQLite database path: `~/.athlytics/athlytics.db`
- Default encryption secret path: `~/.athlytics/.env`
- Custom database path override: `ATHLYTICS_DB_PATH=/path/to/my_athlytics.db uvicorn app.main:create_production_app --factory --reload --port 8000`

---

## 🤖 Running the MCP Server

The Athlytics Model Context Protocol (MCP) server provides AI agents (Claude, Gemini, ChatGPT) with bidirectional access to your fitness data.

### Option A: Over Docker Exec (Standard for AI Clients)

When the Docker container is running, configure your AI client to execute:

```bash
docker exec -i athlytics python -m mcp_server.server
```

### Option B: Local Python Process (For Local Dev)

If developing locally outside Docker:

```bash
python -m mcp_server.server
```

The MCP server communicates over standard I/O (`stdio`) using JSON-RPC.

### Option C: Standalone One-Off Docker Container

If you only want to spin up the MCP server on demand without keeping the web server running:

```bash
docker run --rm -i \
  -v athlytics_data:/data \
  -e ATHLYTICS_DB_PATH=/data/athlytics.db \
  athlytics:latest \
  python -m mcp_server.server
```

---

## 🗄️ Database Architecture & Dev Sandbox

Athlytics uses a **single SQLite database** for all storage (metric readings, targets, training plans, coach notes, admin users, sessions, and sync checkpoints).

### Persistent Storage Layout (Docker)

All data inside Docker lives in the named volume `athlytics_data` mounted at `/data`:

| File / Path | Description |
| :--- | :--- |
| `/data/athlytics.db` | Primary SQLite database |
| `/data/.env` | Auto-generated Fernet symmetric encryption key (`0600` permissions) |
| `/data/garmin_credentials.enc` | Encrypted Garmin credentials |
| `/data/garmin_tokens/` | Cached Garmin OAuth session tokens |
| `/data/strava_credentials.enc` | Encrypted Strava OAuth tokens |
| `/data/mi_fitness_credentials.enc` | Encrypted Mi Fitness session tokens |
| `/data/tonal_credentials.enc` | Encrypted Tonal credentials |

### Isolated Dev Sandbox Instance

To test clean onboarding flows, fresh account creation, or new migrations without touching your real personal health data, use the `app-dev` service gated behind the `dev` Compose profile:

```bash
# Start an isolated instance on http://localhost:8001 with its own volume (athlytics_dev_data)
docker compose --profile dev up -d app-dev
```

To reset the sandbox to a completely blank slate:

```bash
docker compose --profile dev rm -sf app-dev
docker volume rm athlytics_athlytics_dev_data
docker compose --profile dev up -d app-dev
```

> [!WARNING]
> Never run `docker compose down -v` to reset your dev instance, as that will delete all unused project volumes including your real `athlytics_data`. Always remove the specific dev volume by name as shown above.

### Backup and Restore

**Export Database to SQL Dump:**

```bash
docker exec athlytics python scripts/export_db.py --output /data/seed_export.sql
docker cp athlytics:/data/seed_export.sql ./backup_$(date +%Y%m%d).sql
docker exec athlytics rm /data/seed_export.sql
```

**Restore SQL Dump to a Database:**

```bash
python scripts/import_db.py --input ./backup_20260824.sql --db /path/to/athlytics.db
```

*(Note: `import_db.py` will refuse to overwrite an existing non-empty database unless `--force` is passed).*

---

## 🧪 Testing & Quality Assurance

Athlytics includes an extensive, 100% offline test suite (600+ tests) covering:
- Unit tests & sports-science analytics formulas
- SQLite storage schema, models, and migrations
- Provider adapters, token refresh flows, and error handling
- FastAPI routes, authentication, onboarding, and UI rendering
- Full MCP JSON-RPC protocol simulations and tool executions

### Running Tests

```bash
# Run the entire test suite
pytest -v

# Run with coverage report
pytest --cov=core --cov=app --cov=mcp_server

# Run a specific test module
pytest tests/analytics/test_anomalies.py
pytest tests/mcp_server/test_server.py
```

---

## 🏗️ Codebase Architecture

```text
athlytics/
├── core/                   # Pure business logic and storage
│   ├── analytics/          # Rolling baselines, deltas, z-score anomaly detection
│   ├── config.py           # Encryption key provisioning and configuration
│   ├── providers/          # Provider adapters (Garmin, Strava, Apple Health, Mi Fitness, Tonal)
│   ├── scheduler/          # Resumable, paced multi-metric sync orchestrator
│   ├── security/           # Fernet AES-128-CBC credential encryption
│   └── storage/            # SQLite schema, data models, and repository layer
├── app/                    # FastAPI web application & dashboard
│   ├── routes/             # Web & API routes (dashboard, auth, onboarding, coach, metrics, settings)
│   ├── static/             # Vanilla CSS design system and JavaScript live poller
│   ├── templates/          # Semantic HTML Jinja2 templates (Dark/Light/System themes)
│   └── main.py             # FastAPI application factory
├── mcp_server/             # Model Context Protocol server (bidirectional AI coach)
│   ├── server.py           # MCP tool definitions and server entrypoint
│   ├── resources.py        # Dynamic context providers (athlytics:// snapshot, current-state)
│   └── prompts.py          # Evidence-based workflow prompts (readiness, weekly review, plan builder)
├── skills/                 # AI coaching and integration playbooks (symlinked to .claude/skills)
├── scripts/                # Utility scripts (Garmin MFA login, export/import DB, fixture capture)
├── docs/                   # Complete user and developer documentation
├── tests/                  # 600+ unit, integration, and contract tests
├── Dockerfile              # Production container image definition
└── docker-compose.yml      # Multi-instance Compose orchestration
```

---

## 🤝 Contributing Guidelines

We welcome contributions from the community! Whether you want to add support for a new wearable device, refine sports science algorithms, or improve the UI:

1. **Fork and Branch**: Create a feature branch with a descriptive name (`git checkout -b feature/whoop-provider`).
2. **Follow Architecture Principles**:
   - Keep `core/` decoupled from web frameworks (`app/`).
   - Store all timestamps as naive UTC in models (`MetricReading`, `Activity`).
   - Keep all credentials encrypted using `CredentialStore`.
   - Ensure all provider syncs are idempotent and respect checkpoint dates.
3. **Write Tests**: Add unit and integration tests under `tests/` for any new logic.
4. **Verify**: Ensure the full test suite passes (`pytest -v`).
5. **Submit a PR**: Open a Pull Request with a clear description of the problem solved and test coverage added.
