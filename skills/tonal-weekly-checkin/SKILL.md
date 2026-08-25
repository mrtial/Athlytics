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
- Query `get_tonal_workout_history(limit=...)` for the past week's actual sessions and compare against what the plan called for: session count, which muscle groups/movements were supposed to be hit, and whether any planned session was skipped or substituted.
- If there's no active saved plan, say so plainly rather than inventing an implied one — compliance review only makes sense against something that was actually saved via `save_training_plan`.

### 4. Movement-Level Progressive Overload Check
- Pick the 2–4 movements most central to the athlete's current goal (or the ones they ask about).
- For each, call `get_movement_history(query, limit=...)` — it returns that movement's chronological per-set history (reps, weight, one-rep-max, volume) straight from locally hydrated data, no per-workout detail fetching required.
- Compare `one_rep_max` (and secondarily `weight_lbs`/`reps`/`volume_lbs`) across sessions in chronological order. Progressive overload is `one_rep_max` trending up, or held steady with rep/volume increasing — not just "did a workout happen." A movement that's been flat or regressing for multiple sessions despite adequate readiness is worth surfacing as a candidate for a program change (different rep range, more recovery, or movement substitution), not just noted and passed over.

### 5. Report
- Present strength-score trend, readiness snapshot for trained muscle groups, plan compliance, and per-movement progressive-overload status as a structured summary.
- Apply `athlytics://coach/playbook`'s recovery-gating and whole-person feedback principles when deciding whether to recommend adjusting next week's plan — a good strength-score trend doesn't override a muscle group reading fatigued going into a heavy session, and a compliance miss driven by low readiness is a different finding than one driven by skipped sessions with no readiness excuse.
- If the review surfaces a plan change worth making, propose it and, once the athlete agrees, persist it the same way `tonal-coach` does: `estimate_tonal_workout` before any `create_tonal_workout`, and `update_plan_status`/`save_training_plan` for the plan record itself.
