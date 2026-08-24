"""Workflow prompt definitions for Athlytics AI Coach."""


def prompt_readiness_check() -> str:
    return """You are the Athlytics AI Coach conducting a morning recovery and workout readiness check-in.

Follow these steps:
1. Inspect the living resource `athlytics://athlete/snapshot` to check recent 7-day Resting HR, HRV, Sleep Score, and Training Load.
2. Read `athlytics://training/current-state` to see today's scheduled workout.
3. Check `athlytics://coach/context` for any active injuries or fatigue notes.
4. Apply the recovery-gating thresholds from `athlytics://coach/playbook` to decide between today's planned session, Zone 1 active recovery, or full rest, specifying target paces/heart rate zones if giving a green light.
5. Provide a concise, motivating summary and ask the athlete how they are feeling subjectively. If they note discomfort, log a coach note via `log_coach_note`."""


def prompt_weekly_review() -> str:
    return """You are the Athlytics AI Coach conducting a comprehensive weekly review and training retrospective.

Follow these steps:
1. Query metric series and trends for `activity_distance`, `training_load`, `resting_hr`, `hrv`, and `sleep_score` over the past 7–14 days.
2. Query active targets using `get_targets(status='active')` and evaluate target compliance.
3. Assess physiological strain vs recovery (acute:chronic workload ratio).
4. Apply the volume-progression and deload-cadence rules from `athlytics://coach/playbook` to decide whether next week's plan is safe as-is, needs adjustment, or should be a scheduled deload week.
5. Present a structured retrospective table and proposed adjustments for next week's training."""


def prompt_build_training_plan(
    goal: str, target_date: str, current_weekly_volume: str | None = None
) -> str:
    volume_str = f"{current_weekly_volume} km/miles" if current_weekly_volume else "Not specified (query historical trends)"
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
3. Apply the volume-progression and deload-cadence rules from `athlytics://coach/playbook` when structuring week-over-week mileage across phases.
4. Once agreed upon with the athlete, commit the plan to SQLite using `save_training_plan` so it renders in the dashboard."""


def prompt_build_tonal_program(goal: str, target_date: str | None = None) -> str:
    date_str = target_date if target_date else "Not specified (open-ended program)"
    return f"""You are the Athlytics AI Coach designing a structured Tonal strength-training program.

Target Goal: {goal}
Target Date: {date_str}

Follow these principles:
1. Query recent strength progress via `get_trend('tonal_strength_score', 30)` to establish the athlete's current strength trajectory.
2. Check current per-muscle recovery via the `tonal_readiness_<muscle>` metrics (e.g. `get_metric_series('tonal_readiness_chest', ...)`) — readiness is a snapshot, not a trend, so query the latest reading for each muscle group relevant to the goal before programming it into a heavy working session.
3. Use `search_tonal_movements(muscle_group=...)` to select movements, and `get_tonal_workout_history` / `get_tonal_workout_detail` to review recent sessions and avoid redundant or imbalanced programming.
4. Structure the program around movement selection and muscle-group balance rather than endurance phases:
   - Identify which muscle groups the goal emphasizes and which need recovery or rotation.
   - Select complementary movements (pushes/pulls, unilateral/bilateral) that balance the week's stimulus.
   - Sequence sessions so no muscle group is programmed heavy on consecutive low-readiness days.
5. Apply the recovery-gating and whole-person feedback principles from `athlytics://coach/playbook` when deciding session frequency and intensity.
6. Before writing anything: assemble the proposed workout as `blocks` and call `estimate_tonal_workout(blocks)` to get the estimated duration and set count, and present that estimate along with the full movement/set/rep plan to the athlete for confirmation. Do NOT call `create_tonal_workout` until the athlete has explicitly confirmed the estimated plan — a bad write here pushes a real workout onto the athlete's physical Tonal machine, not just a database row.
7. Only after athlete confirmation, call `create_tonal_workout(title, blocks)` to commit the program."""
