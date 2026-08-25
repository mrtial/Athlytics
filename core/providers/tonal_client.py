"""Low-level Tonal API client: auth, token management, HTTP plumbing.

No official Tonal API exists. Endpoints and auth flow here are reverse-
engineered, cross-verified against two independent open-source efforts
(dlwiest/ts-tonal-client, danmarai/tonal-api) -- see
docs/superpowers/specs/2026-08-24-tonal-integration-design.md for the
full provenance and confidence notes.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Callable

import httpx

from core.providers.base import RateLimitError

AUTH0_BASE_URL = "https://tonal.auth0.com"
AUTH0_CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE_URL = "https://api.tonal.com/v6"
TOKEN_EXPIRY_BUFFER_SECONDS = 60

# The 11 muscle groups Tonal's muscle-readiness endpoint may report.
MUSCLE_READINESS_MUSCLES = [
    "Chest", "Shoulders", "Back", "Triceps", "Biceps", "Abs", "Obliques",
    "Quads", "Glutes", "Hamstrings", "Calves",
]

MOVEMENTS_CACHE_TTL_SECONDS = 24 * 60 * 60


class TonalAuthError(Exception):
    """Raised when Tonal authentication fails: no credentials configured,
    invalid password, or both refresh-token and password relogin fail.
    Not retryable with backoff, unlike RateLimitError."""


def _decode_jwt_exp(id_token: str, fallback_seconds: int = 86400) -> int:
    """Decode the `exp` claim from a JWT's payload segment. Falls back to
    now + fallback_seconds if the token can't be parsed -- both upstream
    projects do this rather than failing hard, since a slightly-wrong
    expiry just means one extra refresh call, not a correctness bug."""
    try:
        payload_b64 = id_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        if "exp" in payload:
            return int(payload["exp"])
    except Exception:
        pass
    return int(time.time()) + fallback_seconds


def _password_login(email: str, password: str, auth_http: httpx.Client) -> dict[str, str]:
    response = auth_http.post(
        "/oauth/token",
        json={
            "grant_type": "password",
            "username": email,
            "password": password,
            "client_id": AUTH0_CLIENT_ID,
            "scope": "openid profile email offline_access",
        },
    )
    if response.status_code != 200:
        raise TonalAuthError(f"Tonal login failed: {response.status_code} {response.text}")
    body = response.json()
    id_token = body["id_token"]
    return {
        "id_token": id_token,
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": str(_decode_jwt_exp(id_token)),
    }


def _refresh_login(refresh_token: str, auth_http: httpx.Client) -> dict[str, str] | None:
    response = auth_http.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "client_id": AUTH0_CLIENT_ID, "refresh_token": refresh_token},
    )
    if response.status_code != 200:
        return None
    body = response.json()
    id_token = body["id_token"]
    return {
        "id_token": id_token,
        "refresh_token": body.get("refresh_token", refresh_token),
        "expires_at": str(_decode_jwt_exp(id_token)),
    }


def expand_blocks(blocks: list[dict], movement_map: dict[str, dict]) -> list[dict]:
    """Expand block/superset-shaped workout input into a flat list of
    per-set objects ready to submit to Tonal's `/user-workouts*` endpoints.

    Pure function -- no I/O, fully unit-testable without mocking HTTP.
    Direct port of `tonal_tool.py`'s `_expand_blocks` (design doc §5).

    `movement_map` keys are movement ids; values must carry the raw Tonal
    field names `countReps`/`isAlternating` (as returned by `/movements`,
    not `get_movements()`'s translated `count_reps`/`is_alternating`).
    """
    invalid_ids = sorted({
        exercise["movement_id"]
        for block in blocks
        for exercise in block["exercises"]
        if exercise["movement_id"] not in movement_map
    })
    if invalid_ids:
        raise ValueError(f"unknown Tonal movement id(s): {', '.join(invalid_ids)}")

    sets: list[dict] = []
    for block_number, block in enumerate(blocks, start=1):
        exercises = block["exercises"]
        if not exercises:
            continue
        max_sets = max(exercise.get("sets", 3) for exercise in exercises)
        for round_num in range(1, max_sets + 1):
            for ex_idx, exercise in enumerate(exercises):
                exercise_sets = exercise.get("sets", 3)
                if round_num > exercise_sets:
                    continue

                movement_id = exercise["movement_id"]
                movement = movement_map[movement_id]
                set_obj = {
                    "movementId": movement_id,
                    "blockStart": round_num == 1 and ex_idx == 0,
                    "blockNumber": block_number,
                    "setGroup": ex_idx + 1,
                    "round": round_num,
                    "repetition": round_num,
                    "repetitionTotal": exercise_sets,
                    "burnout": exercise.get("burnout", False),
                    "spotter": exercise.get("spotter", False),
                    "eccentric": exercise.get("eccentric", False),
                    "chains": exercise.get("chains", False),
                    "flex": False,
                    "warmUp": exercise.get("warmUp", False),
                    "dropSet": exercise.get("dropSet", False),
                    "weightPercentage": exercise.get("weight_percentage", 100),
                    "description": "",
                }
                if movement.get("countReps", True):
                    reps = exercise.get("reps", 10)
                    if movement.get("isAlternating"):
                        reps *= 2
                    set_obj["prescribedReps"] = reps
                else:
                    set_obj["prescribedDuration"] = exercise.get("duration", 30)
                    set_obj["prescribedResistanceLevel"] = 5
                sets.append(set_obj)
    return sets


def _parse_workout_set_activity(raw_sets: list[dict]) -> list[dict]:
    """Normalize Tonal's raw per-set wire format (`workoutSetActivity`, as
    returned both by GET /workout-activities/{id} and embedded in each
    entry of GET /workout-activities) into the simplified shape used
    throughout this codebase. Pure function, no I/O -- shared by
    TonalClient.get_workout_detail (single workout) and
    TonalProvider.hydrate_recent_strength_sets (bulk, sync-time) so the two
    write paths can't drift apart."""
    sets = []
    for set_activity in raw_sets:
        reps = set_activity.get("repCount")
        if reps is None:
            reps = set_activity.get("prescribedReps")
        weight_lbs = set_activity.get("baseWeight")
        if weight_lbs is None:
            weight_lbs = set_activity.get("avgWeight")
        volume_lbs = set_activity.get("volume")
        if volume_lbs is None:
            volume_lbs = set_activity.get("totalVolume")
        sets.append({
            "movement_id": set_activity.get("movementId"),
            "is_warm_up": set_activity.get("warmUp"),
            "reps": reps,
            "weight_lbs": weight_lbs,
            "volume_lbs": volume_lbs,
            "one_rep_max": set_activity.get("oneRepMax"),
            "max_power_watts": set_activity.get("maxConPower"),
            "rom_inches": set_activity.get("rom"),
            "struggling_score": set_activity.get("strugglingScore"),
            "side": set_activity.get("movementSide"),
            "begin_time": set_activity.get("beginTime"),
        })
    return sets


class TonalClient:
    def __init__(
        self,
        credential_store,
        http_client: httpx.Client | None = None,
        auth_http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], float] = time.time,
    ):
        credentials = credential_store.load()
        if credentials is None:
            raise TonalAuthError("no Tonal credentials configured; connect a Tonal account first")

        self._credential_store = credential_store
        self._http = http_client or httpx.Client(base_url=API_BASE_URL, timeout=30.0)
        self._auth_http = auth_http_client or httpx.Client(base_url=AUTH0_BASE_URL, timeout=30.0)
        self._sleep_fn = sleep_fn
        self._now_fn = now_fn
        self._email = credentials["email"]
        self._password = credentials["password"]

        if "id_token" not in credentials or self._now_fn() >= int(credentials.get("expires_at", 0)) - TOKEN_EXPIRY_BUFFER_SECONDS:
            credentials = self._reauth(credentials)

        self._id_token = credentials["id_token"]
        self._user_id: str | None = credentials.get("user_id") or None
        self._movements_cache: list[dict] | None = None
        self._movements_cached_at: float = 0.0

    def _reauth(self, credentials: dict[str, str]) -> dict[str, str]:
        """Try refresh first (cheap, doesn't re-send the password); fall
        back to full password login if refresh fails (revoked/expired
        refresh token)."""
        refreshed = None
        if credentials.get("refresh_token"):
            refreshed = _refresh_login(credentials["refresh_token"], self._auth_http)
        tokens = refreshed or _password_login(self._email, self._password, self._auth_http)

        merged = {**credentials, **tokens, "email": self._email, "password": self._password}
        self._credential_store.save(merged)
        return merged

    def _ensure_fresh_token(self) -> None:
        credentials = self._credential_store.load() or {}
        expires_at = int(credentials.get("expires_at", 0))
        if self._now_fn() >= expires_at - TOKEN_EXPIRY_BUFFER_SECONDS:
            credentials = self._reauth(credentials)
            self._id_token = credentials["id_token"]

    @property
    def user_id(self) -> str:
        if self._user_id is None:
            data = self._get("/users/userinfo")
            self._user_id = data["id"]
            credentials = self._credential_store.load() or {}
            credentials["user_id"] = self._user_id
            self._credential_store.save(credentials)
        return self._user_id

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._id_token}"}

    def _get_response(self, path: str, params: dict | None = None, extra_headers: dict | None = None) -> httpx.Response:
        """Like `_get`, but returns the raw `httpx.Response` instead of the
        parsed body -- needed by callers (`get_activities`) that must read
        response headers, not just the JSON payload."""
        self._ensure_fresh_token()
        headers = {**self._headers(), **(extra_headers or {})}
        response = self._http.get(path, params=params, headers=headers)
        if response.status_code == 401:
            credentials = self._reauth(self._credential_store.load() or {})
            self._id_token = credentials["id_token"]
            headers = {**self._headers(), **(extra_headers or {})}
            response = self._http.get(path, params=params, headers=headers)
        if response.status_code == 429:
            raise RateLimitError(f"Tonal rate limit exceeded: {response.text}")
        response.raise_for_status()
        return response

    def _get(self, path: str, params: dict | None = None) -> dict:
        response = self._get_response(path, params=params)
        return {} if response.status_code == 204 else response.json()

    def _post(self, path: str, json_body: dict) -> dict:
        self._ensure_fresh_token()
        response = self._http.post(path, json=json_body, headers=self._headers())
        if response.status_code == 401:
            credentials = self._reauth(self._credential_store.load() or {})
            self._id_token = credentials["id_token"]
            response = self._http.post(path, json=json_body, headers=self._headers())
        if response.status_code == 429:
            raise RateLimitError(f"Tonal rate limit exceeded: {response.text}")
        response.raise_for_status()
        return {} if response.status_code == 204 else response.json()

    def _delete(self, path: str) -> dict:
        self._ensure_fresh_token()
        response = self._http.delete(path, headers=self._headers())
        if response.status_code == 401:
            credentials = self._reauth(self._credential_store.load() or {})
            self._id_token = credentials["id_token"]
            response = self._http.delete(path, headers=self._headers())
        if response.status_code == 429:
            raise RateLimitError(f"Tonal rate limit exceeded: {response.text}")
        response.raise_for_status()
        return {} if response.status_code == 204 else response.json()

    def get_muscle_readiness(self) -> dict[str, float]:
        """Current per-muscle readiness scores. Muscles absent from the raw
        response are omitted (not defaulted to 0) so callers can distinguish
        "no data" from "score is 0"."""
        raw = self._get(f"/users/{self.user_id}/muscle-readiness/current")
        return {muscle: float(raw[muscle]) for muscle in MUSCLE_READINESS_MUSCLES if muscle in raw}

    def get_strength_score_current(self) -> list[dict]:
        raw = self._get(f"/users/{self.user_id}/strength-scores/current")
        return [
            {
                "region": entry.get("bodyRegionDisplay") or entry.get("strengthBodyRegion"),
                "score": entry["score"],
            }
            for entry in raw
        ]

    def get_strength_score_history(self, limit: int = 20) -> list[dict]:
        raw = self._get(f"/users/{self.user_id}/strength-scores/history", params={"limit": limit})
        return [
            {
                "date": entry["activityTime"][:10],
                "overall": entry["overall"],
                "upper": entry["upper"],
                "lower": entry["lower"],
                "core": entry["core"],
            }
            for entry in raw
        ]

    def get_movements(self, force_refresh: bool = False) -> list[dict]:
        """All Tonal movements, cached in-memory on this instance with a 24h
        TTL (mirrors the reverse-engineered reference clients' cache duration,
        but per-instance rather than a disk file, matching StravaProvider's
        per-instance activities cache convention)."""
        cache_is_fresh = (
            self._movements_cache is not None
            and self._now_fn() - self._movements_cached_at < MOVEMENTS_CACHE_TTL_SECONDS
        )
        if not force_refresh and cache_is_fresh:
            return self._movements_cache

        raw = self._get("/movements")
        self._movements_cache = [
            {
                "id": entry["id"],
                "name": entry["name"],
                "muscle_groups": entry.get("muscleGroups", []),
                "body_region": entry.get("bodyRegion"),
                "count_reps": entry.get("countReps"),
                "is_alternating": entry.get("isAlternating"),
            }
            for entry in raw
        ]
        self._movements_cached_at = self._now_fn()
        return self._movements_cache

    def search_movements(self, query: str | None = None, muscle_group: str | None = None) -> list[dict]:
        movements = self.get_movements()
        query_lower = query.lower() if query else None

        results = []
        for movement in movements:
            if muscle_group is not None and muscle_group not in movement["muscle_groups"]:
                continue
            if query_lower is not None:
                haystack = " ".join([
                    movement["name"],
                    " ".join(movement["muscle_groups"]),
                    movement["body_region"] or "",
                ]).lower()
                if query_lower not in haystack:
                    continue
            results.append(movement)
        return results

    def _fetch_recent_page(self, limit: int) -> list[dict]:
        """Shared pagination logic: probe pg-total with a 1-item request,
        then fetch the last `limit` items (offset = max(0, total - limit)),
        returning the *untrimmed* raw entries sorted descending by
        beginTime. The server ignores the `limit` *query* param entirely
        and defaults to the oldest page (offset=0) unless pg-offset/
        pg-limit *request headers* are sent -- confirmed live against a
        real 240-workout account."""
        path = f"/users/{self.user_id}/workout-activities"
        probe = self._get_response(path, extra_headers={"pg-offset": "0", "pg-limit": "1"})
        total = int(probe.headers.get("pg-total", 0))
        offset = max(0, total - limit)
        response = self._get_response(path, extra_headers={"pg-offset": str(offset), "pg-limit": str(limit)})
        raw = response.json()
        return sorted(raw, key=lambda entry: entry["beginTime"], reverse=True)

    def get_activities(self, limit: int = 10) -> list[dict]:
        """Workout history, most-recent-first. Each entry also carries
        planned_sets/completed_sets/completion_rate, derived from the same
        raw entry's totalSets and workoutSetActivity -- no extra API call.

        Completion-rate heuristic: a set counts as "completed" if it has
        repCount > 0 or duration > 0. This is an approximation -- some
        legitimately-performed sets (e.g. certain bodyweight-style
        movements) report zero tracked reps, so completion_rate can slightly
        undercount on workouts using them. Good enough to distinguish an
        abandoned/cut-short session (rate near 0) from a completed one
        (rate near 1); not precise enough to treat as an exact percentage.
        """
        raw_sorted = self._fetch_recent_page(limit)
        activities = []
        for entry in raw_sorted[:limit]:
            workout_type = entry.get("workoutType")
            title = f"{workout_type} Workout" if workout_type else "Workout"
            planned_sets = entry.get("totalSets")
            raw_sets = entry.get("workoutSetActivity", [])
            completed_sets = sum(
                1 for s in raw_sets if (s.get("repCount") or 0) > 0 or (s.get("duration") or 0) > 0
            )
            completion_rate = (completed_sets / planned_sets) if planned_sets else None
            activities.append({
                "activity_id": entry["id"],
                "date": entry["beginTime"],
                "title": title,
                "type": workout_type,
                "duration_seconds": entry.get("totalDuration"),
                "total_volume_lbs": entry.get("totalVolume"),
                "planned_sets": planned_sets,
                "completed_sets": completed_sets,
                "completion_rate": completion_rate,
            })
        return activities

    def get_recent_workout_set_activity(self, limit: int = 500) -> list[dict]:
        """Untrimmed raw workout entries (including the full
        workoutSetActivity per-set array) for the most recent `limit`
        workouts -- used only by TonalProvider.hydrate_recent_strength_sets,
        not called by any MCP tool directly. get_activities() trims this
        same data down to its small public dict shape; this method exists
        so the hydration path can get at what get_activities() discards,
        without a second network call pattern."""
        return self._fetch_recent_page(limit)

    def get_workout_detail(self, activity_id: str) -> dict:
        raw = self._get(f"/users/{self.user_id}/workout-activities/{activity_id}")
        return {
            "total_duration_seconds": raw.get("totalDuration"),
            "total_volume_lbs": raw.get("totalVolume"),
            "sets": _parse_workout_set_activity(raw.get("workoutSetActivity", [])),
        }

    def _get_movement_map(self) -> dict[str, dict]:
        """Movement id -> {"countReps", "isAlternating"} for `expand_blocks`,
        rebuilt from `get_movements()` (which itself may hit the cache)."""
        return {
            movement["id"]: {
                "countReps": movement["count_reps"],
                "isAlternating": movement["is_alternating"],
            }
            for movement in self.get_movements()
        }

    def estimate_workout(self, blocks: list[dict]) -> dict:
        """No side effects on Tonal's end -- safe to call freely. Validates
        every `movement_id` in `blocks` against `get_movements()` (via
        `expand_blocks`, which raises `ValueError` naming any unknown ids)
        before posting."""
        sets = expand_blocks(blocks, self._get_movement_map())
        result = self._post("/user-workouts/estimate", {"sets": sets})
        return {
            "estimated_duration_min": round(result["duration"] / 60),
            "set_count": len(sets),
        }

    def create_workout(self, title: str, blocks: list[dict]) -> dict:
        """Pushes a new workout onto the athlete's Tonal machine -- callers
        should call `estimate_workout` first and confirm with the athlete
        (design doc §5's safety convention)."""
        sets = expand_blocks(blocks, self._get_movement_map())
        exercise_count = sum(len(block["exercises"]) for block in blocks)
        result = self._post("/user-workouts", {"title": title, "sets": sets})
        return {
            "workout_id": result["id"],
            "title": title,
            "set_count": len(sets),
            "exercise_count": exercise_count,
        }

    def delete_workout(self, workout_id: str) -> bool:
        self._delete(f"/user-workouts/{workout_id}")
        return True
