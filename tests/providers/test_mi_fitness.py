import asyncio
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet

from core.providers.base import RateLimitError
from core.providers.mi_fitness import (
    MI_FITNESS_METRIC_TYPES,
    MiFitnessAuthError,
    MiFitnessProvider,
    _classify_exception,
    save_mi_fitness_session,
)
from core.security.credentials import CredentialStore


def test_metric_types_use_existing_naming_conventions():
    assert MI_FITNESS_METRIC_TYPES == ["steps", "daily_distance", "active_calories", "resting_hr", "sleep_score"]
    # If Task 1's inventory determined SleepData has only duration (no
    # score), replace "sleep_score" with "sleep_duration" here and in the
    # implementation -- do not ship both.


def test_classify_token_expired_as_auth_error():
    from mi_fitness.exceptions import TokenExpiredError

    result = _classify_exception(TokenExpiredError("session expired"))

    assert isinstance(result, MiFitnessAuthError)


def test_classify_auth_error_as_auth_error():
    from mi_fitness.exceptions import AuthError

    result = _classify_exception(AuthError("bad login"))

    assert isinstance(result, MiFitnessAuthError)


def test_classify_rate_limited_api_error_as_rate_limit_error():
    from mi_fitness.exceptions import APIError

    exc = APIError("rate limited", status_code=429, response_body="{}")

    result = _classify_exception(exc)

    assert isinstance(result, RateLimitError)


def test_classify_non_rate_limit_api_error_passes_through_unchanged():
    from mi_fitness.exceptions import APIError

    exc = APIError("server error", status_code=500, response_body="{}")

    result = _classify_exception(exc)

    assert result is exc


def test_classify_unrelated_exception_passes_through_unchanged():
    exc = ValueError("some unrelated bug")

    result = _classify_exception(exc)

    assert result is exc


class _FakeMiClient:
    def __init__(self, uid):
        self.uid = uid
        self.closed = False

    async def get_relatives(self):
        return []


@asynccontextmanager
async def _fake_client_factory_ok(token_path):
    with open(token_path) as f:
        content = f.read()
    assert content == "fake-token-content"
    client = _FakeMiClient(uid="123")
    try:
        yield client
    finally:
        client.closed = True


@asynccontextmanager
async def _fake_client_factory_raises(token_path):
    from mi_fitness.exceptions import TokenExpiredError

    raise TokenExpiredError("session expired")
    yield  # pragma: no cover -- unreachable, makes this a generator


class _FakeMiClientVerifyFails:
    """Fake client whose __aenter__ succeeds (no I/O, like the real
    MiHealthClient) but whose get_relatives() -- the real, cheap,
    authenticated call _verify_session now makes -- raises, simulating a
    genuinely dead/expired session that only surfaces on an actual call."""

    async def get_relatives(self):
        from mi_fitness.exceptions import TokenExpiredError

        raise TokenExpiredError("session expired mid-verify")


@asynccontextmanager
async def _fake_client_factory_verify_fails(token_path):
    yield _FakeMiClientVerifyFails()


def test_save_mi_fitness_session_persists_token_content_and_uid(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "mi_fitness_credentials.enc")

    save_mi_fitness_session(store, token_file_content="fake-token-content", uid="123")

    assert store.load() == {"token_file_content": "fake-token-content", "uid": "123"}


def test_mi_fitness_provider_raises_auth_error_when_no_credentials(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")

    with pytest.raises(MiFitnessAuthError):
        MiFitnessProvider(store)


def test_mi_fitness_provider_constructs_client_from_stored_token(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    save_mi_fitness_session(store, token_file_content="fake-token-content", uid="123")

    provider = MiFitnessProvider(store, client_factory=_fake_client_factory_ok, run_async=asyncio.run)

    assert provider.name == "mi_fitness"


def test_mi_fitness_provider_raises_auth_error_when_stored_session_expired(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    save_mi_fitness_session(store, token_file_content="fake-token-content", uid="123")

    with pytest.raises(MiFitnessAuthError):
        MiFitnessProvider(store, client_factory=_fake_client_factory_raises, run_async=asyncio.run)


def test_mi_fitness_provider_raises_auth_error_when_verify_call_fails(tmp_path):
    """_verify_session must make a real authenticated call (not just enter
    the async context manager) so a genuinely dead session is caught at
    construction time, not silently accepted."""
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    save_mi_fitness_session(store, token_file_content="fake-token-content", uid="123")

    with pytest.raises(MiFitnessAuthError):
        MiFitnessProvider(store, client_factory=_fake_client_factory_verify_fails, run_async=asyncio.run)


def test_mi_fitness_provider_raises_auth_error_for_non_numeric_uid(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    save_mi_fitness_session(store, token_file_content="fake-token-content", uid="not-a-number")

    with pytest.raises(MiFitnessAuthError):
        MiFitnessProvider(store, client_factory=_fake_client_factory_ok, run_async=asyncio.run)


def test_mi_fitness_provider_coerces_stored_uid_to_int(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    save_mi_fitness_session(store, token_file_content="fake-token-content", uid="123")

    provider = MiFitnessProvider(store, client_factory=_fake_client_factory_ok, run_async=asyncio.run)

    assert provider._uid == 123
    assert isinstance(provider._uid, int)


from datetime import date, datetime, time

from core.storage.models import MetricReading


class _FakeStepData:
    def __init__(self, steps, distance):
        self.steps = steps
        self.distance = distance


class _FakeHeartRateData:
    def __init__(self, avg_rhr):
        self.avg_rhr = avg_rhr


class _FakeSleepData:
    def __init__(self, sleep_score):
        self.sleep_score = sleep_score


class _FakeCaloriesData:
    def __init__(self, calories):
        self.calories = calories


class _RecordingMiClient:
    """Records which (method, date) calls were made and returns
    pre-programmed per-day responses, so tests can assert the day-by-day
    iteration is correct without a real event loop per data type."""

    def __init__(self, steps_by_date=None, heart_rate_by_date=None, sleep_by_date=None, calories_by_date=None):
        self.uid = "123"
        self.calls = []
        self._steps_by_date = steps_by_date or {}
        self._heart_rate_by_date = heart_rate_by_date or {}
        self._sleep_by_date = sleep_by_date or {}
        self._calories_by_date = calories_by_date or {}

    async def get_steps(self, uid, day, days=1):
        self.calls.append(("get_steps", day))
        return self._steps_by_date.get(day, [])

    async def get_heart_rate(self, uid, day, days=1):
        self.calls.append(("get_heart_rate", day))
        return self._heart_rate_by_date.get(day, [])

    async def get_sleep(self, uid, day, days=1):
        self.calls.append(("get_sleep", day))
        return self._sleep_by_date.get(day, [])

    async def get_calories_history(self, uid, day, days=1):
        self.calls.append(("get_calories_history", day))
        return self._calories_by_date.get(day, [])

    async def get_relatives(self):
        # _verify_session calls this once at construction time; recorded
        # like everything else so tests can assert it happened.
        self.calls.append(("get_relatives", None))
        return []


def _provider_with_client(tmp_path, client, sleep_calls=None):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    save_mi_fitness_session(store, token_file_content="fake-token-content", uid="123")

    @asynccontextmanager
    async def factory(token_path):
        yield client

    provider = MiFitnessProvider(store, client_factory=factory, run_async=asyncio.run)
    if sleep_calls is not None:
        provider._sleep_fn = lambda seconds: sleep_calls.append(seconds)
    return provider


def test_fetch_steps_sums_each_days_list_into_one_daily_total(tmp_path):
    client = _RecordingMiClient(steps_by_date={
        date(2026, 1, 1): [_FakeStepData(steps=3000, distance=2100), _FakeStepData(steps=1000, distance=700)],
        date(2026, 1, 2): [_FakeStepData(steps=5000, distance=3500)],
    })
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 2))

    assert len(readings) == 2
    by_date = {r.timestamp.date(): r.value for r in readings}
    assert by_date == {date(2026, 1, 1): 4000.0, date(2026, 1, 2): 5000.0}
    assert all(r.timestamp == datetime.combine(r.timestamp.date(), time.min) for r in readings)
    assert all(r.source == "mi_fitness" and r.metric_type == "steps" and r.unit == "steps" for r in readings)


def test_fetch_daily_distance_sums_meters_and_converts_to_km(tmp_path):
    client = _RecordingMiClient(steps_by_date={
        date(2026, 1, 1): [_FakeStepData(steps=3000, distance=2100)],
    })
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("daily_distance", date(2026, 1, 1), date(2026, 1, 1))

    assert len(readings) == 1
    assert readings[0].value == pytest.approx(2.1)
    assert readings[0].unit == "km"


def test_fetch_resting_hr_averages_nonzero_avg_rhr_readings(tmp_path):
    client = _RecordingMiClient(heart_rate_by_date={
        date(2026, 1, 1): [_FakeHeartRateData(avg_rhr=52), _FakeHeartRateData(avg_rhr=0), _FakeHeartRateData(avg_rhr=58)],
    })
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("resting_hr", date(2026, 1, 1), date(2026, 1, 1))

    assert len(readings) == 1
    assert readings[0].value == 55.0  # (52 + 58) / 2, the 0 entry excluded
    assert readings[0].unit == "bpm"


def test_fetch_resting_hr_skips_day_when_all_readings_are_zero(tmp_path):
    client = _RecordingMiClient(heart_rate_by_date={
        date(2026, 1, 1): [_FakeHeartRateData(avg_rhr=0)],
    })
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("resting_hr", date(2026, 1, 1), date(2026, 1, 1))

    assert readings == []


def test_fetch_active_calories_sums_via_calories_history(tmp_path):
    client = _RecordingMiClient(calories_by_date={
        date(2026, 1, 1): [_FakeCaloriesData(calories=1800)],
    })
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("active_calories", date(2026, 1, 1), date(2026, 1, 1))

    assert len(readings) == 1
    assert readings[0].value == 1800.0
    assert readings[0].unit == "kcal"
    assert ("get_calories_history", date(2026, 1, 1)) in client.calls
    assert not any(call[0] == "get_calories" for call in client.calls)  # never calls the date-less method


def test_fetch_sleep_score_uses_first_entrys_score(tmp_path):
    client = _RecordingMiClient(sleep_by_date={
        date(2026, 1, 1): [_FakeSleepData(sleep_score=82)],
    })
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("sleep_score", date(2026, 1, 1), date(2026, 1, 1))

    assert len(readings) == 1
    assert readings[0].value == 82.0
    assert readings[0].unit == "score"


def test_fetch_sleep_score_skips_day_when_score_is_zero(tmp_path):
    """sleep_score defaults to 0 when mi_fitness has no computed score for
    that day (same "0 means unshared/absent" semantics as
    HeartRateData.avg_rhr) -- a real score is 1-100, so a 0 must not be
    stored as if it were a genuine reading."""
    client = _RecordingMiClient(sleep_by_date={
        date(2026, 1, 1): [_FakeSleepData(sleep_score=0)],
    })
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("sleep_score", date(2026, 1, 1), date(2026, 1, 1))

    assert readings == []


def test_fetch_skips_days_with_no_data(tmp_path):
    client = _RecordingMiClient(steps_by_date={date(2026, 1, 2): [_FakeStepData(steps=100, distance=70)]})
    provider = _provider_with_client(tmp_path, client)

    readings = provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 2))

    assert len(readings) == 1
    assert readings[0].timestamp.date() == date(2026, 1, 2)


def test_fetch_paces_between_per_day_calls(tmp_path):
    client = _RecordingMiClient(steps_by_date={
        date(2026, 1, 1): [_FakeStepData(steps=1, distance=1)],
        date(2026, 1, 2): [_FakeStepData(steps=1, distance=1)],
        date(2026, 1, 3): [_FakeStepData(steps=1, distance=1)],
    })
    sleep_calls = []
    provider = _provider_with_client(tmp_path, client, sleep_calls=sleep_calls)

    provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 3))

    assert len(sleep_calls) == 2  # paced between the 3 calls, not after the last one


def test_fetch_raises_value_error_for_unsupported_metric_type(tmp_path):
    provider = _provider_with_client(tmp_path, _RecordingMiClient())

    with pytest.raises(ValueError):
        provider.fetch("not_a_real_metric", date(2026, 1, 1), date(2026, 1, 1))


def test_fetch_classifies_library_exceptions(tmp_path):
    from mi_fitness.exceptions import TokenExpiredError

    class _FailingClient(_RecordingMiClient):
        async def get_steps(self, uid, day, days=1):
            raise TokenExpiredError("expired mid-sync")

    provider = _provider_with_client(tmp_path, _FailingClient())

    with pytest.raises(MiFitnessAuthError):
        provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 1))


def test_fetch_persists_session_token_refreshed_mid_call(tmp_path):
    """mi_fitness can auto-refresh an expiring session token during a real
    API call, writing the refreshed token back to whatever file path the
    client was constructed from. That refresh must survive past the temp
    file's deletion by being written into CredentialStore, not silently
    discarded."""
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    save_mi_fitness_session(store, token_file_content="original-token-content", uid="123")

    client = _RecordingMiClient(steps_by_date={
        date(2026, 1, 1): [_FakeStepData(steps=100, distance=70)],
    })

    @asynccontextmanager
    async def refreshing_factory(token_path):
        # Simulate the library refreshing the on-disk token mid-call, the
        # way it would on a real, close-to-expiring session.
        with open(token_path, "w") as f:
            f.write("refreshed-token-content")
        yield client

    provider = MiFitnessProvider(store, client_factory=refreshing_factory, run_async=asyncio.run)

    provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 1))

    assert store.load() == {"token_file_content": "refreshed-token-content", "uid": "123"}
    assert provider._token_file_content == "refreshed-token-content"
