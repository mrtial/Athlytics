"""Athlytics-facing Tonal provider.

Implements the `Provider` protocol (`core/providers/base.py`) for the subset
of Tonal data that's genuinely time-series-shaped, plus additional methods
(movements, workout history/detail, workout write) that fall outside that
protocol -- called directly by MCP tools. See
docs/superpowers/specs/2026-08-24-tonal-integration-design.md §4/§5 for the
full design, including why readiness is snapshot-only while strength score
and workout metrics can backfill within a date range.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Callable

from core.providers.tonal_client import MUSCLE_READINESS_MUSCLES, TonalClient
from core.security.credentials import CredentialStore
from core.storage import repository
from core.storage.models import Activity, MetricReading, StrengthSet

# 11 readiness metrics (one per muscle group) + strength score + the two
# workout-derived metrics = 14 total, matching design doc §4.
TONAL_METRIC_TYPES: list[str] = [
    f"tonal_readiness_{muscle.lower()}" for muscle in MUSCLE_READINESS_MUSCLES
] + [
    "tonal_strength_score",
    "tonal_workout_volume",
    "tonal_workout_duration",
]

_READINESS_METRIC_TO_MUSCLE: dict[str, str] = {
    f"tonal_readiness_{muscle.lower()}": muscle for muscle in MUSCLE_READINESS_MUSCLES
}


def _parse_tonal_timestamp(value: str) -> datetime:
    """Tonal's activity `date`/`beginTime` is ISO 8601, UTC (possibly with a
    'Z' suffix). Convert to naive UTC per MetricReading/Activity's contract."""
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


class TonalProvider:
    name = "tonal"

    # TonalClient.get_activities()/get_strength_score_history() only accept a
    # `limit`, not a date range -- fetch generously, then filter client-side
    # to the requested [start, end] window.
    ACTIVITIES_FETCH_LIMIT = 500
    STRENGTH_SCORE_HISTORY_LIMIT = 500

    def __init__(
        self,
        credential_store: CredentialStore,
        tonal_client_factory: Callable[..., TonalClient] = TonalClient,
    ):
        self._client = tonal_client_factory(credential_store)
        # Per-instance cache, mirroring StravaProvider's `_activities_cache`
        # convention -- but keyed on `limit` rather than a (start, end) date
        # tuple, since TonalClient.get_activities() has no date-range
        # parameter of its own (get_activities(limit) fetches the server's
        # *entire* nested-set-data history regardless of range, and every
        # date filtering happens client-side in _raw_activities_in_range).
        # Without this, `fetch()` re-downloads that whole history from
        # scratch on every single (metric_type x date-chunk) call --
        # `sync_all_metrics` calls fetch() once per chunk for each of
        # tonal_workout_volume/tonal_workout_duration, so a multi-chunk
        # backfill would otherwise re-fetch the same 500-workout response
        # many times over. In practice ACTIVITIES_FETCH_LIMIT is the only
        # value ever passed, so this cache holds at most one entry per
        # instance's lifetime.
        self._raw_activities_cache: dict[int, list[dict]] = {}

    def supported_metric_types(self) -> list[str]:
        return list(TONAL_METRIC_TYPES)

    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        if metric_type not in TONAL_METRIC_TYPES:
            raise ValueError(f"unsupported metric_type for TonalProvider: {metric_type!r}")

        if metric_type in _READINESS_METRIC_TO_MUSCLE:
            return self._fetch_readiness(metric_type)
        if metric_type == "tonal_strength_score":
            return self._fetch_strength_score(start, end)
        return self._fetch_workout_metric(metric_type, start, end)

    def _fetch_readiness(self, metric_type: str) -> list[MetricReading]:
        """Current-snapshot only: emits a single reading timestamped "now"
        regardless of the requested date range -- the endpoint has no
        historical range support (design doc §4)."""
        muscle = _READINESS_METRIC_TO_MUSCLE[metric_type]
        readiness = self._client.get_muscle_readiness()
        if muscle not in readiness:
            return []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return [MetricReading("tonal", metric_type, now, float(readiness[muscle]), "percent")]

    def _fetch_strength_score(self, start: date, end: date) -> list[MetricReading]:
        history = self._client.get_strength_score_history(limit=self.STRENGTH_SCORE_HISTORY_LIMIT)
        readings = []
        for entry in history:
            entry_date = date.fromisoformat(entry["date"])
            if start <= entry_date <= end:
                readings.append(
                    MetricReading(
                        "tonal",
                        "tonal_strength_score",
                        datetime.combine(entry_date, datetime.min.time()),
                        float(entry["overall"]),
                        "score",
                    )
                )
        return readings

    def _raw_activities_in_range(self, start: date, end: date) -> list[dict]:
        limit = self.ACTIVITIES_FETCH_LIMIT
        if limit not in self._raw_activities_cache:
            self._raw_activities_cache[limit] = self._client.get_activities(limit=limit)
        raw = self._raw_activities_cache[limit]
        results = []
        for entry in raw:
            timestamp = _parse_tonal_timestamp(entry["date"])
            if start <= timestamp.date() <= end:
                results.append({**entry, "_timestamp": timestamp})
        return results

    def _fetch_workout_metric(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        field = "total_volume_lbs" if metric_type == "tonal_workout_volume" else "duration_seconds"
        unit = "lbs" if metric_type == "tonal_workout_volume" else "seconds"
        readings = []
        for entry in self._raw_activities_in_range(start, end):
            value = entry.get(field)
            if value is None:
                continue
            readings.append(MetricReading("tonal", metric_type, entry["_timestamp"], float(value), unit))
        return readings

    def fetch_activities(self, start: date, end: date) -> list[Activity]:
        """Full Activity records across [start, end] -- the informal
        `fetch_activities` extension convention (GarminProvider/StravaProvider),
        detected via `hasattr` by callers."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        activities = []
        for entry in self._raw_activities_in_range(start, end):
            duration = entry.get("duration_seconds")
            activities.append(
                Activity(
                    id=f"tonal:{entry['activity_id']}",
                    source="tonal",
                    activity_id=str(entry["activity_id"]),
                    activity_name=entry["title"],
                    activity_type="strength_training",
                    sport_type=entry.get("type") or "strength_training",
                    start_time=entry["_timestamp"],
                    duration_seconds=float(duration) if duration is not None else 0.0,
                    distance_meters=None,
                    calories=None,
                    avg_hr=None,
                    max_hr=None,
                    avg_speed=None,
                    max_speed=None,
                    elevation_gain=None,
                    elevation_loss=None,
                    created_at=now,
                )
            )
        return activities

    def get_workout_detail(self, conn: sqlite3.Connection, activity_id: str) -> dict:
        """Per-set breakdown for one workout: fetches from Tonal, persists
        the sets into `strength_set` (keyed off the same `activity.id`
        convention, f"tonal:{activity_id}"), and returns the raw detail dict
        for direct MCP-tool use."""
        detail = self._client.get_workout_detail(activity_id)
        full_activity_id = f"tonal:{activity_id}"
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        strength_sets = [
            StrengthSet(
                id=f"{full_activity_id}:{index}",
                activity_id=full_activity_id,
                movement_id=set_data["movement_id"],
                movement_name=None,
                set_index=index,
                is_warm_up=bool(set_data.get("is_warm_up")),
                reps=set_data.get("reps"),
                weight_lbs=set_data.get("weight_lbs"),
                volume_lbs=set_data.get("volume_lbs"),
                one_rep_max=set_data.get("one_rep_max"),
                max_power_watts=set_data.get("max_power_watts"),
                rom_inches=set_data.get("rom_inches"),
                struggling_score=set_data.get("struggling_score"),
                side=set_data.get("side"),
                created_at=now,
            )
            for index, set_data in enumerate(detail.get("sets", []))
        ]
        if strength_sets:
            repository.upsert_strength_sets(conn, strength_sets)
        return detail

    def search_movements(self, query: str | None = None, muscle_group: str | None = None) -> list[dict]:
        return self._client.search_movements(query=query, muscle_group=muscle_group)

    def estimate_workout(self, blocks: list[dict]) -> dict:
        return self._client.estimate_workout(blocks)

    def create_workout(self, title: str, blocks: list[dict]) -> dict:
        return self._client.create_workout(title, blocks)

    def delete_workout(self, workout_id: str) -> bool:
        return self._client.delete_workout(workout_id)
