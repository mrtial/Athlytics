from datetime import date, datetime, time

import pytest
from cryptography.fernet import Fernet

from core.providers.base import RateLimitError
from core.providers.garmin import GarminAuthError, GarminProvider
from core.security.credentials import CredentialStore
from core.storage.models import MetricReading


class _StubGarminClient:
    def __init__(self, email, password, return_on_mfa=False):
        self.email = email
        self.password = password
        self.return_on_mfa = return_on_mfa
        self.login_calls = []
        self._needs_mfa = False

    def login(self, tokenstore=None):
        self.login_calls.append(tokenstore)
        return (self._needs_mfa, None)


class _MfaRequiredClient(_StubGarminClient):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._needs_mfa = True


class _LoginFailsClient(_StubGarminClient):
    def login(self, tokenstore=None):
        from garminconnect import GarminConnectAuthenticationError

        raise GarminConnectAuthenticationError("bad credentials")


class _RateLimitedClient(_StubGarminClient):
    def some_method(self):
        from garminconnect import GarminConnectTooManyRequestsError

        raise GarminConnectTooManyRequestsError("rate limited")


class _SessionExpiredClient(_StubGarminClient):
    def some_method(self):
        from garminconnect import GarminConnectAuthenticationError

        raise GarminConnectAuthenticationError("session expired")


def _credential_store(tmp_path, credentials=None):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "garmin_credentials.enc")
    if credentials is not None:
        store.save(credentials)
    return store


def test_init_raises_garmin_auth_error_when_no_credentials_saved(tmp_path):
    store = _credential_store(tmp_path)

    with pytest.raises(GarminAuthError, match="no Garmin credentials"):
        GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)


def test_init_constructs_and_logs_in_client_with_stored_credentials(tmp_path):
    store = _credential_store(tmp_path, {"email": "athlete@example.com", "password": "hunter2"})
    token_dir = tmp_path / "tokens"

    provider = GarminProvider(store, token_dir, garmin_client_factory=_StubGarminClient)

    assert provider._client.email == "athlete@example.com"
    assert provider._client.password == "hunter2"
    assert provider._client.login_calls == [str(token_dir)]
    assert provider.name == "garmin"


def test_init_raises_garmin_auth_error_when_login_fails(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "wrong"})

    with pytest.raises(GarminAuthError, match="authentication failed"):
        GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_LoginFailsClient)


def test_init_raises_garmin_auth_error_when_mfa_required(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})

    with pytest.raises(GarminAuthError, match="MFA"):
        GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_MfaRequiredClient)


def test_supported_metric_types_reflects_registered_parsers(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)

    assert provider.supported_metric_types() == list(provider._registry.keys())


def test_fetch_raises_value_error_for_unsupported_metric_type(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)

    with pytest.raises(ValueError, match="unsupported metric_type"):
        provider.fetch("not_a_real_metric", date(2026, 1, 1), date(2026, 1, 2))


def test_fetch_dispatches_to_registered_handler_with_start_and_end(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)
    calls = []
    provider._registry["stub_metric"] = lambda start, end: calls.append((start, end)) or ["fake-readings"]

    result = provider.fetch("stub_metric", date(2026, 1, 1), date(2026, 1, 5))

    assert result == ["fake-readings"]
    assert calls == [(date(2026, 1, 1), date(2026, 1, 5))]


def test_call_maps_too_many_requests_error_to_rate_limit_error(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_RateLimitedClient)

    with pytest.raises(RateLimitError):
        provider._call(provider._client.some_method)


def test_call_maps_authentication_error_to_garmin_auth_error(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_SessionExpiredClient)

    with pytest.raises(GarminAuthError, match="session was rejected"):
        provider._call(provider._client.some_method)


import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "garmin"


def _load_fixture(name: str):
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def _assert_valid_reading(reading, metric_type, unit):
    assert reading.source == "garmin"
    assert reading.metric_type == metric_type
    assert reading.unit == unit
    assert reading.timestamp.tzinfo is None
    assert isinstance(reading.value, float)


def test_parse_resting_hr_produces_naive_utc_bpm_readings():
    raw = _load_fixture("get_rhr_daily")

    readings = GarminProvider._parse_resting_hr(raw)

    assert len(readings) > 0
    for reading in readings:
        _assert_valid_reading(reading, "resting_hr", "bpm")


def test_parse_hrv_produces_naive_utc_ms_readings():
    raw = _load_fixture("get_hrv_data_range")

    readings = GarminProvider._parse_hrv(raw)

    assert len(readings) > 0
    for reading in readings:
        _assert_valid_reading(reading, "hrv", "ms")


def test_parse_hrv_returns_empty_list_when_raw_is_none():
    assert GarminProvider._parse_hrv(None) == []


def test_parse_vo2max_produces_naive_utc_readings():
    raw = _load_fixture("get_max_metrics_range")

    readings = GarminProvider._parse_vo2max(raw)

    assert len(readings) > 0
    for reading in readings:
        _assert_valid_reading(reading, "vo2max", "ml/kg/min")


def test_parse_body_battery_produces_naive_utc_percent_readings():
    raw = _load_fixture("get_body_battery")

    readings = GarminProvider._parse_body_battery(raw)

    assert len(readings) > 0
    for reading in readings:
        _assert_valid_reading(reading, "body_battery", "percent")
        assert 0.0 <= reading.value <= 100.0


def test_parse_weight_produces_naive_utc_kg_readings():
    raw = _load_fixture("get_body_composition")

    readings = GarminProvider._parse_weight(raw)

    assert len(readings) > 0
    for reading in readings:
        _assert_valid_reading(reading, "weight", "kg")
        assert 20.0 <= reading.value <= 300.0  # sane human-weight-in-kg sanity bound


def test_parse_sleep_produces_naive_utc_score_readings():
    raw = _load_fixture("get_sleep_daily")

    readings = GarminProvider._parse_sleep(raw)

    assert len(readings) > 0
    for reading in readings:
        _assert_valid_reading(reading, "sleep_score", "score")


def test_parse_steps_sums_intraday_entries_into_one_daily_reading():
    raw = _load_fixture("get_steps_data")

    readings = GarminProvider._parse_steps(raw, date(2026, 1, 1))

    assert len(readings) == 1
    _assert_valid_reading(readings[0], "steps", "count")
    assert readings[0].timestamp == datetime(2026, 1, 1, 0, 0)
    assert readings[0].value >= 0.0


def test_parse_stress_produces_one_daily_score_reading():
    raw = _load_fixture("get_stress_data")

    readings = GarminProvider._parse_stress(raw, date(2026, 1, 1))

    assert len(readings) == 1
    _assert_valid_reading(readings[0], "stress", "score")
    assert readings[0].timestamp == datetime(2026, 1, 1, 0, 0)


def test_fetch_single_day_metric_calls_garmin_method_once_per_day_in_range(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)
    calls = []

    def fake_garmin_method(cdate):
        calls.append(cdate)
        return {"day": cdate}

    def fake_parse_fn(raw, day):
        return [
            MetricReading("garmin", "stub_daily", datetime.combine(day, time.min), 1.0, "unit")
        ]

    readings = provider._fetch_single_day_metric(
        fake_garmin_method, fake_parse_fn, date(2026, 1, 1), date(2026, 1, 3)
    )

    assert calls == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert len(readings) == 3


def test_parse_respiration_produces_one_daily_reading():
    raw = _load_fixture("get_respiration_data")

    readings = GarminProvider._parse_respiration(raw, date(2026, 1, 1))

    assert len(readings) == 1
    _assert_valid_reading(readings[0], "respiration", "breaths_per_min")


def test_parse_spo2_produces_one_daily_percent_reading():
    raw = _load_fixture("get_spo2_data")

    readings = GarminProvider._parse_spo2(raw, date(2026, 1, 1))

    assert len(readings) == 1
    _assert_valid_reading(readings[0], "spo2", "percent")
    assert 0.0 <= readings[0].value <= 100.0
