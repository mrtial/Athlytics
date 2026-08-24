from __future__ import annotations

import asyncio
import os
import tempfile
import time as time_module
from datetime import date as date_cls, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from mi_fitness.exceptions import (
    APIError,
    AuthError,
    TokenExpiredError,
)

from core.providers.base import RateLimitError
from core.security.credentials import CredentialStore
from core.storage.models import MetricReading

try:
    from mi_fitness import MiHealthClient
except ImportError:  # pragma: no cover -- only during Task 1 before the dependency is installed
    MiHealthClient = None

MI_FITNESS_METRIC_TYPES: list[str] = [
    "steps",
    "daily_distance",
    "active_calories",
    "resting_hr",
    "sleep_score",  # see Task 1's field inventory -- swap for "sleep_duration" if SleepData has no score field
]

METERS_TO_KM = 0.001
DAY_PACE_SECONDS = 0.5


class MiFitnessAuthError(Exception):
    """Raised when Mi Fitness authentication fails or the stored session
    has expired and needs a fresh QR-code login (see plan
    2026-08-17-mi-fitness-qr-onboarding-flow.md). Mirrors StravaAuthError /
    GarminAuthError: NOT retryable with backoff, unlike RateLimitError.
    """


def _classify_exception(exc: Exception) -> Exception:
    """Maps a raw mi-fitness-python exception onto this codebase's error
    contract (RateLimitError / MiFitnessAuthError), the way
    garmin.py's _call() maps GarminConnectTooManyRequestsError /
    GarminConnectAuthenticationError. Anything not explicitly recognized is
    returned unchanged so the caller re-raises it as-is -- never silently
    downgrade an unknown failure into a retryable RateLimitError.
    """
    if isinstance(exc, (TokenExpiredError, AuthError)):
        return MiFitnessAuthError(str(exc))
    if isinstance(exc, APIError) and getattr(exc, "status_code", None) == 429:
        return RateLimitError(str(exc))
    return exc


def save_mi_fitness_session(credential_store: CredentialStore, token_file_content: str, uid: str) -> None:
    """Persists mi-fitness-python's on-disk token file content (whatever
    save_token() wrote -- see Task 1's field inventory for its exact
    format) plus the uid every get_*() call needs, inside Athlytics'
    existing Fernet-encrypted CredentialStore. mi-fitness-python itself
    only knows how to read/write a plaintext file path (from_token /
    save_token), so the file's *contents* are what gets encrypted here,
    not the file itself.
    """
    credential_store.save({"token_file_content": token_file_content, "uid": uid})


class MiFitnessProvider:
    name = "mi_fitness"

    def __init__(
        self,
        credential_store: CredentialStore,
        client_factory: Callable = None,
        run_async: Callable = asyncio.run,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ):
        credentials = credential_store.load()
        if credentials is None:
            raise MiFitnessAuthError("no Mi Fitness credentials configured; connect a Mi Fitness account first")

        self._credential_store = credential_store
        self._client_factory = client_factory or MiHealthClient.from_token
        self._run_async = run_async
        self._sleep_fn = sleep_fn
        try:
            self._uid = int(credentials["uid"])
        except (KeyError, ValueError, TypeError) as exc:
            raise MiFitnessAuthError(f"Mi Fitness credential is missing a valid uid: {exc}") from exc
        self._token_file_content = credentials["token_file_content"]

        # Fail fast at construction, mirroring StravaProvider's proactive
        # token refresh (strava.py:136-141) and GarminProvider's proactive
        # login (garmin.py:92-98) -- a dead session should surface as
        # MiFitnessAuthError here, not as a confusing failure from inside
        # the first fetch() call.
        self._run_async(self._verify_session())

    async def _verify_session(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(self._token_file_content)
            token_path = f.name
        try:
            async with self._client_factory(token_path) as client:
                # MiHealthClient.__aenter__ does zero I/O by itself, so we
                # need one cheap, real, authenticated call here to actually
                # contact the server and fail fast on a dead session.
                # get_relatives() is the lightest such call in the library:
                # a single GET request with no arguments (unlike
                # get_latest_data(uid), which fetches a full aggregated
                # data snapshot for one account).
                await client.get_relatives()
        except Exception as exc:
            raise _classify_exception(exc) from exc
        finally:
            os.unlink(token_path)

    def supported_metric_types(self) -> list[str]:
        return list(MI_FITNESS_METRIC_TYPES)

    def fetch(self, metric_type: str, start: date_cls, end: date_cls) -> list[MetricReading]:
        if metric_type not in MI_FITNESS_METRIC_TYPES:
            raise ValueError(f"unsupported metric_type for MiFitnessProvider: {metric_type!r}")

        try:
            return self._run_async(self._fetch_async(metric_type, start, end))
        except Exception as exc:
            raise _classify_exception(exc) from exc

    async def _fetch_async(self, metric_type: str, start: date_cls, end: date_cls) -> list[MetricReading]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(self._token_file_content)
            token_path = f.name
        try:
            async with self._client_factory(token_path) as client:
                readings: list[MetricReading] = []
                current = start
                first = True
                while current <= end:
                    if not first:
                        self._sleep_fn(DAY_PACE_SECONDS)
                    first = False
                    reading = await self._fetch_one_day(client, metric_type, current)
                    if reading is not None:
                        readings.append(reading)
                    current += timedelta(days=1)
            # mi_fitness can auto-refresh an expiring session token during a
            # real API call above, writing the refreshed token back to
            # token_path. That refresh would otherwise be silently thrown
            # away when the temp file is deleted below -- persist it so the
            # next fetch() doesn't start from a stale token.
            refreshed_content = Path(token_path).read_text()
            if refreshed_content != self._token_file_content:
                save_mi_fitness_session(
                    self._credential_store, token_file_content=refreshed_content, uid=str(self._uid)
                )
                self._token_file_content = refreshed_content
            return readings
        finally:
            os.unlink(token_path)

    async def _fetch_one_day(self, client, metric_type: str, day: date_cls) -> MetricReading | None:
        timestamp = datetime.combine(day, time.min)

        if metric_type == "steps":
            entries = await client.get_steps(self._uid, day, days=1)
            if not entries:
                return None
            total = sum(entry.steps for entry in entries)
            return MetricReading("mi_fitness", "steps", timestamp, float(total), "steps")

        if metric_type == "daily_distance":
            entries = await client.get_steps(self._uid, day, days=1)
            if not entries:
                return None
            total_meters = sum(entry.distance for entry in entries)
            return MetricReading("mi_fitness", "daily_distance", timestamp, total_meters * METERS_TO_KM, "km")

        if metric_type == "resting_hr":
            entries = await client.get_heart_rate(self._uid, day, days=1)
            nonzero = [entry.avg_rhr for entry in entries if entry.avg_rhr]
            if not nonzero:
                return None
            average = sum(nonzero) / len(nonzero)
            return MetricReading("mi_fitness", "resting_hr", timestamp, float(average), "bpm")

        if metric_type == "sleep_score":
            entries = await client.get_sleep(self._uid, day, days=1)
            if not entries or not entries[0].sleep_score:
                # sleep_score defaults to 0 when the library has no computed
                # score for that day (same "0 means unshared/absent"
                # semantics as HeartRateData.avg_rhr above) -- a real score
                # is 1-100, so storing an unguarded 0 would pollute data as
                # if it were a genuine (impossibly low) score.
                return None
            return MetricReading("mi_fitness", "sleep_score", timestamp, float(entries[0].sleep_score), "score")

        if metric_type == "active_calories":
            entries = await client.get_calories_history(self._uid, day, days=1)
            if not entries:
                return None
            total = sum(entry.calories for entry in entries)
            return MetricReading("mi_fitness", "active_calories", timestamp, float(total), "kcal")

        raise AssertionError(f"unhandled metric_type in _fetch_one_day: {metric_type!r}")  # unreachable, fetch() already validated
