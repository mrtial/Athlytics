from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable

from garminconnect import Garmin

from core.providers.apple_health import SOURCE as APPLE_HEALTH_SOURCE
from core.providers.apple_health import METRIC_TYPE_UNITS, AppleHealthProvider
from core.providers.garmin import GarminProvider
from core.providers.registry import PROVIDER_REGISTRY
from core.providers.strava_export import StravaExportProvider
from core.security.credentials import CredentialStore
from core.storage import repository
from core.storage.models import MetricReading

SUPPORTED_PROVIDERS = {p.id for p in PROVIDER_REGISTRY if p.flow_type == "credentials_form"}


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
        existing = repository.get_checkpoint(conn, provider.name, metric_type)
        if existing is None or latest_date > existing:
            repository.set_checkpoint(conn, provider.name, metric_type, latest_date)

    return {metric_type: f"imported: {count}" for metric_type, count in counts.items()}


def ingest_apple_health_metrics(conn, day: date, readings: dict[str, float]) -> dict[str, object]:
    """Lightweight counterpart to import_apple_health for the scheduled
    multi-metric Shortcut path: takes values a Shortcut already computed
    itself (Find Health Samples + Calculate Statistics), keyed by
    Athlytics's own metric_type vocabulary, and stores one reading per key
    for the given calendar day. Units are looked up server-side from
    METRIC_TYPE_UNITS rather than trusted from the caller, since a wrong
    unit string would silently corrupt that metric_type's stored unit for
    every other source (see HK_QUANTITY_MAP's comment in apple_health.py).
    Unrecognized keys are skipped and reported back rather than rejected
    outright, so one Shortcut typo doesn't drop the whole sync.
    """
    timestamp = datetime.combine(day, time.min)
    batch: list[MetricReading] = []
    skipped: list[str] = []

    for metric_type, value in readings.items():
        unit = METRIC_TYPE_UNITS.get(metric_type)
        if unit is None:
            skipped.append(metric_type)
            continue
        batch.append(
            MetricReading(
                source=APPLE_HEALTH_SOURCE, metric_type=metric_type, timestamp=timestamp, value=float(value), unit=unit
            )
        )

    if batch:
        repository.upsert_readings(conn, batch)
        for reading in batch:
            existing = repository.get_checkpoint(conn, APPLE_HEALTH_SOURCE, reading.metric_type)
            if existing is None or day > existing:
                repository.set_checkpoint(conn, APPLE_HEALTH_SOURCE, reading.metric_type, day)

    return {
        "imported": {reading.metric_type: "imported: 1" for reading in batch},
        "skipped": skipped,
    }


def import_strava_export(conn, payload: bytes) -> dict[str, int]:
    """Streams payload (a Strava "Request your Archive" zip) through
    StravaExportProvider and upserts the resulting activities, applying the
    same cross-source dedup the OAuth path uses (repository.upsert_activities
    -- Garmin wins ties, see ACTIVITY_SOURCE_PRIORITY). Also records a
    sync_run_status row for "strava" so the Connections page's "Last synced"
    reflects the upload time -- gated the same way perform_sync_pass gates
    the OAuth path, so an unrelated background sync pass (which only touches
    Strava when OAuth credentials exist) never overwrites this timestamp for
    a file-import-only user."""
    from app.sync import record_sync_run

    activities = list(StravaExportProvider().ingest(payload))
    inserted = repository.upsert_activities(conn, activities)
    if activities:
        record_sync_run(conn, "strava", auth_error=None)
    return {"imported": inserted, "total_in_file": len(activities)}
