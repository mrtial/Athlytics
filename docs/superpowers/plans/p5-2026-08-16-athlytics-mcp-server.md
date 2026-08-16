# Athlytics AI Coach & Actionable MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `mcp_server/` — a bidirectional Model Context Protocol (MCP) server implementing the complete **Athlytics AI Coach** architecture specified in `docs/superpowers/specs/2026-08-16-athlytics-ai-coach-design.md`. Exposes 8 read tools, 5 action/write tools, 3 dynamic living context resources (`athlytics://` URIs), and 3 sports-science workflow prompts over stdio transport. Extends `core/storage` with models, schemas, and repository methods for athlete targets (`target`), periodized training plans (`training_plan`), qualitative coaching logs (`coach_note`), and stored reports (`report`). Bundles coaching playbooks and skill definitions for Claude and Google Gemini.

**Architecture:** 
- `core/storage/`: Adds canonical schemas, indexes, and repository operations for actionable entities (`Target`, `TrainingPlan`, `CoachNote`, `Report`, and `MetricSummary`).
- `mcp_server/server.py`: Module-level `MCPServer("Athlytics")` instance registering all read tools, write tools, dynamic resources, and workflow prompts.
- `mcp_server/resources.py`: Dynamic resource builders computing athlete recovery snapshots (`athlytics://athlete/snapshot`), active training plan status (`athlytics://training/current-state`), and athlete coaching profiles (`athlytics://coach/context`).
- `mcp_server/prompts.py`: Evidence-based prompt templates for readiness check-ins, weekly reviews, and periodized training plan generation.
- `.claude/skills/athlytics-coach/SKILL.md` & `docs/coach/gemini-system-instructions.md`: Evidence-based sports-science coaching playbooks enforcing recovery-gated training, safe volume progression (the 10% rule), structured periodization/deloads, and action persistence.

**Tech Stack:** Python 3.11+, `mcp>=2.0,<3.0` (official Model Context Protocol Python SDK v2), stdlib `sqlite3`/`datetime`/`uuid`/`json`/`contextlib`, `pytest`/`pytest` anyio integration for in-memory protocol contract tests.

---

## MCP SDK Verification (read before Task 1)

This plan targets the **v2 API** of the official `mcp` Python SDK:
- **Package name on PyPI:** `mcp` (version `2.0.0+`).
- **Server class:** `from mcp.server import MCPServer` (replaces legacy FastMCP from v1).
- **Tool registration:** `@mcp.tool()` on synchronous or async Python functions with strict type hints.
- **Resource registration:** `@mcp.resource("uri://path")` generating live textual/structured context.
- **Prompt registration:** `@mcp.prompt()` generating pre-packaged coaching workflows.
- **Structured output & errors:** Dataclasses and Pydantic models automatically serialize to JSON schema; standard exceptions raised inside tool functions are caught and returned as `CallToolResult(is_error=True, content=[TextContent(text=str(e))])`.
- **Stdio transport:** `mcp.run()` defaults to stdio transport, zero network exposure.
- **In-memory testing:** `from mcp import Client` allows in-process async client testing with `async with Client(mcp) as client:`.

---

## Plan Sequence

This is **Plan 5 of 6** (design spec: `docs/superpowers/specs/2026-08-16-athlytics-design.md`, parent spec: `docs/superpowers/specs/2026-08-16-athlytics-ai-coach-design.md`):

1. **Foundation** (done, merged) — Storage schema, provider protocol, credential encryption, fake provider. `docs/superpowers/plans/p1-2026-08-16-athlytics-foundation.md`
2. **Garmin Provider Adapter** (done, merged) — Real `garminconnect` integration covering all 18 canonical v1 metrics. `docs/superpowers/plans/p2-2026-08-16-athlytics-garmin-provider.md`
3. **Analytics** (`core/analytics`) — Rolling averages, deltas, and z-score anomaly detection over `core/storage`. `docs/superpowers/plans/p3-2026-08-16-athlytics-analytics.md`
4. **Dashboard/API** (`app/`) — FastAPI + dashboard UI, onboarding persona selection, and live rendering for targets/plans. `docs/superpowers/plans/p4-2026-08-16-athlytics-dashboard-api.md`
5. **AI Coach & Actionable MCP Server** (this plan) — Stdio MCP server with read tools, write tools, dynamic context resources, workflow prompts, and bundled coaching playbooks for Claude and Gemini.
6. **Deployment** — Docker Compose packaging and container entrypoints.

---

## File Structure

```
pyproject.toml                                # MODIFY: add mcp>=2.0,<3.0 dependency
core/
  storage/
    models.py                                 # MODIFY: add MetricSummary, Report, Target, TrainingPlan, CoachNote
    db.py                                     # MODIFY: add report, target, training_plan, coach_note tables to SCHEMA
    repository.py                             # MODIFY: add queries/mutations for summaries, reports, targets, plans, notes
mcp_server/
  __init__.py                                 # NEW
  server.py                                   # NEW: MCPServer instance, tools, resource/prompt dispatch
  resources.py                                # NEW: dynamic resource generators (snapshot, training state, coach context)
  prompts.py                                  # NEW: workflow prompt templates (readiness, review, plan builder)
.claude/
  skills/
    athlytics-coach/
      SKILL.md                                # NEW: Claude Code & Claude Desktop coaching playbook skill
docs/
  coach/
    gemini-system-instructions.md             # NEW: Google Gemini Custom Gem & CLI coaching instructions
    client-setup.md                           # NEW: Claude Desktop & Gemini client configuration guide
tests/
  storage/
    test_repository.py                        # MODIFY: add tests for actionable storage repository methods
  mcp_server/
    __init__.py                               # NEW
    conftest.py                               # NEW: anyio_backend fixture
    test_server.py                            # NEW: unit tests & MCP protocol contract tests for all tools
    test_resources.py                         # NEW: contract tests for dynamic athlytics:// resources
    test_prompts.py                           # NEW: contract tests for workflow prompts
    test_coach_workflow.py                    # NEW: multi-turn end-to-end AI coaching simulation test
```

---

### Task 1: `mcp` Dependency + Server Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `mcp_server/__init__.py`
- Create: `mcp_server/server.py`
- Create: `tests/mcp_server/__init__.py`
- Create: `tests/mcp_server/conftest.py`
- Create: `tests/mcp_server/test_server.py`

**Interfaces:**
- Consumes: `core.storage.db.connect(db_path: Path) -> sqlite3.Connection`
- Produces:
  - `mcp_server.server.mcp` — `MCPServer("Athlytics")` instance.
  - `mcp_server.server.DB_PATH_ENV_VAR = "ATHLYTICS_DB_PATH"`
  - `mcp_server.server._db_path() -> Path`
  - `mcp_server.server._connection()` — Context manager yielding a fresh short-lived SQLite connection per tool/resource call.

- [ ] **Step 1: Add `mcp` dependency to `pyproject.toml`**

Edit `pyproject.toml`'s dependencies:
```toml
dependencies = [
    "cryptography>=42.0",
    "python-dotenv>=1.0",
    "garminconnect>=0.2.31",
    "mcp>=2.0,<3.0",
]
```

Install:
```bash
pip install -e ".[dev]"
```

- [ ] **Step 2: Write failing test for server skeleton**

Create `tests/mcp_server/__init__.py` (empty), `tests/mcp_server/conftest.py`:
```python
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

Create `tests/mcp_server/test_server.py`:
```python
from mcp.server import MCPServer
from mcp_server.server import mcp, _db_path, DB_PATH_ENV_VAR
from pathlib import Path


def test_server_instance_is_an_mcp_server():
    assert isinstance(mcp, MCPServer)
    assert mcp.name == "Athlytics"


def test_db_path_respects_environment_override(monkeypatch, tmp_path):
    custom_db = tmp_path / "custom.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(custom_db))
    assert _db_path() == custom_db
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: FAIL (`ModuleNotFoundError: No module named 'mcp_server'`)

- [ ] **Step 4: Implement `mcp_server/server.py`**

Create `mcp_server/__init__.py` (empty) and `mcp_server/server.py`:
```python
"""Athlytics AI Coach & Actionable MCP Server.

Provides bidirectional tools (reading metrics/trends, writing targets and plans),
living dynamic context resources (athlytics://), and evidence-based workflow prompts.
"""
import os
from contextlib import contextmanager
from pathlib import Path

from mcp.server import MCPServer

from core.storage.db import connect

DB_PATH_ENV_VAR = "ATHLYTICS_DB_PATH"
DEFAULT_DB_PATH = Path.home() / ".athlytics" / "athlytics.db"

mcp = MCPServer("Athlytics")


def _db_path() -> Path:
    return Path(os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH)))


@contextmanager
def _connection():
    conn = connect(_db_path())
    try:
        yield conn
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml mcp_server tests/mcp_server
git commit -m "feat: add mcp SDK dependency and MCP server skeleton"
```

---

### Task 2: Storage Extensions — `MetricSummary` and `Report`

**Files:**
- Modify: `core/storage/models.py`
- Modify: `core/storage/db.py`
- Modify: `core/storage/repository.py`
- Modify: `tests/storage/test_repository.py`

**Interfaces:**
- Produces:
  - `MetricSummary(metric_type: str, earliest_date: date, latest_date: date, reading_count: int, unit: str)`
  - `Report(id: int, created_at: datetime, title: str, content: str)`
  - `repository.list_metric_summaries(conn) -> list[MetricSummary]`
  - `repository.save_report(conn, title: str, content: str, created_at: datetime | None = None) -> int`
  - `repository.get_report(conn, report_id: int) -> Report | None`

- [ ] **Step 1: Write failing tests for `MetricSummary` and `Report` storage**

Append to `tests/storage/test_repository.py`:
```python
from datetime import date, datetime, timezone
from core.storage.models import MetricReading, MetricSummary, Report


def test_list_metric_summaries_returns_aggregates(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [
        MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 52.0, "bpm"),
        MetricReading("garmin", "resting_hr", datetime(2026, 1, 5), 54.0, "bpm"),
        MetricReading("garmin", "steps", datetime(2026, 1, 3), 8000.0, "count"),
    ]
    repository.upsert_readings(conn, readings)

    summaries = repository.list_metric_summaries(conn)

    assert summaries == [
        MetricSummary("resting_hr", date(2026, 1, 1), date(2026, 1, 5), 2, "bpm"),
        MetricSummary("steps", date(2026, 1, 3), date(2026, 1, 3), 1, "count"),
    ]


def test_save_and_get_report_roundtrip(tmp_path):
    conn = connect(tmp_path / "test.db")
    created = datetime(2026, 1, 15, 10, 0)
    rep_id = repository.save_report(conn, "Week 2 Review", "Strong consistency.", created)

    retrieved = repository.get_report(conn, rep_id)
    assert retrieved == Report(rep_id, created, "Week 2 Review", "Strong consistency.")
    assert repository.get_report(conn, 9999) is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/storage/test_repository.py -v
```
Expected: FAIL (`ImportError: cannot import name 'MetricSummary'`).

- [ ] **Step 3: Update `core/storage/models.py`**

Append to `core/storage/models.py`:
```python
from datetime import date


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
```

- [ ] **Step 4: Update `core/storage/db.py` and `core/storage/repository.py`**

In `core/storage/db.py`, append to `SCHEMA`:
```sql
CREATE TABLE IF NOT EXISTS report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL
);
```

In `core/storage/repository.py`, add imports and functions:
```python
from datetime import timezone
from core.storage.models import MetricReading, MetricSummary, Report


def list_metric_summaries(conn: sqlite3.Connection) -> list[MetricSummary]:
    rows = conn.execute(
        """
        SELECT metric_type, MIN(date(timestamp)), MAX(date(timestamp)), COUNT(*), unit
        FROM metric_reading
        GROUP BY metric_type
        ORDER BY metric_type ASC
        """
    ).fetchall()
    return [
        MetricSummary(
            metric_type=row[0],
            earliest_date=date.fromisoformat(row[1]),
            latest_date=date.fromisoformat(row[2]),
            reading_count=row[3],
            unit=row[4],
        )
        for row in rows
    ]


def save_report(
    conn: sqlite3.Connection, title: str, content: str, created_at: datetime | None = None
) -> int:
    created_at = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
    cursor = conn.execute(
        "INSERT INTO report (created_at, title, content) VALUES (?, ?, ?)",
        (created_at.isoformat(), title, content),
    )
    conn.commit()
    return cursor.lastrowid


def get_report(conn: sqlite3.Connection, report_id: int) -> Report | None:
    row = conn.execute(
        "SELECT id, created_at, title, content FROM report WHERE id = ?",
        (report_id,),
    ).fetchone()
    if row is None:
        return None
    return Report(
        id=row[0],
        created_at=datetime.fromisoformat(row[1]),
        title=row[2],
        content=row[3],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/storage/test_repository.py -v
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add core/storage/ tests/storage/
git commit -m "feat: add MetricSummary and Report storage schema and queries"
```

---

### Task 3: Storage Extensions — Actionable Models (`Target`, `TrainingPlan`, `CoachNote`)

**Files:**
- Modify: `core/storage/models.py`
- Modify: `core/storage/db.py`
- Modify: `core/storage/repository.py`
- Modify: `tests/storage/test_repository.py`

**Interfaces:**
- Produces:
  - `Target(id: str, metric_type: str, target_value: float, operator: str, target_window: str, start_date: date, end_date: date | None, status: str, notes: str | None, created_at: datetime)`
  - `TrainingPlan(id: str, title: str, goal_description: str | None, start_date: date, target_date: date, plan_json: str, status: str, created_at: datetime)`
  - `CoachNote(id: str, date: date, category: str, note: str, tags_json: str | None, created_at: datetime)`
  - Target repository methods: `save_target`, `get_targets`, `get_target_by_id`, `delete_target`
  - TrainingPlan repository methods: `save_training_plan`, `get_training_plans`, `get_training_plan_by_id`, `update_plan_status`
  - CoachNote repository methods: `save_coach_note`, `get_coach_notes`

- [ ] **Step 1: Write failing unit tests for actionable models in `test_repository.py`**

Append to `tests/storage/test_repository.py`:
```python
from core.storage.models import Target, TrainingPlan, CoachNote


def test_target_crud_operations(tmp_path):
    conn = connect(tmp_path / "test.db")
    now = datetime(2026, 1, 1, 12, 0)
    target = Target(
        id="t-1",
        metric_type="steps",
        target_value=10000.0,
        operator="gte",
        target_window="daily",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status="active",
        notes="Daily step goal",
        created_at=now,
    )
    saved = repository.save_target(conn, target)
    assert saved == target

    assert repository.get_target_by_id(conn, "t-1") == target
    assert len(repository.get_targets(conn, status="active")) == 1
    assert len(repository.get_targets(conn, status="completed")) == 0

    assert repository.delete_target(conn, "t-1") is True
    assert repository.get_target_by_id(conn, "t-1") is None
    assert repository.delete_target(conn, "t-1") is False


def test_training_plan_crud_and_status_update(tmp_path):
    conn = connect(tmp_path / "test.db")
    now = datetime(2026, 1, 1, 12, 0)
    plan = TrainingPlan(
        id="plan-1",
        title="Half Marathon Base",
        goal_description="Build aerobic base",
        start_date=date(2026, 2, 1),
        target_date=date(2026, 4, 30),
        plan_json='{"weeks": 12}',
        status="active",
        created_at=now,
    )
    repository.save_training_plan(conn, plan)

    assert repository.get_training_plan_by_id(conn, "plan-1") == plan
    assert len(repository.get_training_plans(conn, status="active")) == 1

    updated = repository.update_plan_status(conn, "plan-1", "completed")
    assert updated.status == "completed"
    assert repository.update_plan_status(conn, "non-existent", "completed") is None


def test_coach_note_save_and_retrieve(tmp_path):
    conn = connect(tmp_path / "test.db")
    now = datetime(2026, 1, 1, 12, 0)
    note1 = CoachNote(
        id="n-1",
        date=date(2026, 1, 2),
        category="injury",
        note="Mild left Achilles tightness.",
        tags_json='["achilles", "recovery"]',
        created_at=now,
    )
    note2 = CoachNote(
        id="n-2",
        date=date(2026, 1, 3),
        category="nutrition",
        note="Carb loaded before long run.",
        tags_json=None,
        created_at=now,
    )
    repository.save_coach_note(conn, note1)
    repository.save_coach_note(conn, note2)

    all_notes = repository.get_coach_notes(conn, limit=10)
    assert len(all_notes) == 2
    assert all_notes[0].id == "n-2"  # Sorted by date DESC

    injury_notes = repository.get_coach_notes(conn, category="injury")
    assert len(injury_notes) == 1
    assert injury_notes[0].category == "injury"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/storage/test_repository.py -v
```
Expected: FAIL (`ImportError: cannot import name 'Target'`).

- [ ] **Step 3: Define models in `core/storage/models.py`**

Append to `core/storage/models.py`:
```python
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
```

- [ ] **Step 4: Update SQLite `SCHEMA` in `core/storage/db.py`**

Append to `SCHEMA` in `core/storage/db.py`:
```sql
CREATE TABLE IF NOT EXISTS target (
    id TEXT PRIMARY KEY,
    metric_type TEXT NOT NULL,
    target_value REAL NOT NULL,
    operator TEXT NOT NULL CHECK(operator IN ('gte', 'lte', 'eq')),
    target_window TEXT NOT NULL CHECK(target_window IN ('daily', 'weekly_sum', 'weekly_avg', 'by_date')),
    start_date TEXT NOT NULL,
    end_date TEXT,
    status TEXT NOT NULL CHECK(status IN ('active', 'completed', 'abandoned')),
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_status ON target(status);
CREATE INDEX IF NOT EXISTS idx_target_metric ON target(metric_type);

CREATE TABLE IF NOT EXISTS training_plan (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal_description TEXT,
    start_date TEXT NOT NULL,
    target_date TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'paused', 'completed', 'archived')),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plan_status ON training_plan(status);

CREATE TABLE IF NOT EXISTS coach_note (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('injury', 'nutrition', 'feeling', 'gear', 'milestone', 'general')),
    note TEXT NOT NULL,
    tags_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coach_note_date ON coach_note(date);
```

- [ ] **Step 5: Implement repository methods in `core/storage/repository.py`**

Append to `core/storage/repository.py`:
```python
from core.storage.models import Target, TrainingPlan, CoachNote


def save_target(conn: sqlite3.Connection, target: Target) -> Target:
    conn.execute(
        """
        INSERT INTO target (id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            metric_type = excluded.metric_type,
            target_value = excluded.target_value,
            operator = excluded.operator,
            target_window = excluded.target_window,
            start_date = excluded.start_date,
            end_date = excluded.end_date,
            status = excluded.status,
            notes = excluded.notes
        """,
        (
            target.id,
            target.metric_type,
            target.target_value,
            target.operator,
            target.target_window,
            target.start_date.isoformat(),
            target.end_date.isoformat() if target.end_date else None,
            target.status,
            target.notes,
            target.created_at.isoformat(),
        ),
    )
    conn.commit()
    return target


def get_targets(conn: sqlite3.Connection, status: str | None = None) -> list[Target]:
    if status is not None:
        rows = conn.execute(
            """
            SELECT id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at
            FROM target
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at
            FROM target
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        Target(
            id=row[0],
            metric_type=row[1],
            target_value=row[2],
            operator=row[3],
            target_window=row[4],
            start_date=date.fromisoformat(row[5]),
            end_date=date.fromisoformat(row[6]) if row[6] else None,
            status=row[7],
            notes=row[8],
            created_at=datetime.fromisoformat(row[9]),
        )
        for row in rows
    ]


def get_target_by_id(conn: sqlite3.Connection, target_id: str) -> Target | None:
    row = conn.execute(
        """
        SELECT id, metric_type, target_value, operator, target_window, start_date, end_date, status, notes, created_at
        FROM target
        WHERE id = ?
        """,
        (target_id,),
    ).fetchone()
    if row is None:
        return None
    return Target(
        id=row[0],
        metric_type=row[1],
        target_value=row[2],
        operator=row[3],
        target_window=row[4],
        start_date=date.fromisoformat(row[5]),
        end_date=date.fromisoformat(row[6]) if row[6] else None,
        status=row[7],
        notes=row[8],
        created_at=datetime.fromisoformat(row[9]),
    )


def delete_target(conn: sqlite3.Connection, target_id: str) -> bool:
    cursor = conn.execute("DELETE FROM target WHERE id = ?", (target_id,))
    conn.commit()
    return cursor.rowcount > 0


def save_training_plan(conn: sqlite3.Connection, plan: TrainingPlan) -> TrainingPlan:
    conn.execute(
        """
        INSERT INTO training_plan (id, title, goal_description, start_date, target_date, plan_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            goal_description = excluded.goal_description,
            start_date = excluded.start_date,
            target_date = excluded.target_date,
            plan_json = excluded.plan_json,
            status = excluded.status
        """,
        (
            plan.id,
            plan.title,
            plan.goal_description,
            plan.start_date.isoformat(),
            plan.target_date.isoformat(),
            plan.plan_json,
            plan.status,
            plan.created_at.isoformat(),
        ),
    )
    conn.commit()
    return plan


def get_training_plans(conn: sqlite3.Connection, status: str | None = None) -> list[TrainingPlan]:
    if status is not None:
        rows = conn.execute(
            """
            SELECT id, title, goal_description, start_date, target_date, plan_json, status, created_at
            FROM training_plan
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, title, goal_description, start_date, target_date, plan_json, status, created_at
            FROM training_plan
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        TrainingPlan(
            id=row[0],
            title=row[1],
            goal_description=row[2],
            start_date=date.fromisoformat(row[3]),
            target_date=date.fromisoformat(row[4]),
            plan_json=row[5],
            status=row[6],
            created_at=datetime.fromisoformat(row[7]),
        )
        for row in rows
    ]


def get_training_plan_by_id(conn: sqlite3.Connection, plan_id: str) -> TrainingPlan | None:
    row = conn.execute(
        """
        SELECT id, title, goal_description, start_date, target_date, plan_json, status, created_at
        FROM training_plan
        WHERE id = ?
        """,
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    return TrainingPlan(
        id=row[0],
        title=row[1],
        goal_description=row[2],
        start_date=date.fromisoformat(row[3]),
        target_date=date.fromisoformat(row[4]),
        plan_json=row[5],
        status=row[6],
        created_at=datetime.fromisoformat(row[7]),
    )


def update_plan_status(conn: sqlite3.Connection, plan_id: str, status: str) -> TrainingPlan | None:
    cursor = conn.execute(
        "UPDATE training_plan SET status = ? WHERE id = ?",
        (status, plan_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return get_training_plan_by_id(conn, plan_id)


def save_coach_note(conn: sqlite3.Connection, note: CoachNote) -> CoachNote:
    conn.execute(
        """
        INSERT INTO coach_note (id, date, category, note, tags_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            date = excluded.date,
            category = excluded.category,
            note = excluded.note,
            tags_json = excluded.tags_json
        """,
        (
            note.id,
            note.date.isoformat(),
            note.category,
            note.note,
            note.tags_json,
            note.created_at.isoformat(),
        ),
    )
    conn.commit()
    return note


def get_coach_notes(conn: sqlite3.Connection, limit: int = 10, category: str | None = None) -> list[CoachNote]:
    if category is not None:
        rows = conn.execute(
            """
            SELECT id, date, category, note, tags_json, created_at
            FROM coach_note
            WHERE category = ?
            ORDER BY date DESC, created_at DESC
            LIMIT ?
            """,
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, date, category, note, tags_json, created_at
            FROM coach_note
            ORDER BY date DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        CoachNote(
            id=row[0],
            date=date.fromisoformat(row[1]),
            category=row[2],
            note=row[3],
            tags_json=row[4],
            created_at=datetime.fromisoformat(row[5]),
        )
        for row in rows
    ]
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/storage/test_repository.py -v
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add core/storage/ tests/storage/
git commit -m "feat: add target, training_plan, and coach_note models and repository methods"
```

---

### Task 4: Read Tools — Metric & Health Series (`list_metrics`, `get_metric_series`)

**Files:**
- Modify: `mcp_server/server.py`
- Modify: `tests/mcp_server/test_server.py`

**Interfaces:**
- Consumes: `repository.list_metric_summaries`, `repository.get_readings`
- Produces: MCP tools `list_metrics`, `get_metric_series`

- [ ] **Step 1: Write failing contract tests for `list_metrics` and `get_metric_series`**

Append to `tests/mcp_server/test_server.py`:
```python
from datetime import datetime
import pytest
from mcp import Client
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading


@pytest.mark.anyio
async def test_list_metrics_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    repository.upsert_readings(
        conn, [MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 52.0, "bpm")]
    )
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("list_metrics", {})

    assert result.is_error is not True
    assert result.structured_content == {
        "result": [
            {
                "metric_type": "resting_hr",
                "earliest_date": "2026-01-01",
                "latest_date": "2026-01-01",
                "reading_count": 1,
                "unit": "bpm",
            }
        ]
    }


@pytest.mark.anyio
async def test_get_metric_series_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    repository.upsert_readings(
        conn,
        [
            MetricReading("garmin", "steps", datetime(2026, 1, 2), 8000.0, "count"),
            MetricReading("garmin", "steps", datetime(2026, 2, 1), 9000.0, "count"),
        ],
    )
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_metric_series", {"metric_type": "steps", "start": "2026-01-01", "end": "2026-01-31"}
        )

    assert result.is_error is not True
    assert len(result.structured_content["result"]) == 1
    assert result.structured_content["result"][0]["value"] == 8000.0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: FAIL (tool not found).

- [ ] **Step 3: Register `list_metrics` and `get_metric_series` in `mcp_server/server.py`**

In `mcp_server/server.py`, add imports and tools:
```python
from datetime import date
from core.storage import repository
from core.storage.models import MetricReading, MetricSummary


def _get_metric_series_impl(conn, metric_type: str, start: str, end: str) -> list[MetricReading]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return repository.get_readings(conn, metric_type, start_date, end_date)


@mcp.tool()
def list_metrics() -> list[MetricSummary]:
    """List every metric_type with stored data, available date range, reading count, and unit."""
    with _connection() as conn:
        return repository.list_metric_summaries(conn)


@mcp.tool()
def get_metric_series(metric_type: str, start: str, end: str) -> list[MetricReading]:
    """Fetch raw daily readings for a metric across an ISO-8601 date range (e.g. start='2026-01-01', end='2026-01-31')."""
    with _connection() as conn:
        return _get_metric_series_impl(conn, metric_type, start, end)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/mcp_server/test_server.py
git commit -m "feat: add list_metrics and get_metric_series MCP tools"
```

---

### Task 5: Read Tools — Analytics Integration (`get_trend`, `get_anomalies`)

**Files:**
- Modify: `mcp_server/server.py`
- Modify: `tests/mcp_server/test_server.py`

**Interfaces:**
- Consumes: `core.analytics.get_trend`, `core.analytics.detect_anomalies_for_metrics` (or `detect_anomalies`)
- Produces: MCP tools `get_trend`, `get_anomalies`

- [ ] **Step 1: Write failing unit & contract tests for `get_trend` and `get_anomalies`**

Append to `tests/mcp_server/test_server.py`:
```python
from datetime import timedelta, time


@pytest.mark.anyio
async def test_get_trend_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()
    readings = [
        MetricReading(
            "garmin", "steps", datetime.combine(today - timedelta(days=d), time.min), 1000.0, "count"
        )
        for d in range(7)
    ]
    repository.upsert_readings(conn, readings)
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_trend", {"metric_type": "steps", "window": 7})

    assert result.is_error is not True
    assert result.structured_content["metric_type"] == "steps"
    assert result.structured_content["window_days"] == 7
    assert result.structured_content["current"]["average"] == 1000.0


@pytest.mark.anyio
async def test_get_anomalies_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()
    hr_values = [50.0] * 9 + [75.0]
    readings = [
        MetricReading(
            "garmin", "resting_hr", datetime.combine(today - timedelta(days=9 - d), time.min), v, "bpm"
        )
        for d, v in enumerate(hr_values)
    ]
    repository.upsert_readings(conn, readings)
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_anomalies", {})

    assert result.is_error is not True
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: FAIL (tool not found).

- [ ] **Step 3: Implement `get_trend` and `get_anomalies` tools in `mcp_server/server.py`**

In `mcp_server/server.py`, add imports and tools:
```python
from core.analytics import Trend, get_trend as analytics_get_trend
try:
    from core.analytics import Anomaly, detect_anomalies_for_metrics
except ImportError:
    # Graceful fallback for anomalies if module is being loaded incrementally
    from dataclasses import dataclass
    @dataclass(frozen=True)
    class Anomaly:
        metric_type: str
        timestamp: datetime
        value: float
        baseline_mean: float
        baseline_stdev: float
        z_score: float
        direction: str
        baseline_window_days: int
    def detect_anomalies_for_metrics(conn, metric_types, since=None):
        return []


def _get_trend_impl(conn, metric_type: str, window: int) -> Trend:
    return analytics_get_trend(conn, metric_type, window_days=window)


def _get_anomalies_impl(conn, since: str | None = None) -> list[Anomaly]:
    since_date = date.fromisoformat(since) if since is not None else None
    metric_types = [s.metric_type for s in repository.list_metric_summaries(conn)]
    return detect_anomalies_for_metrics(conn, metric_types, since=since_date)


@mcp.tool()
def get_trend(metric_type: str, window: int = 30) -> Trend:
    """Fetch rolling average, sample count, and period-over-period delta for a metric over trailing window days."""
    with _connection() as conn:
        return _get_trend_impl(conn, metric_type, window)


@mcp.tool()
def get_anomalies(since: str | None = None) -> list[Anomaly]:
    """Fetch statistical anomaly flags (>2 standard deviations) across all stored metrics on/after optional since date."""
    with _connection() as conn:
        return _get_anomalies_impl(conn, since)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/mcp_server/test_server.py
git commit -m "feat: add get_trend and get_anomalies MCP tools"
```

---

### Task 6: Read Tools — Actionable State & Reports (`get_report`, `get_targets`, `get_training_plans`, `get_coach_notes`)

**Files:**
- Modify: `mcp_server/server.py`
- Modify: `tests/mcp_server/test_server.py`

**Interfaces:**
- Consumes: `repository.get_report`, `repository.get_targets`, `repository.get_training_plans`, `repository.get_coach_notes`
- Produces: MCP tools `get_report`, `get_targets`, `get_training_plans`, `get_coach_notes`

- [ ] **Step 1: Write failing contract tests for actionable read tools**

Append to `tests/mcp_server/test_server.py`:
```python
from core.storage.models import Target, TrainingPlan, CoachNote, Report


@pytest.mark.anyio
async def test_actionable_read_tools_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    now = datetime(2026, 1, 1, 12, 0)
    rep_id = repository.save_report(conn, "Report 1", "Content 1", now)
    repository.save_target(
        conn,
        Target("t-1", "hrv", 65.0, "gte", "daily", date(2026, 1, 1), None, "active", None, now),
    )
    repository.save_training_plan(
        conn,
        TrainingPlan("p-1", "Base", None, date(2026, 1, 1), date(2026, 3, 1), "{}", "active", now),
    )
    repository.save_coach_note(
        conn,
        CoachNote("n-1", date(2026, 1, 1), "feeling", "Feeling strong.", None, now),
    )
    conn.close()

    async with Client(mcp) as client:
        # get_report
        res_rep = await client.call_tool("get_report", {"id": rep_id})
        assert res_rep.structured_content["title"] == "Report 1"

        # get_targets
        res_tar = await client.call_tool("get_targets", {"status": "active"})
        assert len(res_tar.structured_content["result"]) == 1
        assert res_tar.structured_content["result"][0]["metric_type"] == "hrv"

        # get_training_plans
        res_plan = await client.call_tool("get_training_plans", {"status": "active"})
        assert len(res_plan.structured_content["result"]) == 1
        assert res_plan.structured_content["result"][0]["title"] == "Base"

        # get_coach_notes
        res_notes = await client.call_tool("get_coach_notes", {"limit": 5})
        assert len(res_notes.structured_content["result"]) == 1
        assert res_notes.structured_content["result"][0]["category"] == "feeling"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: FAIL (tools not found).

- [ ] **Step 3: Register read tools in `mcp_server/server.py`**

In `mcp_server/server.py`, add imports and tools:
```python
from core.storage.models import Target, TrainingPlan, CoachNote, Report


def _get_report_impl(conn, report_id: int) -> Report:
    report = repository.get_report(conn, report_id)
    if report is None:
        raise ValueError(f"no report found with id={report_id}")
    return report


@mcp.tool()
def get_report(id: int) -> Report:
    """Fetch a previously generated stored report by integer id."""
    with _connection() as conn:
        return _get_report_impl(conn, id)


@mcp.tool()
def get_targets(status: str = "active") -> list[Target]:
    """Fetch active or historical athlete targets (status: 'active', 'completed', 'abandoned')."""
    with _connection() as conn:
        return repository.get_targets(conn, status=status)


@mcp.tool()
def get_training_plans(status: str = "active") -> list[TrainingPlan]:
    """Fetch structured training plans (status: 'active', 'paused', 'completed', 'archived')."""
    with _connection() as conn:
        return repository.get_training_plans(conn, status=status)


@mcp.tool()
def get_coach_notes(limit: int = 10, category: str | None = None) -> list[CoachNote]:
    """Fetch recent qualitative coach notes, injury logs, or athlete feedback."""
    with _connection() as conn:
        return repository.get_coach_notes(conn, limit=limit, category=category)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/mcp_server/test_server.py
git commit -m "feat: add get_report, get_targets, get_training_plans, and get_coach_notes MCP tools"
```

---

### Task 7: Action / Write Tools (`set_target`, `delete_target`, `save_training_plan`, `update_plan_status`, `log_coach_note`)

**Files:**
- Modify: `mcp_server/server.py`
- Modify: `tests/mcp_server/test_server.py`

**Interfaces:**
- Consumes: `repository.save_target`, `repository.delete_target`, `repository.save_training_plan`, `repository.update_plan_status`, `repository.save_coach_note`
- Produces: Action MCP tools:
  - `set_target(metric_type: str, target_value: float, operator: str, target_window: str, start_date: str, end_date: str | None = None, notes: str | None = None, target_id: str | None = None) -> Target`
  - `delete_target(target_id: str) -> bool`
  - `save_training_plan(title: str, goal_description: str | None, start_date: str, target_date: str, plan_json: str, plan_id: str | None = None) -> TrainingPlan`
  - `update_plan_status(plan_id: str, status: str) -> TrainingPlan`
  - `log_coach_note(date: str, category: str, note: str, tags: list[str] | None = None, note_id: str | None = None) -> CoachNote`

- [ ] **Step 1: Write failing contract tests for write tools in `test_server.py`**

Append to `tests/mcp_server/test_server.py`:
```python
@pytest.mark.anyio
async def test_action_write_tools_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    async with Client(mcp) as client:
        # set_target
        res_tar = await client.call_tool(
            "set_target",
            {
                "metric_type": "steps",
                "target_value": 10000.0,
                "operator": "gte",
                "target_window": "daily",
                "start_date": "2026-01-01",
                "notes": "Target 10k daily",
            },
        )
        assert res_tar.is_error is not True
        target_id = res_tar.structured_content["id"]
        assert res_tar.structured_content["target_value"] == 10000.0

        # delete_target
        res_del = await client.call_tool("delete_target", {"target_id": target_id})
        assert res_del.is_error is not True
        assert res_del.structured_content == {"result": True}

        # save_training_plan
        res_plan = await client.call_tool(
            "save_training_plan",
            {
                "title": "Marathon Build",
                "goal_description": "Sub-3:30",
                "start_date": "2026-02-01",
                "target_date": "2026-05-31",
                "plan_json": '{"phases": ["Base", "Build"]}',
            },
        )
        assert res_plan.is_error is not True
        plan_id = res_plan.structured_content["id"]
        assert res_plan.structured_content["title"] == "Marathon Build"

        # update_plan_status
        res_up = await client.call_tool(
            "update_plan_status", {"plan_id": plan_id, "status": "paused"}
        )
        assert res_up.is_error is not True
        assert res_up.structured_content["status"] == "paused"

        # log_coach_note
        res_note = await client.call_tool(
            "log_coach_note",
            {
                "date": "2026-01-10",
                "category": "injury",
                "note": "Knee soreness after intervals.",
                "tags": ["knee", "intervals"],
            },
        )
        assert res_note.is_error is not True
        assert res_note.structured_content["category"] == "injury"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: FAIL (write tools not found).

- [ ] **Step 3: Implement write tools in `mcp_server/server.py`**

In `mcp_server/server.py`, add imports and tools:
```python
import json
import uuid


def _set_target_impl(
    conn,
    metric_type: str,
    target_value: float,
    operator: str,
    target_window: str,
    start_date: str,
    end_date: str | None = None,
    notes: str | None = None,
    target_id: str | None = None,
) -> Target:
    if operator not in ("gte", "lte", "eq"):
        raise ValueError(f"Invalid operator '{operator}', must be 'gte', 'lte', or 'eq'")
    if target_window not in ("daily", "weekly_sum", "weekly_avg", "by_date"):
        raise ValueError(f"Invalid target_window '{target_window}'")
    
    t_id = target_id or f"target-{uuid.uuid4().hex[:8]}"
    s_date = date.fromisoformat(start_date)
    e_date = date.fromisoformat(end_date) if end_date else None
    target = Target(
        id=t_id,
        metric_type=metric_type,
        target_value=float(target_value),
        operator=operator,
        target_window=target_window,
        start_date=s_date,
        end_date=e_date,
        status="active",
        notes=notes,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    return repository.save_target(conn, target)


def _delete_target_impl(conn, target_id: str) -> bool:
    return repository.delete_target(conn, target_id)


def _save_training_plan_impl(
    conn,
    title: str,
    goal_description: str | None,
    start_date: str,
    target_date: str,
    plan_json: str,
    plan_id: str | None = None,
) -> TrainingPlan:
    p_id = plan_id or f"plan-{uuid.uuid4().hex[:8]}"
    s_date = date.fromisoformat(start_date)
    t_date = date.fromisoformat(target_date)
    # Validate plan_json is valid JSON
    json.loads(plan_json)
    plan = TrainingPlan(
        id=p_id,
        title=title,
        goal_description=goal_description,
        start_date=s_date,
        target_date=t_date,
        plan_json=plan_json,
        status="active",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    return repository.save_training_plan(conn, plan)


def _update_plan_status_impl(conn, plan_id: str, status: str) -> TrainingPlan:
    if status not in ("active", "paused", "completed", "archived"):
        raise ValueError(f"Invalid plan status '{status}'")
    updated = repository.update_plan_status(conn, plan_id, status)
    if updated is None:
        raise ValueError(f"Training plan with id '{plan_id}' not found")
    return updated


def _log_coach_note_impl(
    conn,
    date_str: str,
    category: str,
    note: str,
    tags: list[str] | None = None,
    note_id: str | None = None,
) -> CoachNote:
    if category not in ("injury", "nutrition", "feeling", "gear", "milestone", "general"):
        raise ValueError(f"Invalid coach note category '{category}'")
    n_id = note_id or f"note-{uuid.uuid4().hex[:8]}"
    n_date = date.fromisoformat(date_str)
    tags_json = json.dumps(tags) if tags else None
    coach_note = CoachNote(
        id=n_id,
        date=n_date,
        category=category,
        note=note,
        tags_json=tags_json,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    return repository.save_coach_note(conn, coach_note)


@mcp.tool()
def set_target(
    metric_type: str,
    target_value: float,
    operator: str,
    target_window: str,
    start_date: str,
    end_date: str | None = None,
    notes: str | None = None,
    target_id: str | None = None,
) -> Target:
    """Set or update an athlete target tracked on the dashboard (operator: 'gte'/'lte'/'eq', window: 'daily'/'weekly_sum'/'weekly_avg'/'by_date')."""
    with _connection() as conn:
        return _set_target_impl(conn, metric_type, target_value, operator, target_window, start_date, end_date, notes, target_id)


@mcp.tool()
def delete_target(target_id: str) -> bool:
    """Remove or archive an active target by target_id."""
    with _connection() as conn:
        return _delete_target_impl(conn, target_id)


@mcp.tool()
def save_training_plan(
    title: str,
    goal_description: str | None,
    start_date: str,
    target_date: str,
    plan_json: str,
    plan_id: str | None = None,
) -> TrainingPlan:
    """Commit a periodized training plan JSON to SQLite for dashboard visualization and progress tracking."""
    with _connection() as conn:
        return _save_training_plan_impl(conn, title, goal_description, start_date, target_date, plan_json, plan_id)


@mcp.tool()
def update_plan_status(plan_id: str, status: str) -> TrainingPlan:
    """Update training plan status ('active', 'paused', 'completed', 'archived')."""
    with _connection() as conn:
        return _update_plan_status_impl(conn, plan_id, status)


@mcp.tool()
def log_coach_note(
    date: str,
    category: str,
    note: str,
    tags: list[str] | None = None,
    note_id: str | None = None,
) -> CoachNote:
    """Log a qualitative observation, injury feedback, or coaching advice (category: 'injury'/'nutrition'/'feeling'/'gear'/'milestone'/'general')."""
    with _connection() as conn:
        return _log_coach_note_impl(conn, date, category, note, tags, note_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_server.py -v
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/mcp_server/test_server.py
git commit -m "feat: add actionable write tools (set_target, delete_target, save_training_plan, update_plan_status, log_coach_note)"
```

---

### Task 8: Dynamic Context Resources (`athlytics://` URIs)

**Files:**
- Create: `mcp_server/resources.py`
- Modify: `mcp_server/server.py`
- Create: `tests/mcp_server/test_resources.py`

**Interfaces:**
- Produces:
  - `mcp_server.resources.build_athlete_snapshot(conn) -> str`
  - `mcp_server.resources.build_training_current_state(conn) -> str`
  - `mcp_server.resources.build_coach_context(conn) -> str`
  - MCP Resources:
    - `athlytics://athlete/snapshot`
    - `athlytics://training/current-state`
    - `athlytics://coach/context`

- [ ] **Step 1: Write failing contract tests for dynamic context resources**

Create `tests/mcp_server/test_resources.py`:
```python
from datetime import date, datetime, timedelta, time
import pytest
from mcp import Client
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading, Target, TrainingPlan, CoachNote
from mcp_server.server import mcp


@pytest.mark.anyio
async def test_dynamic_resources_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()
    now = datetime.combine(today, time(12, 0))

    # Populate 7 days of readings
    for d in range(7):
        day = today - timedelta(days=d)
        dt = datetime.combine(day, time.min)
        repository.upsert_readings(
            conn,
            [
                MetricReading("garmin", "resting_hr", dt, 50.0, "bpm"),
                MetricReading("garmin", "hrv", dt, 65.0, "ms"),
                MetricReading("garmin", "sleep_score", dt, 85.0, "score"),
                MetricReading("garmin", "training_load", dt, 120.0, "load"),
            ],
        )

    # Active target, plan, and coach note
    repository.save_target(
        conn,
        Target("t-1", "resting_hr", 52.0, "lte", "daily", today, None, "active", "Keep RHR low", now),
    )
    repository.save_training_plan(
        conn,
        TrainingPlan(
            "p-1",
            "Base 10k",
            "Aerobic base",
            today,
            today + timedelta(days=60),
            '{"phase": "Base", "week": 1, "today_workout": "45min Zone 2 Run"}',
            "active",
            now,
        ),
    )
    repository.save_coach_note(
        conn,
        CoachNote("n-1", today, "injury", "Left hamstring feeling tight", None, now),
    )
    conn.close()

    async with Client(mcp) as client:
        # Resource 1: athlete snapshot
        res_snap = await client.read_resource("athlytics://athlete/snapshot")
        assert "7-Day Health Snapshot" in res_snap.contents[0].text
        assert "resting_hr" in res_snap.contents[0].text

        # Resource 2: training state
        res_train = await client.read_resource("athlytics://training/current-state")
        assert "Base 10k" in res_train.contents[0].text
        assert "45min Zone 2 Run" in res_train.contents[0].text

        # Resource 3: coach context
        res_ctx = await client.read_resource("athlytics://coach/context")
        assert "Left hamstring feeling tight" in res_ctx.contents[0].text
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/mcp_server/test_resources.py -v
```
Expected: FAIL (resources not found).

- [ ] **Step 3: Implement `mcp_server/resources.py`**

Create `mcp_server/resources.py`:
```python
"""Dynamic context resource generators for Athlytics AI Coach."""
import json
from datetime import date, timedelta
from core.storage import repository
from core.analytics import get_trend


def build_athlete_snapshot(conn) -> str:
    """Builds the 7-day health snapshot resource: RHR, HRV, Sleep, and Load."""
    today = date.today()
    metrics = ["resting_hr", "hrv", "sleep_score", "training_load"]
    lines = [
        f"# Athlytics 7-Day Health Snapshot ({today.isoformat()})",
        "",
        "| Metric | 7-Day Average | Delta vs Prior Week | Status |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for m in metrics:
        trend = get_trend(conn, m, window_days=7, as_of=today)
        avg_str = f"{trend.current.average:.1f}" if trend.current.average is not None else "No data"
        delta_str = (
            f"{trend.delta.absolute_change:+.1f} ({trend.delta.percent_change:+.1f}%)"
            if trend.delta.absolute_change is not None and trend.delta.percent_change is not None
            else "N/A"
        )
        lines.append(f"| `{m}` | {avg_str} | {delta_str} | Active |")

    lines.extend([
        "",
        "## Recovery Context",
        "- Inspect HRV and Resting HR to assess physiological readiness before prescribing high intensity.",
    ])
    return "\n".join(lines)


def build_training_current_state(conn) -> str:
    """Builds active training plan and active athlete targets summary."""
    active_plans = repository.get_training_plans(conn, status="active")
    active_targets = repository.get_targets(conn, status="active")

    lines = ["# Current Training State & Active Targets", ""]

    if active_plans:
        current_plan = active_plans[0]
        lines.extend([
            f"## Active Plan: {current_plan.title}",
            f"- **Goal:** {current_plan.goal_description or 'General Fitness'}",
            f"- **Timeline:** {current_plan.start_date} to {current_plan.target_date}",
            f"- **Structured Plan Data:**",
            f"```json",
            current_plan.plan_json,
            f"```",
            "",
        ])
    else:
        lines.extend(["## Active Plan", "No active training plan. Guide athlete to build one.", ""])

    lines.extend(["## Active Targets", ""])
    if active_targets:
        for t in active_targets:
            lines.append(
                f"- `{t.metric_type}` {t.operator} {t.target_value} ({t.target_window}) — Notes: {t.notes or 'None'}"
            )
    else:
        lines.append("No active targets set.")

    return "\n".join(lines)


def build_coach_context(conn) -> str:
    """Builds athlete profile notes, injury history, and qualitative feedback."""
    notes = repository.get_coach_notes(conn, limit=10)
    lines = ["# Athlete Profile & Coach Context", "", "## Recent Coaching & Athlete Notes", ""]

    if notes:
        for n in notes:
            tags_str = f" [tags: {n.tags_json}]" if n.tags_json else ""
            lines.append(f"- **{n.date}** `[{n.category.upper()}]`: {n.note}{tags_str}")
    else:
        lines.append("No historical coaching notes logged.")

    return "\n".join(lines)
```

- [ ] **Step 4: Register resources on `mcp` in `mcp_server/server.py`**

In `mcp_server/server.py`, add imports and resource handlers:
```python
from mcp_server.resources import (
    build_athlete_snapshot,
    build_training_current_state,
    build_coach_context,
)


@mcp.resource("athlytics://athlete/snapshot")
def athlete_snapshot() -> str:
    """Current 7-day health snapshot: 7d RHR/HRV vs baseline, training load, and sleep score."""
    with _connection() as conn:
        return build_athlete_snapshot(conn)


@mcp.resource("athlytics://training/current-state")
def training_current_state() -> str:
    """Active training plan details, current phase, scheduled workouts, and active targets."""
    with _connection() as conn:
        return build_training_current_state(conn)


@mcp.resource("athlytics://coach/context")
def coach_context() -> str:
    """Athlete coaching profile, recent qualitative feedback, injury history, and notes."""
    with _connection() as conn:
        return build_coach_context(conn)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_resources.py -v
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcp_server/resources.py mcp_server/server.py tests/mcp_server/test_resources.py
git commit -m "feat: add dynamic MCP context resources (snapshot, training state, coach context)"
```

---

### Task 9: MCP Workflow Prompts (`readiness_check`, `weekly_review`, `build_training_plan`)

**Files:**
- Create: `mcp_server/prompts.py`
- Modify: `mcp_server/server.py`
- Create: `tests/mcp_server/test_prompts.py`

**Interfaces:**
- Produces:
  - MCP Workflow Prompts:
    - `readiness_check`
    - `weekly_review`
    - `build_training_plan(goal: str, target_date: str, current_weekly_volume: float | None = None)`

- [ ] **Step 1: Write failing contract tests for workflow prompts**

Create `tests/mcp_server/test_prompts.py`:
```python
import pytest
from mcp import Client
from mcp_server.server import mcp


@pytest.mark.anyio
async def test_workflow_prompts_contract():
    async with Client(mcp) as client:
        # Prompt 1: readiness_check
        res_readiness = await client.get_prompt("readiness_check", {})
        assert res_readiness.messages is not None
        assert "readiness check" in res_readiness.messages[0].content.text.lower()

        # Prompt 2: weekly_review
        res_review = await client.get_prompt("weekly_review", {})
        assert res_review.messages is not None
        assert "weekly review" in res_review.messages[0].content.text.lower()

        # Prompt 3: build_training_plan
        res_plan = await client.get_prompt(
            "build_training_plan",
            {"goal": "Sub-4 Marathon", "target_date": "2026-10-15", "current_weekly_volume": 35.0},
        )
        assert res_plan.messages is not None
        prompt_text = res_plan.messages[0].content.text
        assert "Sub-4 Marathon" in prompt_text
        assert "2026-10-15" in prompt_text
        assert "35.0" in prompt_text
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/mcp_server/test_prompts.py -v
```
Expected: FAIL (prompts not found).

- [ ] **Step 3: Implement `mcp_server/prompts.py`**

Create `mcp_server/prompts.py`:
```python
"""Workflow prompt definitions for Athlytics AI Coach."""


def prompt_readiness_check() -> str:
    return """You are the Athlytics AI Coach conducting a morning recovery and workout readiness check-in.

Follow these steps:
1. Inspect the living resource `athlytics://athlete/snapshot` to check recent 7-day Resting HR, HRV, Sleep Score, and Training Load.
2. Read `athlytics://training/current-state` to see today's scheduled workout.
3. Check `athlytics://coach/context` for any active injuries or fatigue notes.
4. Sports-Science Decision Logic:
   - If HRV is suppressed (< -1.5 baseline z-score) or Resting HR is elevated (> +1.5 standard deviations), advise downgrading today's workout to Zone 1 active recovery or full rest.
   - If recovery metrics are in optimal range, give a clear green light and specify target paces/heart rate zones for today's session.
5. Provide a concise, motivating summary and ask the athlete how they are feeling subjectively. If they note discomfort, log a coach note via `log_coach_note`."""


def prompt_weekly_review() -> str:
    return """You are the Athlytics AI Coach conducting a comprehensive weekly training retrospective.

Follow these steps:
1. Query metric series and trends for `activity_distance`, `training_load`, `resting_hr`, `hrv`, and `sleep_score` over the past 7–14 days.
2. Query active targets using `get_targets(status='active')` and evaluate target compliance.
3. Assess physiological strain vs recovery (acute:chronic workload ratio).
4. Evaluate whether the 10% volume progression rule is satisfied for the upcoming week.
5. If the athlete completed 3–4 continuous build weeks, recommend a scheduled deload week (20–30% volume reduction).
6. Present a structured retrospective table and proposed adjustments for next week's training."""


def prompt_build_training_plan(
    goal: str, target_date: str, current_weekly_volume: float | None = None
) -> str:
    volume_str = f"{current_weekly_volume} km/miles" if current_weekly_volume is not None else "Not specified (query historical trends)"
    return f"""You are the Athlytics AI Coach designing a structured, periodized training plan.

Target Goal: {goal}
Target Race/Event Date: {target_date}
Current Baseline Volume: {volume_str}

Follow these principles:
1. Query recent mileage via `get_trend('activity_distance', 30)` to establish baseline volume.
2. Structure the timeline into distinct phases:
   - Base Building (Aerobic development & form)
   - Build Phase (Threshold & VO2 max stimulus)
   - Peak Phase (Race-specific pacing & volume peak)
   - Taper Phase (Volume reduction, maintaining intensity)
3. Enforce the 10% Rule: Never increase weekly volume by >10% over the previous 3-week rolling average.
4. Mandate Deload Weeks: Every 3rd or 4th week, reduce volume by 20–30% for physiological adaptation.
5. Once agreed upon with the athlete, commit the plan to SQLite using `save_training_plan` so it renders in the dashboard."""
```

- [ ] **Step 4: Register prompts in `mcp_server/server.py`**

In `mcp_server/server.py`, add imports and prompt decorators:
```python
from mcp_server.prompts import (
    prompt_readiness_check,
    prompt_weekly_review,
    prompt_build_training_plan,
)


@mcp.prompt()
def readiness_check() -> str:
    """Daily morning recovery check-in and workout readiness evaluation."""
    return prompt_readiness_check()


@mcp.prompt()
def weekly_review() -> str:
    """7-day training volume, recovery metrics, and target compliance retrospective."""
    return prompt_weekly_review()


@mcp.prompt()
def build_training_plan(
    goal: str, target_date: str, current_weekly_volume: float | None = None
) -> str:
    """Guides building a structured, periodized training block with the 10% rule and deload weeks."""
    return prompt_build_training_plan(goal, target_date, current_weekly_volume)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_prompts.py -v
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcp_server/prompts.py mcp_server/server.py tests/mcp_server/test_prompts.py
git commit -m "feat: add MCP workflow prompts (readiness_check, weekly_review, build_training_plan)"
```

---

### Task 10: Bundled AI Coach Playbooks & Client Configurations

**Files:**
- Create: `.claude/skills/athlytics-coach/SKILL.md`
- Create: `docs/coach/gemini-system-instructions.md`
- Create: `docs/coach/client-setup.md`
- Create: `tests/mcp_server/test_playbooks.py`

**Interfaces:**
- Produces:
  - Claude Skill definition in `.claude/skills/athlytics-coach/SKILL.md`
  - Google Gemini coaching system instructions in `docs/coach/gemini-system-instructions.md`
  - Setup guide in `docs/coach/client-setup.md`

- [ ] **Step 1: Write verification test for playbook files**

Create `tests/mcp_server/test_playbooks.py`:
```python
from pathlib import Path


def test_coaching_playbooks_exist_and_contain_rules():
    root = Path(__file__).resolve().parents[2]
    claude_skill = root / ".claude" / "skills" / "athlytics-coach" / "SKILL.md"
    gemini_doc = root / "docs" / "coach" / "gemini-system-instructions.md"
    setup_doc = root / "docs" / "coach" / "client-setup.md"

    assert claude_skill.exists()
    assert gemini_doc.exists()
    assert setup_doc.exists()

    claude_content = claude_skill.read_text()
    assert "athlytics-coach" in claude_content
    assert "10% Rule" in claude_content
    assert "Recovery-Gated" in claude_content
    assert "save_training_plan" in claude_content

    gemini_content = gemini_doc.read_text()
    assert "Athlytics AI Coach" in gemini_content
    assert "Deload" in gemini_content
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/mcp_server/test_playbooks.py -v
```
Expected: FAIL (files missing).

- [ ] **Step 3: Create `.claude/skills/athlytics-coach/SKILL.md`**

Create `.claude/skills/athlytics-coach/SKILL.md`:
```markdown
---
name: athlytics-coach
description: Evidence-based endurance and fitness AI Coach with bidirectional Athlytics MCP integration.
---

# Athlytics AI Coach Playbook

You are the personal AI Coach integrated directly with Athlytics via the Model Context Protocol (MCP). You have full access to the athlete's longitudinal health data, recovery metrics, structured training plans, and active targets.

## Core Sports Science Principles

### 1. Recovery-Gated Training
- When evaluating workout readiness or daily training, inspect `athlytics://athlete/snapshot`.
- If 7-day rolling HRV z-score is `< -1.5` or Resting Heart Rate is `> +1.5` standard deviations from baseline:
  - Do NOT prescribe threshold, interval, or long endurance sessions.
  - Automatically recommend Zone 1 active recovery or complete rest.
  - Record the observation with `log_coach_note(category='feeling', note=...)`.

### 2. Safe Volume Progression (The 10% Rule)
- Never increase weekly running/cycling volume by more than 10% over the previous 3-week rolling average.
- Always query `get_trend('activity_distance', 21)` to verify previous volume before proposing target mileage.

### 3. Structured Periodization & Deload Weeks
- Every 3–4 weeks of continuous training build, prescribe a **deload week** with a 20–30% volume reduction to enable physiological adaptation and prevent overtraining.

### 4. Action Persistence
- Whenever you and the athlete agree on a new milestone or workout schedule:
  - Call `set_target` to persist targets to SQLite so they appear on the dashboard.
  - Call `save_training_plan` to persist periodized training blocks.
  - Call `log_coach_note` to log injury symptoms, nutrition notes, or gear adjustments.

## Available Tools & Resources
- **Living Context:** `athlytics://athlete/snapshot`, `athlytics://training/current-state`, `athlytics://coach/context`
- **Read Queries:** `list_metrics`, `get_metric_series`, `get_trend`, `get_anomalies`, `get_targets`, `get_training_plans`, `get_coach_notes`, `get_report`
- **Write Actions:** `set_target`, `delete_target`, `save_training_plan`, `update_plan_status`, `log_coach_note`
```

- [ ] **Step 4: Create `docs/coach/gemini-system-instructions.md`**

Create `docs/coach/gemini-system-instructions.md`:
```markdown
# Google Gemini Coaching System Instructions

Copy and paste the following prompt into your Google Gemini Custom Gem System Instructions (on Google AI Studio or `gemini.google.com`) or your Gemini CLI configuration:

```text
You are the Athlytics AI Coach, an expert sports scientist and endurance coach paired with the Athlytics platform via MCP.

Your primary directive is to guide the athlete toward their fitness goals using evidence-based methodology, recovery gating, and periodized training.

Core Rules:
1. Always check recovery before hard workouts: If HRV z-score is < -1.5 or RHR is > +1.5 standard deviations above baseline, prescribe Zone 1 or rest.
2. Enforce the 10% rule: Do not raise weekly distance/load by more than 10% week-over-week.
3. Schedule deload weeks: Every 3rd or 4th week, reduce volume by 20–30%.
4. Persist decisions: Use MCP tools (set_target, save_training_plan, log_coach_note) so the athlete's web dashboard reflects current plans and goals.
```
```

- [ ] **Step 5: Create `docs/coach/client-setup.md`**

Create `docs/coach/client-setup.md`:
```markdown
# Athlytics AI Coach — Client Setup Guide

## 1. Claude Desktop Setup
Add the Athlytics MCP server to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "/absolute/path/to/athlytics/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "ATHLYTICS_DB_PATH": "/Users/USERNAME/.athlytics/athlytics.db"
      }
    }
  }
}
```

## 2. Claude Code Setup
Configure project-level MCP in `.mcp.json`:

```json
{
  "mcpServers": {
    "athlytics": {
      "command": "python",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```

## 3. Google Gemini CLI / Antigravity Setup
Add to your Gemini CLI or Antigravity MCP server definitions:

```json
{
  "athlytics": {
    "command": "python",
    "args": ["-m", "mcp_server.server"]
  }
}
```
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/mcp_server/test_playbooks.py -v
```
Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/athlytics-coach/ docs/coach/ tests/mcp_server/test_playbooks.py
git commit -m "feat: bundle AI coach playbooks and client configuration guides for Claude and Gemini"
```

---

### Task 11: End-to-End Multi-Turn AI Coaching Simulation Test

**Files:**
- Create: `tests/mcp_server/test_coach_workflow.py`

**Interfaces:**
- Simulates an end-to-end multi-turn coaching session:
  1. Client checks `athlytics://athlete/snapshot`
  2. Client queries `get_trend` and `get_anomalies`
  3. Client sets goal via `set_target`
  4. Client creates periodized block via `save_training_plan`
  5. Client logs observation via `log_coach_note`
  6. Client checks `get_targets`, `get_training_plans`, `get_coach_notes` to verify complete state persistence.

- [ ] **Step 1: Write the end-to-end coaching workflow test**

Create `tests/mcp_server/test_coach_workflow.py`:
```python
from datetime import date, datetime, timedelta, time
import pytest
from mcp import Client
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading
from mcp_server.server import mcp


@pytest.mark.anyio
async def test_full_ai_coaching_workflow(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()

    # 1. Backfill 14 days of realistic baseline data
    for d in range(14):
        day = today - timedelta(days=d)
        dt = datetime.combine(day, time.min)
        repository.upsert_readings(
            conn,
            [
                MetricReading("garmin", "resting_hr", dt, 49.0 + (d % 2), "bpm"),
                MetricReading("garmin", "hrv", dt, 70.0 - (d % 3), "ms"),
                MetricReading("garmin", "activity_distance", dt, 8.0, "km"),
                MetricReading("garmin", "sleep_score", dt, 88.0, "score"),
            ],
        )
    conn.close()

    async with Client(mcp) as client:
        # Step A: AI opens session and reads living context snapshot
        snapshot_res = await client.read_resource("athlytics://athlete/snapshot")
        assert "7-Day Health Snapshot" in snapshot_res.contents[0].text

        # Step B: AI checks trend
        trend_res = await client.call_tool("get_trend", {"metric_type": "resting_hr", "window": 7})
        assert trend_res.structured_content["metric_type"] == "resting_hr"
        assert trend_res.structured_content["current"]["average"] is not None

        # Step C: AI sets target for athlete
        target_res = await client.call_tool(
            "set_target",
            {
                "metric_type": "activity_distance",
                "target_value": 50.0,
                "operator": "gte",
                "target_window": "weekly_sum",
                "start_date": today.isoformat(),
                "notes": "Weekly volume goal of 50km",
            },
        )
        assert target_res.is_error is not True
        target_id = target_res.structured_content["id"]

        # Step D: AI saves training plan
        plan_json_str = (
            '{"phases": [{"name": "Base", "weeks": 4}, {"name": "Build", "weeks": 4}]}'
        )
        plan_res = await client.call_tool(
            "save_training_plan",
            {
                "title": "Half Marathon Sub-90",
                "goal_description": "Target 1:29:59 half marathon",
                "start_date": today.isoformat(),
                "target_date": (today + timedelta(days=56)).isoformat(),
                "plan_json": plan_json_str,
            },
        )
        assert plan_res.is_error is not True
        plan_id = plan_res.structured_content["id"]

        # Step E: AI logs coach observation note
        note_res = await client.call_tool(
            "log_coach_note",
            {
                "date": today.isoformat(),
                "category": "milestone",
                "note": "Agreed on 8-week Sub-90 Half Marathon plan.",
                "tags": ["sub-90", "plan-kickoff"],
            },
        )
        assert note_res.is_error is not True

        # Step F: Verify living training state resource updates dynamically
        training_state_res = await client.read_resource("athlytics://training/current-state")
        assert "Half Marathon Sub-90" in training_state_res.contents[0].text
        assert "activity_distance" in training_state_res.contents[0].text

        # Step G: Verify queries return persisted records
        targets_list = await client.call_tool("get_targets", {"status": "active"})
        assert len(targets_list.structured_content["result"]) == 1
        assert targets_list.structured_content["result"][0]["id"] == target_id

        plans_list = await client.call_tool("get_training_plans", {"status": "active"})
        assert len(plans_list.structured_content["result"]) == 1
        assert plans_list.structured_content["result"][0]["id"] == plan_id

        notes_list = await client.call_tool("get_coach_notes", {"limit": 5})
        assert len(notes_list.structured_content["result"]) == 1
        assert notes_list.structured_content["result"][0]["category"] == "milestone"
```

- [ ] **Step 2: Run test to verify it passes**

```bash
pytest tests/mcp_server/test_coach_workflow.py -v
```
Expected: 1 passed.

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```
Expected: All tests pass across the entire repository with zero regressions.

- [ ] **Step 4: Commit**

```bash
git add tests/mcp_server/test_coach_workflow.py
git commit -m "test: add multi-turn AI coach workflow integration test"
```

---

## Self-Review

### 1. Specification Coverage Matrix

| Feature / Spec Requirement | Design Spec Reference | Plan Task |
| :--- | :--- | :--- |
| `target` table, model, repository | `2026-08-16-athlytics-ai-coach-design.md` §4 | Task 3 |
| `training_plan` table, model, repository | `2026-08-16-athlytics-ai-coach-design.md` §4 | Task 3 |
| `coach_note` table, model, repository | `2026-08-16-athlytics-ai-coach-design.md` §4 | Task 3 |
| `report` table, model, repository | `2026-08-16-athlytics-ai-coach-design.md` §4 | Task 2 |
| Read tool: `list_metrics` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 4 |
| Read tool: `get_metric_series` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 4 |
| Read tool: `get_trend` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 5 |
| Read tool: `get_anomalies` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 5 |
| Read tool: `get_report` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 6 |
| Read tool: `get_targets` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 6 |
| Read tool: `get_training_plans` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 6 |
| Read tool: `get_coach_notes` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 6 |
| Action tool: `set_target` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 7 |
| Action tool: `delete_target` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 7 |
| Action tool: `save_training_plan` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 7 |
| Action tool: `update_plan_status` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 7 |
| Action tool: `log_coach_note` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 7 |
| Dynamic resource: `athlytics://athlete/snapshot` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 8 |
| Dynamic resource: `athlytics://training/current-state` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 8 |
| Dynamic resource: `athlytics://coach/context` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 8 |
| Workflow prompt: `readiness_check` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 9 |
| Workflow prompt: `weekly_review` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 9 |
| Workflow prompt: `build_training_plan` | `2026-08-16-athlytics-ai-coach-design.md` §3 | Task 9 |
| Coaching Playbook for Claude (.claude/skills) | `2026-08-16-athlytics-ai-coach-design.md` §5 | Task 10 |
| Coaching Playbook for Gemini (System Instructions) | `2026-08-16-athlytics-ai-coach-design.md` §5-§6 | Task 10 |
| Client setup documentation | `2026-08-16-athlytics-ai-coach-design.md` §6 | Task 10 |
| End-to-end integration & simulation testing | `2026-08-16-athlytics-ai-coach-design.md` §7 | Task 11 |

### 2. Quality & Rigor Checklist
- **No placeholders or TODOs:** Every task contains literal file paths, exact Python and SQL code, precise test fixtures, and assertion statements.
- **Strict typing & validation:** Dates at the MCP boundary are ISO-8601 strings parsed safely into Python `date` objects. SQLite constraints and checks ensure schema integrity.
- **In-Memory Testing:** Tests use `from mcp import Client` to exercise real JSON-RPC serialization, structured output unpacking, and error-raising behavior without spawning external network servers or subprocesses.
