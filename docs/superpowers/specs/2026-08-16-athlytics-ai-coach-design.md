# Athlytics AI Coach & Actionable MCP Architecture — Design Doc

Date: 2026-08-16
Status: Draft, pending review
Parent Spec: `docs/superpowers/specs/2026-08-16-athlytics-design.md`

---

## 1. Executive Summary

Athlytics stores longitudinal personal fitness data (Garmin Connect, and future health sources) and computes statistical baselines, trends, and anomalies. While the web dashboard provides static and time-series visualization, athletes need an **interactive, intelligent AI Coach** to:
1. **Answer complex, multi-metric questions** (e.g., *"How has my sleep quality impacted my 10k pacing over the last 3 months?"*).
2. **Conduct daily readiness check-ins** (e.g., assessing morning HRV/RHR against baseline to adjust today's scheduled workout).
3. **Build periodized training plans** (e.g., 12-week half-marathon or base-building blocks).
4. **Set and track dynamic athletic targets** (e.g., weekly mileage thresholds, sleep consistency goals).

This document specifies the architecture for the **Athlytics AI Coach**, implemented via an **Actionable, Bidirectional Model Context Protocol (MCP)** server coupled with athlete context resources and sports-science coaching playbooks. This design supports both **Claude** (Claude Desktop, Claude Code) and **Google Gemini** (Gemini CLI, Antigravity, Custom Gems) with zero token-handling overhead or LLM API key management inside the core Athlytics application.

---

## 2. Architecture & Design Rationale

### Why MCP-Centric over Embedded In-App Chat?

The parent design doc (`2026-08-16-athlytics-design.md`) explicitly identified in-app chat as a non-goal for v1. Evaluating a full in-app chat engine versus an expanded MCP server reveals clear architectural advantages:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        External AI Clients                             │
│       (Claude Desktop / Claude Code / Gemini CLI / Custom Gems)         │
│  • Manages multi-turn conversation memory, context, and reasoning     │
│  • Employs frontier models (Claude 3.7 Sonnet, Gemini 2.5 Pro)         │
│  • Renders native rich artifacts (tables, charts, markdown workouts)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Bidirectional MCP (stdio)
                                    │ (Tools, Resources, Prompts)
┌───────────────────────────────────▼────────────────────────────────────┐
│                    Athlytics MCP Server                                │
│  ┌────────────────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │   Read & Write Tools   │  │ Living Context │  │ Workflow Prompts │  │
│  │ (Queries, Plans, Goals)│  │   Resources    │  │(Readiness, Review│  │
│  └───────────┬────────────┘  └───────┬────────┘  └────────┬─────────┘  │
└──────────────┼───────────────────────┼────────────────────┼────────────┘
               │                       │                    │
┌──────────────▼───────────────────────▼────────────────────▼────────────┐
│  core/ Library                                                         │
│   ├── storage/     (SQLite: readings, targets, plans, coach notes)     │
│   ├── analytics/   (Baselines, rolling deltas, z-score anomalies)      │
│   └── providers/   (Garmin Connect sync adapter)                       │
└──────────────────────────────────────┬─────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼─────────────────────────────────┐
│  FastAPI + SPA Web Dashboard                                           │
│   • Visualizes AI-created targets with live progress tracking          │
│   • Renders active training plan workout grids and phases              │
│   • Displays health metric trends and anomaly alerts                   │
└────────────────────────────────────────────────────────────────────────┘
```

1. **Zero LLM Maintenance Burden:** Athlytics does not need to store third-party LLM API keys, manage streaming SSE web sockets, implement token rate limiting, or build custom chat UIs.
2. **Access to Frontier Reasoning:** Users interact using Claude 3.7 / Gemini 2.5 directly in their native client environments, taking advantage of large context windows (1M+ tokens on Gemini) and rich UI artifacts.
3. **Bidirectional State Sync:** When the AI coach sets a target or drafts a training plan, it commits structured data directly to SQLite via MCP write tools. The Athlytics web dashboard immediately displays these plans and targets with real-time progress bars.

---

## 3. The 3-Tier Coach Architecture

To provide a cohesive coaching experience, Athlytics implements three MCP primitives:

```
Tier 1: Coaching Playbook & Rules (System instructions / Skills)
Tier 2: Workflow Prompts (/readiness_check, /weekly_review, /build_plan)
Tier 3: Dynamic Resources & Bidirectional Tools (athlytics:// & DB operations)
```

---

### Tier 1: Dynamic MCP Resources (Zero-Turn Context)

MCP Resources provide living state documents that the AI client reads immediately upon opening a thread, avoiding redundant preliminary tool calls:

| Resource URI | Description | Payload Content |
| :--- | :--- | :--- |
| `athlytics://athlete/snapshot` | Current 7-day health snapshot | 7d RHR vs 60d baseline, HRV status/z-score, acute:chronic workload ratio (ACWR), 7d average sleep score. |
| `athlytics://training/current-state` | Active training plan & goals | Active targets, current plan title, current phase (Base/Build/Peak), week number, and scheduled workouts for the current week. |
| `athlytics://coach/context` | Profile & athlete background | Primary sport persona, athlete age/weight, training preferences, injury history, and equipment notes. |

---

### Tier 2: Workflow Prompts (Pre-Packaged Slash Commands)

Athlytics registers pre-configured MCP prompt templates:

1. **`readiness_check`**:
   - *Purpose:* Evaluates morning recovery data against the current day's planned workout.
   - *Logic:* Inspects `athlytics://athlete/snapshot`. If HRV is suppressed (< -1.5 z-score) or Resting HR is elevated (> +1.5 z-score), instructs the coach to recommend active recovery (Zone 1) or rest, overriding high-intensity workouts.
2. **`weekly_review`**:
   - *Purpose:* Evaluates the previous 7 days of training and metric progress.
   - *Logic:* Aggregates volume/load, checks compliance with active targets, flags emerging fatigue or recovery deficits, and recommends adjustments for the upcoming week.
3. **`build_training_plan`**:
   - *Purpose:* Guides the user through setting up a structured, periodized training block.
   - *Logic:* Queries historical VO2 max, race predictions, and recent weekly volume; drafts phases (Base, Build, Peak, Taper); and calls `save_training_plan`.

---

### Tier 3: MCP Toolset (Read & Write)

#### Read Tools

```python
list_metrics() -> list[dict]
"""List all available canonical metric types, date ranges, and units."""

get_metric_series(metric_type: str, start: str, end: str) -> list[dict]
"""Fetch raw daily readings for a metric across a date range."""

get_trend(metric_type: str, window_days: int = 30) -> dict
"""Fetch rolling average, baseline z-score, and week-over-week delta."""

get_anomalies(since_date: str) -> list[dict]
"""Fetch statistical anomaly flags where readings deviate >2 standard deviations."""

get_targets(status: str = "active") -> list[dict]
"""Fetch active or historical athlete targets with current computed progress."""

get_training_plans(status: str = "active") -> list[dict]
"""Fetch current and past structured training plans."""

get_coach_notes(limit: int = 10) -> list[dict]
"""Fetch recent qualitative coach notes, athlete feedback, or injury logs."""
```

#### Action / Write Tools

```python
set_target(
    metric_type: str,
    target_value: float,
    operator: str,       # 'gte', 'lte', 'eq'
    target_window: str,  # 'daily', 'weekly_sum', 'weekly_avg', 'by_date'
    start_date: str,
    end_date: str | None = None,
    notes: str | None = None
) -> dict
"""Set or update an athlete target tracked on the Athlytics dashboard."""

delete_target(target_id: str) -> bool
"""Remove or archive an active target."""

save_training_plan(
    title: str,
    goal_description: str,
    start_date: str,
    target_date: str,
    plan_json: dict      # Structured phases, weekly workouts, and target paces/zones
) -> dict
"""Commit a periodized training plan to SQLite for dashboard visualization."""

update_plan_status(
    plan_id: str,
    status: str          # 'active', 'paused', 'completed', 'archived'
) -> dict
"""Update the status of an existing training plan."""

log_coach_note(
    date: str,
    category: str,       # 'injury', 'nutrition', 'feeling', 'gear', 'milestone'
    note: str,
    tags: list[str] | None = None
) -> dict
"""Log a qualitative observation or coaching recommendation."""
```

---

## 4. Storage Schema Additions (`core/storage/`)

To support targets, structured plans, and coach notes alongside `metric_reading`, extend SQLite storage with three canonical tables:

```sql
-- 1. Athlete Targets & Milestones
CREATE TABLE target (
    id TEXT PRIMARY KEY,
    metric_type TEXT NOT NULL,
    target_value REAL NOT NULL,
    operator TEXT NOT NULL CHECK(operator IN ('gte', 'lte', 'eq')),
    target_window TEXT NOT NULL CHECK(target_window IN ('daily', 'weekly_sum', 'weekly_avg', 'by_date')),
    start_date DATE NOT NULL,
    end_date DATE,
    status TEXT NOT NULL CHECK(status IN ('active', 'completed', 'abandoned')),
    notes TEXT,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_target_status ON target(status);
CREATE INDEX idx_target_metric ON target(metric_type);

-- 2. Structured Training Plans
CREATE TABLE training_plan (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal_description TEXT,
    start_date DATE NOT NULL,
    target_date DATE NOT NULL,
    plan_json TEXT NOT NULL,  -- JSON containing phases, weeks, daily workouts
    status TEXT NOT NULL CHECK(status IN ('active', 'paused', 'completed', 'archived')),
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_plan_status ON training_plan(status);

-- 3. Qualitative Coach Notes & Log
CREATE TABLE coach_note (
    id TEXT PRIMARY KEY,
    date DATE NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('injury', 'nutrition', 'feeling', 'gear', 'milestone', 'general')),
    note TEXT NOT NULL,
    tags_json TEXT,          -- JSON list of tags
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_coach_note_date ON coach_note(date);
```

---

## 5. Coaching Playbook & Guardrails (Prompt Engineering)

Athlytics bundles a standardized **AI Coach Playbook** (provided as `CLAUDE.md`, `.claude/skills/athlytics-coach/SKILL.md`, or Gemini Custom Gem instructions) encoding evidence-based sports science:

### Core Coaching Principles
1. **Recovery-Gated Training:**
   - If 7-day rolling HRV z-score is `< -1.5` or Resting HR is `> +1.5` baseline standard deviations, do not prescribe threshold, VO2 max, or long endurance sessions. Automatically recommend Zone 1 recovery or full rest.
2. **Safe Volume Progression (The 10% Rule):**
   - Never increase weekly running or cycling mileage by more than 10% compared to the previous 3-week rolling average.
3. **Structured Periodization:**
   - Every 3–4 weeks of build, mandate a **deload week** with a 20–30% reduction in total volume to allow physiological adaptation.
4. **Action Persistence:**
   - Whenever an athlete agrees to a new goal or workout plan during conversation, the AI Coach must execute `set_target` or `save_training_plan` so the web dashboard reflects the agreement.

---

## 6. Client Ecosystem Compatibility

### Claude (Claude Desktop / Claude Code)
- **Configuration:** Configured via `claude_desktop_config.json` or project-level `.mcp.json`.
- **Workflow:** Claude invokes `athlytics://` resources on session startup, applies the playbook in `CLAUDE.md`, and renders workout tables via Claude Artifacts.

### Google Gemini (Gemini CLI / Antigravity / Custom Gems)
- **Configuration:** Configured via standard MCP `stdio` bridge.
- **Long-Horizon Analysis:** Gemini's 1M–2M token context window allows ingesting multi-year raw daily metric series for deep retrospective trend analysis across multiple racing seasons.
- **Custom Gem Instructions:** The coaching playbook is pasted directly into the Custom Gem System Instructions on Google AI Studio or `gemini.google.com`.

---

## 7. Plan Sequence & Implementation Roadmap

This design doc extends the overall Athlytics roadmap as follows:

1. **Foundation** (`Plan 1`, merged) — Storage schema, provider protocol, credential encryption, fake provider.
2. **Garmin Provider Adapter** (`Plan 2`, merged) — Real `garminconnect` integration covering all 18 canonical v1 metrics.
3. **Analytics Core** (`Plan 3`, next) — Rolling averages, deltas, z-score anomaly baselines over `core/storage`.
4. **Dashboard & Actionable Storage** (`Plan 4`) — FastAPI + SPA, onboarding persona selection, and UI rendering for targets/plans.
5. **Actionable MCP Server & AI Coach** (`Plan 5`) — Stdio MCP server with read tools, action write tools, dynamic `athlytics://` resources, workflow prompts, and bundled coaching playbooks for Claude and Gemini.
6. **Deployment** (`Plan 6`) — Docker Compose packaging and entrypoints.
