---
name: tonal-weekly-checkin
description: Weekly strength-training review for Tonal-connected athletes — strength-score trend, readiness pattern, plan compliance, and movement-level progressive overload.
---

# Tonal Weekly Check-In

A weekly review flow for athletes training on Tonal: are they progressing, are they compliant with what was planned, and is any muscle group being trained through fatigue rather than around it. Structurally parallel to a marathon weekly check-in (mileage trend, recovery pattern, plan compliance), scoped to strength training instead of endurance volume.

Use this when the athlete asks for a training check-in, weekly review, progress update, or "am I on track" and Tonal is their connected strength modality — not for a one-off question about a single lift or metric, which doesn't need the full review below.

## Review Steps

### 1. Strength-Score Trend
- Query `get_trend('tonal_strength_score', 30)` (or `get_metric_series` over the last 7–14 days for finer granularity) to see whether the athlete's overall Tonal strength score is climbing, flat, or declining.
- A flat or declining trend across several weeks of consistent training is worth flagging on its own — don't wait for the athlete to ask.

### 2. Readiness Pattern Over the Week
- Readiness (`tonal_readiness_<muscle>`) is a snapshot metric, not backfillable history — there's no way to pull "readiness as it was each day this week" retroactively. Instead, pull the **current** reading for each muscle group the athlete trained this week (`get_metric_series('tonal_readiness_<muscle>', ...)` returns the latest snapshot) and cross-reference it against which muscle groups were actually trained this week via `get_muscle_group_volume(start_date, end_date)` — it aggregates volume by muscle group directly from locally hydrated data, no need to infer muscle groups per movement by hand.
- Apply the same bands as `tonal-coach`: **< 40 fatigued**, **40–70 moderate**, **≥ 70 ready**. If a muscle group currently reads fatigued and was also trained heavy multiple times this week, call that out as a likely driver — not necessarily a problem on its own, but something the athlete should know before the next session hits that muscle group again.

### 3. Workout Compliance vs. Saved Plan
- Query `get_training_plans(status='active')` for the athlete's current plan, if one exists.
- Query `get_tonal_workout_history(limit=...)` for the past week's actual sessions.
- **If the plan prescribes specific sessions** (its `plan_json` has a `sessions` key, e.g. "Upper A"/"Upper B"/"Legs & Core" with named movements), compare actual sessions against what was prescribed: session count, which muscle groups/movements were supposed to be hit, and whether any planned session was skipped or substituted.
- **If the plan is goal-only** (`plan_json` has no `sessions` key — the athlete tracks a target like strength score but follows Tonal's own guided program rather than a prescribed split), there's nothing to judge compliance against. Skip the compliance comparison and go to step 4 instead — log what happened, don't score it against a plan that was never meant to prescribe a specific week.
- If there's no active saved plan at all, say so plainly rather than inventing an implied one.

### 4. Log Actual Workouts (goal-only plans only)
Skip this step entirely for a plan with prescribed `sessions` — step 3's compliance comparison already captures what happened for those. For a goal-only plan, this is what makes the week's real workouts show up on the dashboard's "Actual Workouts" table.

- Filter this week's entries from `get_tonal_workout_history(limit=...)` to the Monday-starting week being reviewed.
- For each workout in that range, call `get_tonal_workout_detail(activity_id)` to get `total_duration_seconds`, `total_volume_lbs`, and the per-set `movement_id`s. Resolve movement names by cross-referencing the muscle-group searches you already ran in step 2 (`search_tonal_movements(muscle_group=...)` for each muscle group trained that week) — build an id→name map from those results rather than issuing fresh lookups per movement. If a movement's muscle group wasn't covered in step 2 (e.g. it trained something outside what readiness flagged), one more `search_tonal_movements(muscle_group=...)` call covers it. If a movement genuinely can't be resolved, it's fine to log the workout without a full name list rather than blocking on it.
- Use `completed_sets` from the workout-history entry (not the raw per-set row count from the detail call, which includes warm-ups and any aborted 0-rep sets) for the `sets` field.
- Take the most recent `tonal_strength_score` reading in the week's range (`get_metric_series`) as that week's score, or omit the field if none landed that week.
- Build one `actual_log` entry:
  ```json
  {"week_start": "2026-09-07", "workouts": [{"date": "2026-09-07", "activity_id": "...", "title": "Linear Workout", "duration_min": 33, "volume_lbs": 6856, "sets": 19, "movements": ["Bench Press", "Bench Chest Fly", "..."]}], "strength_score": 708, "note": "All-push day, no back/biceps work this week."}
  ```
  `title` comes straight from `get_tonal_workout_history` (Tonal has no real per-workout title — see the tonal-provider skill — so this will usually just read "Linear Workout" or "PT Workout"). `note` is one short observation worth surfacing at a glance (a muscle-balance gap, an unusually short/long session, multiple sessions in one week) — skip it if nothing stands out.
- Find the plan's existing `actual_log` array (create it if absent) and **overwrite** any entry whose `week_start` matches this week rather than appending a duplicate — same idempotent-per-week convention as the marathon plan's `weekly_schedule[].actual`. Re-serialize the full `plan_json` and call `save_training_plan` with the existing `plan_id`.

### 5. Movement-Level Progressive Overload Check
- Pick the 2–4 movements most central to the athlete's current goal (or the ones they ask about).
- For each, call `get_movement_history(query, limit=...)` — it returns that movement's chronological per-set history (reps, weight, one-rep-max, volume) straight from locally hydrated data, no per-workout detail fetching required.
- Compare `one_rep_max` (and secondarily `weight_lbs`/`reps`/`volume_lbs`) across sessions in chronological order. Progressive overload is `one_rep_max` trending up, or held steady with rep/volume increasing — not just "did a workout happen." A movement that's been flat or regressing for multiple sessions despite adequate readiness is worth surfacing as a candidate for a program change (different rep range, more recovery, or movement substitution), not just noted and passed over.

### 6. Report
- Present strength-score trend, readiness snapshot for trained muscle groups, plan compliance (or the actual-workouts log, for a goal-only plan), and per-movement progressive-overload status as a structured summary.
- Apply `athlytics://coach/playbook`'s recovery-gating and whole-person feedback principles when deciding whether to recommend adjusting next week's plan — a good strength-score trend doesn't override a muscle group reading fatigued going into a heavy session, and a compliance miss driven by low readiness is a different finding than one driven by skipped sessions with no readiness excuse.
- If the review surfaces a plan change worth making, propose it and, once the athlete agrees, persist it the same way `tonal-coach` does: `estimate_tonal_workout` before any `create_tonal_workout`, and `update_plan_status`/`save_training_plan` for the plan record itself.
