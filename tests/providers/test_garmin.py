from datetime import date, datetime, time, timedelta

import pytest
from cryptography.fernet import Fernet

from core.providers.base import RateLimitError
from core.providers.garmin import (
    GARMIN_METRIC_TYPES,
    SLEEP_HYDRATION_MAX_BACKFILL_DAYS,
    SYNC_RESYNC_GRACE_DAYS,
    GarminAuthError,
    GarminMfaRequired,
    GarminProvider,
    complete_garmin_mfa,
)
from core.security.credentials import CredentialStore
from core.storage.db import connect
from core.storage import repository
from core.storage.models import MetricReading


class _FakeInnerClient:
    """Stands in for Garmin.client (the garth-style session client), whose
    .dump() persists the resumed session's tokens to token_cache_dir."""

    def __init__(self):
        self.dump_calls = []

    def dump(self, path):
        self.dump_calls.append(path)


class _StubGarminClient:
    def __init__(self, email, password, return_on_mfa=False):
        self.email = email
        self.password = password
        self.return_on_mfa = return_on_mfa
        self.login_calls = []
        self.resume_login_calls = []
        self._needs_mfa = False
        self._client_state = None
        self.client = _FakeInnerClient()

    def login(self, tokenstore=None):
        self.login_calls.append(tokenstore)
        return (self._needs_mfa, self._client_state)

    def resume_login(self, client_state, mfa_code):
        self.resume_login_calls.append((client_state, mfa_code))
        return (None, None)


class _MfaRequiredClient(_StubGarminClient):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._needs_mfa = True
        self._client_state = {"pending": "state"}


class _MfaWrongCodeClient(_MfaRequiredClient):
    def resume_login(self, client_state, mfa_code):
        from garminconnect import GarminConnectAuthenticationError

        raise GarminConnectAuthenticationError("invalid MFA code")


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


def test_garmin_metric_types_constant_is_importable_without_instantiation():
    # No CredentialStore, no login -- this must work as a bare import.
    assert GARMIN_METRIC_TYPES == [
        "resting_hr", "hrv", "vo2max", "body_battery", "weight", "sleep_score",
        "steps", "stress", "respiration", "spo2", "training_load",
        "race_predictor_5k", "race_predictor_10k", "race_predictor_half_marathon",
        "race_predictor_marathon", "activity_duration", "activity_distance", "activity_calories",
    ]


def test_garmin_metric_types_constant_matches_live_registry(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)

    assert GARMIN_METRIC_TYPES == list(provider._registry.keys())


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


def test_init_raises_garmin_mfa_required_carrying_client_and_state(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})

    with pytest.raises(GarminMfaRequired, match="MFA") as exc_info:
        GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_MfaRequiredClient)

    exc = exc_info.value
    assert isinstance(exc, GarminAuthError)  # still catchable as the general auth-error case
    assert isinstance(exc.client, _MfaRequiredClient)
    assert exc.client_state == {"pending": "state"}


def test_complete_garmin_mfa_resumes_login_and_persists_token_cache(tmp_path):
    client = _MfaRequiredClient("a@example.com", "x", return_on_mfa=True)
    token_dir = tmp_path / "tokens"

    complete_garmin_mfa(client, {"pending": "state"}, "123456", token_dir)

    assert client.resume_login_calls == [({"pending": "state"}, "123456")]
    assert client.client.dump_calls == [str(token_dir)]


def test_complete_garmin_mfa_raises_garmin_auth_error_on_invalid_code(tmp_path):
    client = _MfaWrongCodeClient("a@example.com", "x", return_on_mfa=True)

    with pytest.raises(GarminAuthError, match="Invalid or expired MFA code"):
        complete_garmin_mfa(client, {"pending": "state"}, "000000", tmp_path / "tokens")

    assert client.client.dump_calls == []


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


def test_parse_training_load_produces_one_daily_reading():
    raw = _load_fixture("get_training_status")

    readings = GarminProvider._parse_training_load(raw, date(2026, 1, 1))

    assert len(readings) == 1
    _assert_valid_reading(readings[0], "training_load", "load")


def test_parse_race_predictions_produces_four_metric_types_per_day():
    raw = _load_fixture("get_race_predictions")

    readings = GarminProvider._parse_race_predictions(raw)

    assert len(readings) > 0
    metric_types = {r.metric_type for r in readings}
    assert metric_types == {
        "race_predictor_5k",
        "race_predictor_10k",
        "race_predictor_half_marathon",
        "race_predictor_marathon",
    }
    for reading in readings:
        assert reading.source == "garmin"
        assert reading.unit == "seconds"
        assert reading.timestamp.tzinfo is None
        assert isinstance(reading.value, float)


def test_fetch_race_predictor_caches_one_call_across_all_four_metric_types(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    raw = _load_fixture("get_race_predictions")

    class _RacePredictionClient(_StubGarminClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.get_race_predictions_calls = 0

        def get_race_predictions(self, startdate, enddate, _type):
            self.get_race_predictions_calls += 1
            return raw

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_RacePredictionClient)

    provider.fetch("race_predictor_5k", date(2026, 1, 1), date(2026, 1, 7))
    provider.fetch("race_predictor_10k", date(2026, 1, 1), date(2026, 1, 7))

    assert provider._client.get_race_predictions_calls == 1


def test_parse_activities_produces_three_metric_types_per_activity():
    raw = _load_fixture("get_activities_by_date")

    readings = GarminProvider._parse_activities(raw)

    assert len(readings) > 0
    metric_types = {r.metric_type for r in readings}
    assert metric_types == {"activity_duration", "activity_distance", "activity_calories"}
    for reading in readings:
        assert reading.source == "garmin"
        assert reading.timestamp.tzinfo is None
        assert isinstance(reading.value, float)


def test_parse_activity_records_produces_normalized_activities():
    raw = _load_fixture("get_activities_by_date")

    activities = GarminProvider._parse_activity_records(raw)

    assert len(activities) == 2
    act1 = activities[0]
    assert act1.id == "garmin:10001"
    assert act1.activity_name == "Morning Run"
    assert act1.activity_type == "running"
    assert act1.duration_seconds == 1800.0
    assert act1.distance_meters == 5000.0
    assert act1.calories == 350.0
    assert act1.start_time.tzinfo is None

    act2 = activities[1]
    assert act2.id == "garmin:10002"
    assert act2.activity_name == "Afternoon Ride"
    assert act2.activity_type == "cycling"
    assert act2.duration_seconds == 3600.0
    assert act2.distance_meters == 25000.0


def test_fetch_activity_metric_caches_one_call_across_all_three_metric_types(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    raw = _load_fixture("get_activities_by_date")

    class _ActivitiesClient(_StubGarminClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.get_activities_by_date_calls = 0

        def get_activities_by_date(self, startdate, enddate):
            self.get_activities_by_date_calls += 1
            return raw

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_ActivitiesClient)

    provider.fetch("activity_duration", date(2026, 1, 1), date(2026, 1, 7))
    provider.fetch("activity_distance", date(2026, 1, 1), date(2026, 1, 7))
    provider.fetch("activity_calories", date(2026, 1, 1), date(2026, 1, 7))
    acts = provider.fetch_activities(date(2026, 1, 1), date(2026, 1, 7))

    assert len(acts) == 2
    assert provider._client.get_activities_by_date_calls == 1



def test_supported_metric_types_covers_all_v1_metrics(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)

    assert sorted(provider.supported_metric_types()) == sorted(
        [
            "resting_hr",
            "hrv",
            "vo2max",
            "body_battery",
            "weight",
            "sleep_score",
            "steps",
            "stress",
            "respiration",
            "spo2",
            "training_load",
            "race_predictor_5k",
            "race_predictor_10k",
            "race_predictor_half_marathon",
            "race_predictor_marathon",
            "activity_duration",
            "activity_distance",
            "activity_calories",
        ]
    )


def test_stage_code_to_name_mapping():
    from core.providers.garmin import STAGE_CODE_TO_NAME
    assert STAGE_CODE_TO_NAME == {0.0: "deep", 1.0: "light", 2.0: "rem", 3.0: "awake"}


def test_parse_sleep_session_maps_all_fields():
    raw = _load_fixture("get_sleep_data")
    session = GarminProvider._parse_sleep_session(raw, source_id_prefix="garmin")
    assert session is not None
    assert session.id == "garmin:2026-09-15"
    assert session.calendar_date == date(2026, 9, 15)
    assert session.total_sleep_seconds == 5460.0
    assert session.deep_sleep_seconds == 3120.0
    assert session.overall_score == 76.0
    assert session.overall_score_qualifier == "FAIR"
    assert session.duration_qualifier == "FAIR"
    assert session.stress_qualifier == "POOR"
    assert session.rem_percentage == 0.0
    assert session.restless_moments_count == 2
    assert session.avg_overnight_hrv == 26.0
    assert session.sleep_need_baseline_minutes == 470
    assert session.sleep_need_actual_minutes == 500
    assert session.score_feedback == "NEGATIVE_NOT_RESTORATIVE"


def test_parse_sleep_session_returns_none_when_no_sleep_recorded():
    assert GarminProvider._parse_sleep_session({}, source_id_prefix="garmin") is None
    assert GarminProvider._parse_sleep_session({"dailySleepDTO": {}}, source_id_prefix="garmin") is None


def test_parse_sleep_stage_segments():
    raw = _load_fixture("get_sleep_data")
    segments = GarminProvider._parse_sleep_stage_segments(raw, session_id="garmin:2026-09-15")
    assert len(segments) == 4
    assert [s.stage for s in segments] == ["light", "deep", "light", "awake"]
    assert segments[0].id == "garmin:2026-09-15:0"
    assert segments[1].duration_seconds == 3120.0  # 04:39:52 - 03:47:52


def test_parse_sleep_stage_segments_handles_non_zero_fractional_seconds():
    """Regression test: a naive `.replace(".0", "")` substring strip (as a
    previous version of this parser used to normalize Garmin's trailing
    fractional-seconds artifact) corrupts any startGMT/endGMT whose
    fraction isn't exactly ".0" -- e.g. ".05" becomes "5" appended to the
    seconds digit, producing an invalid isoformat string. Python 3.11+'s
    datetime.fromisoformat natively parses these fractional-second
    timestamps directly, so no preprocessing is needed or safe to do."""
    raw = {
        "sleepLevels": [
            {"startGMT": "2026-09-15T03:46:52.05", "endGMT": "2026-09-15T03:47:52.0", "activityLevel": 1.0},
        ]
    }
    segments = GarminProvider._parse_sleep_stage_segments(raw, session_id="garmin:2026-09-15")
    assert len(segments) == 1
    assert segments[0].start_utc == datetime(2026, 9, 15, 3, 46, 52, 50000)
    assert segments[0].start_utc.microsecond == 50000


def test_stage_segment_sums_match_daily_sleep_dto_totals():
    """Operationalizes the STAGE_CODE_TO_NAME mapping's validation (spec
    Section 2): summing sleepLevels segments grouped by stage must equal
    dailySleepDTO's own per-stage second totals for the same night. This
    fixture was deliberately built so its dailySleepDTO totals equal the
    sum of its own (trimmed, 4-segment) sleepLevels list -- unlike real
    Garmin data, which satisfies this by construction across its full
    ~14-segment night, a hand-built fixture only does if built carefully
    (this one previously didn't, until corrected while writing this plan)."""
    raw = _load_fixture("get_sleep_data")
    segments = GarminProvider._parse_sleep_stage_segments(raw, session_id="garmin:2026-09-15")
    summed = {}
    for s in segments:
        summed[s.stage] = summed.get(s.stage, 0.0) + s.duration_seconds

    dto = raw["dailySleepDTO"]
    assert summed.get("deep", 0.0) == dto["deepSleepSeconds"]
    assert summed.get("light", 0.0) == dto["lightSleepSeconds"]
    assert summed.get("rem", 0.0) == dto["remSleepSeconds"]
    assert summed.get("awake", 0.0) == dto["awakeSleepSeconds"]


def test_parse_sleep_restless_moments():
    raw = _load_fixture("get_sleep_data")
    moments = GarminProvider._parse_sleep_restless_moments(raw, session_id="garmin:2026-09-15")
    assert len(moments) == 2
    assert moments[0].value == 1
    assert moments[0].sleep_session_id == "garmin:2026-09-15"


def test_hydrate_recent_sleep_persists_session_and_children(tmp_path):
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    fixture = _load_fixture("get_sleep_data")

    class _SleepDetailClient(_StubGarminClient):
        def get_sleep_data(self, date_str):
            return fixture if date_str == "2026-09-15" else {}

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_SleepDetailClient)

    result = provider.hydrate_recent_sleep(conn, since=date(2026, 9, 15), until=date(2026, 9, 15))
    assert result == {"nights": 1, "segments": 4, "restless_moments": 2}
    assert repository.get_sleep_session(conn, "garmin:2026-09-15") is not None
    assert len(repository.get_sleep_stage_segments(conn, "garmin:2026-09-15")) == 4


def test_hydrate_recent_sleep_skips_day_with_no_sleep_recorded(tmp_path):
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})

    class _NoSleepClient(_StubGarminClient):
        def get_sleep_data(self, date_str):
            return {}  # matches the real "no sleep recorded" case (3/30 nights observed live)

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_NoSleepClient)
    result = provider.hydrate_recent_sleep(conn, since=date(2026, 9, 15), until=date(2026, 9, 15))
    assert result == {"nights": 0, "segments": 0, "restless_moments": 0}


def test_hydrate_recent_sleep_isolates_per_day_failures(tmp_path):
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    fixture_14 = dict(_load_fixture("get_sleep_data"))
    fixture_14["dailySleepDTO"] = dict(fixture_14["dailySleepDTO"])
    fixture_14["dailySleepDTO"]["calendarDate"] = "2026-09-14"

    class _OneBadDayClient(_StubGarminClient):
        def get_sleep_data(self, date_str):
            if date_str == "2026-09-14":
                return fixture_14
            raise RuntimeError("simulated malformed response")

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_OneBadDayClient)
    result = provider.hydrate_recent_sleep(conn, since=date(2026, 9, 14), until=date(2026, 9, 15))
    assert result["nights"] == 1  # the bad day (09-15) is skipped, not fatal
    assert repository.get_sleep_session(conn, "garmin:2026-09-14") is not None
    assert repository.get_sleep_session(conn, "garmin:2026-09-15") is None


def test_hydrate_recent_sleep_replaces_not_merges_on_rehydration(tmp_path):
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    state = {"raw": _load_fixture("get_sleep_data")}

    class _MutableSleepClient(_StubGarminClient):
        def get_sleep_data(self, date_str):
            return state["raw"] if date_str == "2026-09-15" else {}

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_MutableSleepClient)
    provider.hydrate_recent_sleep(conn, since=date(2026, 9, 15), until=date(2026, 9, 15))
    assert len(repository.get_sleep_stage_segments(conn, "garmin:2026-09-15")) == 4

    # simulate Garmin reclassifying the night with fewer segments on a second pull
    reclassified = dict(_load_fixture("get_sleep_data"))
    reclassified["sleepLevels"] = reclassified["sleepLevels"][:1]
    state["raw"] = reclassified
    provider.hydrate_recent_sleep(conn, since=date(2026, 9, 15), until=date(2026, 9, 15))
    assert len(repository.get_sleep_stage_segments(conn, "garmin:2026-09-15")) == 1


def test_sync_hydration_skips_on_force_full_history(tmp_path):
    """Regression guard for the sync_garmin_data/perform_sync_pass wiring:
    a full-history resync must not call hydrate_recent_sleep at all (years
    of per-night detail calls is out of proportion to what a metric
    backfill needs) and must leave the sleep-detail checkpoint untouched."""
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})

    class _ExplodingClient(_StubGarminClient):
        def get_sleep_data(self, date_str):
            raise AssertionError("must not be called on force_full_history=True")

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_ExplodingClient)
    result = provider.sync_hydration(conn, date(2026, 9, 1), date(2026, 9, 15), True)

    assert result == "skipped (full history sync)"
    assert repository.get_checkpoint(conn, "garmin", "garmin_sleep_detail") is None


def test_sync_hydration_hydrates_and_advances_checkpoint_with_no_prior_checkpoint(tmp_path):
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    fixture = _load_fixture("get_sleep_data")

    class _SleepDetailClient(_StubGarminClient):
        def get_sleep_data(self, date_str):
            return fixture if date_str == "2026-09-15" else {}

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_SleepDetailClient)
    result = provider.sync_hydration(conn, date(2026, 9, 15), date(2026, 9, 15), False)

    assert result == "1 night (4 stage segments, 2 restless moments)"
    assert repository.get_checkpoint(conn, "garmin", "garmin_sleep_detail") == date(2026, 9, 15)


def test_sync_hydration_uses_grace_days_window_from_existing_checkpoint(tmp_path):
    """Regression: unlike Tonal's sync_hydration (which only re-walks the
    checkpoint day itself), Garmin sleep summaries can finalize a day or
    two after the fact, so sync_hydration must re-walk a trailing
    SYNC_RESYNC_GRACE_DAYS window behind the existing checkpoint -- not
    just resume from the checkpoint day. Captures the `since` hydrate_recent_sleep
    actually receives to verify the grace-days math directly, rather than
    just asserting on hydrate_recent_sleep's return value."""
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    checkpoint_day = date(2026, 9, 10)
    repository.set_checkpoint(conn, "garmin", "garmin_sleep_detail", checkpoint_day)

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)
    captured = {}

    def _capturing_hydrate(conn, since, until=None):
        captured["since"] = since
        return {"nights": 0, "segments": 0, "restless_moments": 0}

    provider.hydrate_recent_sleep = _capturing_hydrate

    provider.sync_hydration(conn, date(2026, 8, 1), date(2026, 9, 15), False)

    assert captured["since"] == checkpoint_day - timedelta(days=SYNC_RESYNC_GRACE_DAYS)


def test_sync_hydration_clamps_grace_days_window_to_start_date(tmp_path):
    """If checkpoint - SYNC_RESYNC_GRACE_DAYS would land before start_date
    (a checkpoint from very early in the requested window), hydrate_since
    must clamp to start_date rather than walking earlier than the sync's
    own requested range."""
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    start_date = date(2026, 9, 1)
    checkpoint_day = date(2026, 9, 2)  # checkpoint_day - grace_days(3) = 2026-08-30, before start_date
    repository.set_checkpoint(conn, "garmin", "garmin_sleep_detail", checkpoint_day)

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)
    captured = {}

    def _capturing_hydrate(conn, since, until=None):
        captured["since"] = since
        return {"nights": 0, "segments": 0, "restless_moments": 0}

    provider.hydrate_recent_sleep = _capturing_hydrate

    provider.sync_hydration(conn, start_date, date(2026, 9, 15), False)

    assert captured["since"] == start_date


def test_sync_hydration_caps_first_run_backfill_to_30_days(tmp_path):
    """Critical regression guard: app.sync's BACKFILL_LOOKBACK_DAYS is 3650
    days, and with no garmin_sleep_detail checkpoint yet (a brand-new
    connection's first background sync), hydrate_since used to collapse to
    that 10-year-old start_date -- hydrate_recent_sleep's day-by-day loop
    would then issue ~3651 sequential get_sleep_data() calls, and if that
    trips a rate limit partway through, the checkpoint is deliberately left
    untouched (so a transient failure can retry), meaning the *next* sync
    restarts from day 1 and fails again at roughly the same point: a
    permanent livelock. sync_hydration must clamp hydrate_since to
    SLEEP_HYDRATION_MAX_BACKFILL_DAYS before start_date, even when
    start_date reaches far into the past and there is no checkpoint to
    otherwise bound it. Captures the actual `since` hydrate_recent_sleep
    receives, same pattern as the existing grace-days tests above."""
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})
    far_past_start = date(2016, 9, 15)  # ~3650 days before end_date below
    end_date = date(2026, 9, 15)

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)
    captured = {}

    def _capturing_hydrate(conn, since, until=None):
        captured["since"] = since
        return {"nights": 0, "segments": 0, "restless_moments": 0}

    provider.hydrate_recent_sleep = _capturing_hydrate

    provider.sync_hydration(conn, far_past_start, end_date, False)

    assert captured["since"] == end_date - timedelta(days=SLEEP_HYDRATION_MAX_BACKFILL_DAYS)
    assert captured["since"] != far_past_start


def test_sync_hydration_isolates_failure_and_leaves_checkpoint_untouched(tmp_path):
    conn = connect(tmp_path / "test.db")
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "x"})

    provider = GarminProvider(store, tmp_path / "tokens", garmin_client_factory=_StubGarminClient)

    def _raising_hydrate(conn, since, until=None):
        raise RuntimeError("simulated rate limit error")

    provider.hydrate_recent_sleep = _raising_hydrate

    result = provider.sync_hydration(conn, date(2026, 9, 1), date(2026, 9, 15), False)

    # sync_all_metrics's already-successful results must survive a
    # hydration failure, not be discarded by a propagating exception.
    assert "hydration failed" in result
    assert "simulated rate limit error" in result
    # The checkpoint must not advance past a failed hydration.
    assert repository.get_checkpoint(conn, "garmin", "garmin_sleep_detail") is None
