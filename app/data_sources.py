from pathlib import Path
from typing import Callable

from garminconnect import Garmin

from core.providers.garmin import GarminProvider
from core.security.credentials import CredentialStore

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
