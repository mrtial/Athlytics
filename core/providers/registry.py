from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES
from core.providers.garmin import GARMIN_METRIC_TYPES
from core.providers.mi_fitness import MI_FITNESS_METRIC_TYPES
from core.providers.strava import STRAVA_METRIC_TYPES
from core.storage import repository


@dataclass(frozen=True)
class ProviderInfo:
    """One entry in PROVIDER_REGISTRY -- everything a route needs to know
    about a data source without importing its provider module directly.

    `is_connected` is duck-typed on `state`: each entry's function reads
    whichever `state.<provider>_credential_store` attribute (or, for
    apple_health, the sqlite connection) it needs, so any object with the
    relevant attributes works (in practice `request.app.state` in routes,
    `types.SimpleNamespace(...)` in tests).
    """

    id: str
    display_name: str
    flow_type: str  # "credentials_form" | "oauth_redirect" | "file_import" | "qr_login_poll"
    metric_types: list[str]
    is_connected: Callable[[sqlite3.Connection, object], bool]


def _garmin_is_connected(conn: sqlite3.Connection, state: object) -> bool:
    return state.credential_store.load() is not None


def _strava_is_connected(conn: sqlite3.Connection, state: object) -> bool:
    # OAuth credentials are the primary connection method, but a user
    # without an active Strava API subscription can instead import a
    # bulk-export zip (app.data_sources.import_strava_export) -- that
    # writes Activity rows with no OAuth credentials involved, so this
    # source is also "connected" once any Strava-sourced activity exists.
    if state.strava_credential_store.load() is not None:
        return True
    return repository.has_activities_from_source(conn, "strava")


def _apple_health_is_connected(conn: sqlite3.Connection, state: object) -> bool:
    return repository.has_synced_data(conn, "apple_health")


def _mi_fitness_is_connected(conn: sqlite3.Connection, state: object) -> bool:
    # `state.mi_fitness_credential_store` is set up by
    # 2026-08-17-mi-fitness-qr-onboarding-flow.md (now complete). Still
    # read via getattr rather than a direct attribute access: it keeps this
    # function tolerant of any `state` object (e.g. a bare
    # types.SimpleNamespace(...) in a test) that doesn't set the attribute,
    # reporting "not connected" rather than raising, so routes that iterate
    # the full PROVIDER_REGISTRY (dashboard, connections, sync_status,
    # onboarding) keep working either way.
    store = getattr(state, "mi_fitness_credential_store", None)
    return store is not None and store.load() is not None


PROVIDER_REGISTRY: list[ProviderInfo] = [
    ProviderInfo(
        id="garmin",
        display_name="Garmin",
        flow_type="credentials_form",
        metric_types=GARMIN_METRIC_TYPES,
        is_connected=_garmin_is_connected,
    ),
    ProviderInfo(
        id="strava",
        display_name="Strava",
        flow_type="oauth_redirect",
        metric_types=STRAVA_METRIC_TYPES,
        is_connected=_strava_is_connected,
    ),
    ProviderInfo(
        id="apple_health",
        display_name="Apple Health",
        flow_type="file_import",
        metric_types=APPLE_HEALTH_METRIC_TYPES,
        is_connected=_apple_health_is_connected,
    ),
    ProviderInfo(
        id="mi_fitness",
        display_name="Mi Fitness",
        flow_type="qr_login_poll",
        metric_types=MI_FITNESS_METRIC_TYPES,
        is_connected=_mi_fitness_is_connected,
    ),
]


def get_provider(provider_id: str) -> ProviderInfo:
    for provider in PROVIDER_REGISTRY:
        if provider.id == provider_id:
            return provider
    raise ValueError(f"unknown provider id: {provider_id!r}")


def connected_providers(conn: sqlite3.Connection, state: object) -> list[ProviderInfo]:
    return [p for p in PROVIDER_REGISTRY if p.is_connected(conn, state)]
