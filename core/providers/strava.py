"""Strava V3 API provider.

Fetches activities via OAuth 2.0 and maps them onto Athlytics's Activity/
MetricReading models. See docs/superpowers/specs/strava_provider.md for
the full design, including the cross-source Activity dedup this provider
relies on (core.storage.repository.upsert_activities).
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx

from core.providers.base import RateLimitError
from core.providers.normalize import normalize_activity_type
from core.security.credentials import CredentialStore
from core.storage.models import Activity, MetricReading

STRAVA_BASE_URL = "https://www.strava.com"
STRAVA_TOKEN_PATH = "/oauth/token"
STRAVA_ACTIVITIES_PATH = "/api/v3/athlete/activities"
STRAVA_PAGE_SIZE = 200
KILOJOULES_TO_KCAL = 0.239006


class StravaAuthError(Exception):
    """Raised when Strava OAuth token exchange/refresh fails: a revoked
    refresh_token, invalid client credentials, or no credentials configured
    yet. Not retryable with backoff, unlike RateLimitError."""


def _token_request(data: dict[str, str], http_client: httpx.Client) -> dict[str, str]:
    response = http_client.post(STRAVA_TOKEN_PATH, data=data)
    if response.status_code != 200:
        raise StravaAuthError(f"Strava token request failed: {response.status_code} {response.text}")
    body = response.json()
    return {
        "client_id": data["client_id"],
        "client_secret": data["client_secret"],
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_at": str(int(body["expires_at"])),
    }


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str, http_client: httpx.Client) -> dict[str, str]:
    """One-time exchange of an OAuth authorization code for the first
    access/refresh token pair. Used only by the /oauth/strava/callback
    route (Task 10) -- StravaProvider itself only ever refreshes."""
    return _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        http_client,
    )


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str, http_client: httpx.Client) -> dict[str, str]:
    return _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        http_client,
    )


def _parse_strava_timestamp(value: str) -> datetime:
    """Strava's start_date is always UTC ISO 8601 (e.g. '2026-01-01T07:00:00Z')."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _parse_activity(raw: dict, now: datetime) -> Activity:
    activity_id = str(raw["id"])
    sport_key = raw.get("sport_type") or raw.get("type") or "Workout"

    calories = raw.get("calories")
    if calories is None and raw.get("kilojoules") is not None:
        calories = float(raw["kilojoules"]) * KILOJOULES_TO_KCAL

    return Activity(
        id=f"strava:{activity_id}",
        source="strava",
        activity_id=activity_id,
        activity_name=raw.get("name") or "Workout",
        activity_type=normalize_activity_type(sport_key),
        sport_type=sport_key,
        start_time=_parse_strava_timestamp(raw["start_date"]),
        duration_seconds=float(raw.get("moving_time") or 0.0),
        distance_meters=float(raw["distance"]) if raw.get("distance") is not None else None,
        calories=float(calories) if calories is not None else None,
        avg_hr=float(raw["average_heartrate"]) if raw.get("average_heartrate") is not None else None,
        max_hr=float(raw["max_heartrate"]) if raw.get("max_heartrate") is not None else None,
        avg_speed=float(raw["average_speed"]) if raw.get("average_speed") is not None else None,
        max_speed=float(raw["max_speed"]) if raw.get("max_speed") is not None else None,
        elevation_gain=float(raw["total_elevation_gain"]) if raw.get("total_elevation_gain") is not None else None,
        elevation_loss=None,  # SummaryActivity has no separate elevation-loss field
        created_at=now,
    )


STRAVA_METRIC_TYPES: list[str] = ["activity_duration", "activity_distance", "activity_calories"]
TOKEN_EXPIRY_BUFFER_SECONDS = 60


class StravaProvider:
    name = "strava"
    RATE_LIMIT_BACKOFF_THRESHOLD = 0.9
    RATE_LIMIT_BACKOFF_SECONDS = 60.0

    def __init__(
        self,
        credential_store: CredentialStore,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], float] = time.time,
    ):
        credentials = credential_store.load()
        if credentials is None:
            raise StravaAuthError("no Strava credentials configured; connect a Strava account first")

        self._credential_store = credential_store
        self._http = http_client or httpx.Client(base_url=STRAVA_BASE_URL, timeout=30.0)
        self._sleep_fn = sleep_fn
        self._now_fn = now_fn
        self._rate_limit_ratio: float = 0.0
        self._activities_cache: dict[tuple[date, date], list[Activity]] = {}

        expires_at = int(credentials["expires_at"])
        if self._now_fn() >= expires_at - TOKEN_EXPIRY_BUFFER_SECONDS:
            credentials = refresh_access_token(
                credentials["client_id"], credentials["client_secret"], credentials["refresh_token"], self._http
            )
            self._credential_store.save(credentials)

        self._access_token = credentials["access_token"]

    def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._rate_limit_ratio >= self.RATE_LIMIT_BACKOFF_THRESHOLD:
            self._sleep_fn(self.RATE_LIMIT_BACKOFF_SECONDS)

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        response = self._http.request(method, path, headers=headers, **kwargs)

        if response.status_code == 429:
            raise RateLimitError(f"Strava rate limit exceeded: {response.text}")
        if response.status_code == 401:
            raise StravaAuthError(f"Strava rejected the access token: {response.text}")

        usage_header = response.headers.get("X-ReadRateLimit-Usage")
        limit_header = response.headers.get("X-ReadRateLimit-Limit")
        if usage_header and limit_header:
            usage_15min = int(usage_header.split(",")[0])
            limit_15min = int(limit_header.split(",")[0])
            self._rate_limit_ratio = usage_15min / limit_15min if limit_15min else 0.0

        response.raise_for_status()
        return response

    def _fetch_raw_activities(self, start: date, end: date) -> list[dict]:
        after = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        before_date = end + timedelta(days=1)
        before = int(datetime.combine(before_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        results: list[dict] = []
        page = 1
        while True:
            response = self._call(
                "GET",
                STRAVA_ACTIVITIES_PATH,
                params={"after": after, "before": before, "page": page, "per_page": STRAVA_PAGE_SIZE},
            )
            batch = response.json()
            results.extend(batch)
            if len(batch) < STRAVA_PAGE_SIZE:
                break
            page += 1
        return results

    def fetch_activities(self, start: date, end: date) -> list[Activity]:
        cache_key = (start, end)
        if cache_key not in self._activities_cache:
            raw_list = self._fetch_raw_activities(start, end)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            self._activities_cache[cache_key] = [_parse_activity(raw, now) for raw in raw_list]
        return self._activities_cache[cache_key]

    def supported_metric_types(self) -> list[str]:
        return list(STRAVA_METRIC_TYPES)

    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        if metric_type not in STRAVA_METRIC_TYPES:
            raise ValueError(f"unsupported metric_type for StravaProvider: {metric_type!r}")

        readings: list[MetricReading] = []
        for activity in self.fetch_activities(start, end):
            if metric_type == "activity_duration":
                readings.append(
                    MetricReading("strava", "activity_duration", activity.start_time, activity.duration_seconds / 60.0, "minutes")
                )
            elif metric_type == "activity_distance" and activity.distance_meters is not None:
                readings.append(
                    MetricReading("strava", "activity_distance", activity.start_time, activity.distance_meters / 1000.0, "km")
                )
            elif metric_type == "activity_calories" and activity.calories is not None:
                readings.append(
                    MetricReading("strava", "activity_calories", activity.start_time, activity.calories, "kcal")
                )
        return readings
