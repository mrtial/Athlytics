from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from core.providers.mi_fitness import MI_FITNESS_METRIC_TYPES
from core.providers.registry import PROVIDER_REGISTRY, ProviderInfo, connected_providers, get_provider
from core.security.credentials import CredentialStore
from core.storage.db import connect


def _state(
    tmp_path,
    *,
    garmin_connected=False,
    strava_connected=False,
    mi_fitness_connected=False,
    tonal_connected=False,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    garmin_store = CredentialStore(Fernet.generate_key(), tmp_path / "garmin.enc")
    if garmin_connected:
        garmin_store.save({"email": "a@example.com", "password": "x"})
    strava_store = CredentialStore(Fernet.generate_key(), tmp_path / "strava.enc")
    if strava_connected:
        strava_store.save({"client_id": "1", "client_secret": "s", "access_token": "a", "refresh_token": "r", "expires_at": "9999999999"})
    mi_fitness_store = CredentialStore(Fernet.generate_key(), tmp_path / "mi_fitness.enc")
    if mi_fitness_connected:
        mi_fitness_store.save({"token_file_content": "{}", "uid": "u"})
    tonal_store = CredentialStore(Fernet.generate_key(), tmp_path / "tonal.enc")
    if tonal_connected:
        tonal_store.save({"email": "a@example.com", "password": "x"})
    return SimpleNamespace(
        credential_store=garmin_store,
        strava_credential_store=strava_store,
        mi_fitness_credential_store=mi_fitness_store,
        tonal_credential_store=tonal_store,
    )


def test_registry_contains_garmin_strava_apple_health_mi_fitness_tonal_and_only_those():
    ids = {p.id for p in PROVIDER_REGISTRY}
    assert ids == {"garmin", "strava", "apple_health", "mi_fitness", "tonal"}


def test_registry_entries_have_expected_flow_types():
    flow_types = {p.id: p.flow_type for p in PROVIDER_REGISTRY}
    assert flow_types == {
        "garmin": "credentials_form",
        "strava": "oauth_redirect",
        "apple_health": "file_import",
        "mi_fitness": "qr_login_poll",
        "tonal": "credentials_form",
    }


def test_get_provider_returns_matching_entry():
    assert get_provider("strava").display_name == "Strava"


def test_get_provider_raises_on_unknown_id():
    with pytest.raises(ValueError):
        get_provider("not_a_real_provider")


def test_garmin_is_connected_reflects_credential_store(tmp_path):
    provider = get_provider("garmin")
    conn = connect(tmp_path / "test.db")

    assert provider.is_connected(conn, _state(tmp_path, garmin_connected=False)) is False
    assert provider.is_connected(conn, _state(tmp_path / "b", garmin_connected=True)) is True


def test_strava_is_connected_reflects_credential_store(tmp_path):
    provider = get_provider("strava")
    conn = connect(tmp_path / "test.db")

    assert provider.is_connected(conn, _state(tmp_path, strava_connected=False)) is False
    assert provider.is_connected(conn, _state(tmp_path / "b", strava_connected=True)) is True


def test_strava_is_connected_reflects_file_imported_activities_without_oauth(tmp_path):
    from datetime import datetime

    from core.storage import repository
    from core.storage.models import Activity

    provider = get_provider("strava")
    conn = connect(tmp_path / "test.db")
    state = _state(tmp_path, strava_connected=False)

    assert provider.is_connected(conn, state) is False

    repository.upsert_activities(
        conn,
        [
            Activity(
                id="strava:1",
                source="strava",
                activity_id="1",
                activity_name="Morning Run",
                activity_type="running",
                sport_type="Run",
                start_time=datetime(2026, 1, 1, 7, 0),
                duration_seconds=1800.0,
                distance_meters=5000.0,
                calories=300.0,
                avg_hr=140.0,
                max_hr=160.0,
                avg_speed=2.8,
                max_speed=3.5,
                elevation_gain=20.0,
                elevation_loss=20.0,
                created_at=datetime(2026, 1, 1, 8, 0),
            )
        ],
    )

    assert provider.is_connected(conn, state) is True


def test_apple_health_is_connected_reflects_synced_data(tmp_path):
    from datetime import date

    from core.storage import repository

    provider = get_provider("apple_health")
    conn = connect(tmp_path / "test.db")
    state = _state(tmp_path)

    assert provider.is_connected(conn, state) is False

    repository.set_checkpoint(conn, "apple_health", "steps", date(2026, 1, 1))
    assert provider.is_connected(conn, state) is True


def test_mi_fitness_is_connected_reflects_credential_store(tmp_path):
    provider = get_provider("mi_fitness")
    conn = connect(tmp_path / "test.db")

    assert provider.is_connected(conn, _state(tmp_path, mi_fitness_connected=False)) is False
    assert provider.is_connected(conn, _state(tmp_path / "b", mi_fitness_connected=True)) is True


def test_tonal_is_connected_reflects_credential_store(tmp_path):
    provider = get_provider("tonal")
    conn = connect(tmp_path / "test.db")

    assert provider.is_connected(conn, _state(tmp_path, tonal_connected=False)) is False
    assert provider.is_connected(conn, _state(tmp_path / "b", tonal_connected=True)) is True


def test_connected_providers_returns_only_connected_entries(tmp_path):
    conn = connect(tmp_path / "test.db")
    state = _state(tmp_path, garmin_connected=True, strava_connected=False)

    result = connected_providers(conn, state)

    assert [p.id for p in result] == ["garmin"]


def test_strava_metric_types_are_registered_correctly():
    from core.providers.strava import STRAVA_METRIC_TYPES

    assert get_provider("strava").metric_types == STRAVA_METRIC_TYPES


def test_registry_includes_mi_fitness_with_qr_login_poll_flow_type():
    provider = get_provider("mi_fitness")

    assert provider.flow_type == "qr_login_poll"
    assert provider.metric_types == MI_FITNESS_METRIC_TYPES
