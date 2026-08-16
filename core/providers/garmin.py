from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from core.providers.base import RateLimitError
from core.security.credentials import CredentialStore
from core.storage.models import MetricReading


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails, requires MFA this headless
    context cannot complete, or no credentials are configured yet.

    Distinct from RateLimitError: an auth failure is not retryable with
    backoff. A later Dashboard plan catches this to surface a sync-status
    panel prompting the user to reconnect, per the design doc's Error
    Handling section ("Garmin auth failures and MFA challenges are
    surfaced in a sync-status panel, not silently retried forever").
    """


class GarminProvider:
    name = "garmin"

    def __init__(
        self,
        credential_store: CredentialStore,
        token_cache_dir: Path,
        garmin_client_factory: Callable[..., Garmin] = Garmin,
    ):
        credentials = credential_store.load()
        if credentials is None:
            raise GarminAuthError("no Garmin credentials configured; connect a data source first")

        self._client = garmin_client_factory(
            credentials["email"], credentials["password"], return_on_mfa=True
        )
        try:
            needs_mfa, _ = self._client.login(str(token_cache_dir))
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError(f"Garmin authentication failed: {exc}") from exc

        if needs_mfa:
            raise GarminAuthError(
                "Garmin requires an MFA code to complete login; headless sync "
                "cannot prompt interactively. Reconnect the data source "
                "interactively to complete MFA and refresh the cached session "
                "token."
            )

        self._registry: dict[str, Callable[[date, date], list[MetricReading]]] = {}

    def supported_metric_types(self) -> list[str]:
        return list(self._registry.keys())

    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        if metric_type not in self._registry:
            raise ValueError(f"unsupported metric_type for GarminProvider: {metric_type!r}")
        return self._registry[metric_type](start, end)

    def _call(self, garmin_method: Callable[..., object], *args: object) -> object:
        """Call a garminconnect client method, mapping its exceptions onto
        this codebase's error contract (RateLimitError / GarminAuthError)."""
        try:
            return garmin_method(*args)
        except GarminConnectTooManyRequestsError as exc:
            raise RateLimitError(str(exc)) from exc
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError(f"Garmin session was rejected: {exc}") from exc
