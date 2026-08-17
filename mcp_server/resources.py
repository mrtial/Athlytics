"""Dynamic context resource generators for Athlytics AI Coach."""
import json
from datetime import date, timedelta
from pathlib import Path
from core.storage import repository
from core.analytics import get_trend

_COACH_PLAYBOOK_PATH = Path(__file__).resolve().parent.parent / "skills" / "athlytics-coach" / "SKILL.md"


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


def build_coach_playbook() -> str:
    """Serves the canonical coaching playbook (recovery gating, 10% rule, deload weeks,
    action persistence), sourced directly from skills/athlytics-coach/SKILL.md so every
    MCP client sees the same rules the file-based skill defines."""
    text = _COACH_PLAYBOOK_PATH.read_text()
    _, _, body = text.partition("---\n")
    _, _, body = body.partition("---\n")
    return body.strip() or text.strip()


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
