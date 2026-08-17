from collections import defaultdict
from pathlib import Path
from typing import Callable

from garminconnect import Garmin

from core.providers.apple_health import AppleHealthProvider
from core.providers.garmin import GarminProvider
from core.security.credentials import CredentialStore
from core.storage import repository

SUPPORTED_PROVIDERS = {"garmin"}


def connect_garmin(
    credential_store: CredentialStore,
    token_cache_dir: Path,
    email: str,
    password: str,
    garmin_client_factory: Callable[..., Garmin] = Garmin,
) -> None:
    """Save the given Garmin credentials, then validate them by actually
    constructing a GarminProvider (a real login). Raises GarminAuthError
    (bad credentials, MFA required) if validation fails.
    """
    credential_store.save({"email": email, "password": password})
    GarminProvider(credential_store, token_cache_dir, garmin_client_factory=garmin_client_factory)


def import_apple_health(conn, payload: bytes, batch_size: int = 500) -> dict[str, str]:
    """Streams payload (a zip's raw bytes) through AppleHealthProvider,
    batching upserts every batch_size readings, and updates sync_checkpoint
    per metric_type to the latest date seen -- giving Apple Health the same
    "last synced" status signal Garmin's scheduler already provides."""
    provider = AppleHealthProvider()
    counts: dict[str, int] = defaultdict(int)
    latest_date_by_type: dict[str, object] = {}
    batch: list = []

    for reading in provider.ingest(payload):
        batch.append(reading)
        counts[reading.metric_type] += 1
        day = reading.timestamp.date()
        if reading.metric_type not in latest_date_by_type or day > latest_date_by_type[reading.metric_type]:
            latest_date_by_type[reading.metric_type] = day

        if len(batch) >= batch_size:
            repository.upsert_readings(conn, batch)
            batch = []

    if batch:
        repository.upsert_readings(conn, batch)

    for metric_type, latest_date in latest_date_by_type.items():
        repository.set_checkpoint(conn, provider.name, metric_type, latest_date)

    return {metric_type: f"imported: {count}" for metric_type, count in counts.items()}
