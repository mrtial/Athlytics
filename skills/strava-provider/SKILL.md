---
name: strava-provider
description: How to maintain, debug, and use Athlytics's Strava data source integration (StravaProvider, OAuth connect flow, and the sync_strava_data MCP tool).
---

# Strava Provider Skill

This skill covers Athlytics's Strava integration: `core/providers/strava.py` (`StravaProvider`), the OAuth connect flow (`app/routes/strava_oauth.py`), and the `sync_strava_data` MCP tool (`mcp_server/server.py`).

## How it works

- Strava credentials (`client_id`, `client_secret`, `access_token`, `refresh_token`, `expires_at`) live in their own encrypted `CredentialStore`, separate from Garmin's (`/data/strava_credentials.enc` vs. `/data/garmin_credentials.enc`) — never share one `CredentialStore` instance between the two.
- `StravaProvider` refreshes its access token once, at construction, if expired. It does not re-check mid-sync — a single sync pass is expected to complete well within a token's ~6-hour lifetime.
- `StravaProvider.fetch_activities(start, end)` is cached per-instance by `(start, end)` to avoid redundant `/athlete/activities` calls across the three activity-derived metric_types (`activity_duration`, `activity_distance`, `activity_calories`) — this matters because Strava's rate limit (100 requests/15 min for reads) is much tighter than Garmin's.
- Because Garmin devices commonly auto-forward activities to Strava, `core/storage/repository.upsert_activities` deduplicates across sources by `(activity_type, start_time proximity)`, always keeping the Garmin-sourced record when both exist (`ACTIVITY_SOURCE_PRIORITY = ["garmin", "strava"]`). If you're debugging "why didn't my Strava activity show up," check whether a matching Garmin activity already exists in that time window first — that's expected, not a bug.

## Debugging a failed sync

1. Check `GET /api/sync-status` — a `"strava"` key appears when Strava is connected, with its own `auth_error`/`metrics` (source-keyed independently from Garmin's, per `app/db.py`'s `sync_run_status`/`sync_metric_status`).
2. A `StravaAuthError` there means the refresh token was revoked (user disconnected the app on Strava's side, or their app registration changed) — the user needs to reconnect via onboarding's Connect step or Settings.
3. A `RateLimitError` surfaces as the metric_type's status going to `"failed"` after retries are exhausted (`core/scheduler/sync.py:_fetch_with_backoff`) — this should be rare given the proactive backoff in `StravaProvider._call`, but can still happen on a very large first backfill.

## Using the MCP tool

`sync_strava_data(days: int = 30, force_full_history: bool = False)` triggers an on-demand sync, identical in shape to `sync_garmin_data`. Use `force_full_history=True` only for a deliberate one-off resync (ignores checkpoints, can issue many API calls) — never on routine syncs.
