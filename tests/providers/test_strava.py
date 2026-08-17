from datetime import date as date_cls, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet

from core.providers.strava import StravaAuthError, exchange_code_for_tokens, refresh_access_token
from core.security.credentials import CredentialStore


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(base_url="https://www.strava.com", transport=httpx.MockTransport(handler))


def test_exchange_code_for_tokens_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        body = dict(x.split("=") for x in request.content.decode().split("&"))
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "auth-code-123"
        return httpx.Response(200, json={
            "token_type": "Bearer",
            "expires_at": 1893456000,
            "expires_in": 21600,
            "refresh_token": "new-refresh-token",
            "access_token": "new-access-token",
        })

    client = _mock_client(handler)
    result = exchange_code_for_tokens("12345", "client-secret", "auth-code-123", client)

    assert result == {
        "client_id": "12345",
        "client_secret": "client-secret",
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_at": "1893456000",
    }


def test_exchange_code_for_tokens_raises_on_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Bad Request", "errors": []})

    client = _mock_client(handler)
    with pytest.raises(StravaAuthError):
        exchange_code_for_tokens("12345", "client-secret", "bad-code", client)


def test_refresh_access_token_success():
    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(x.split("=") for x in request.content.decode().split("&"))
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "old-refresh-token"
        return httpx.Response(200, json={
            "token_type": "Bearer",
            "access_token": "refreshed-access-token",
            "expires_at": 1893456500,
            "expires_in": 21600,
            "refresh_token": "rotated-refresh-token",
        })

    client = _mock_client(handler)
    result = refresh_access_token("12345", "client-secret", "old-refresh-token", client)

    assert result["access_token"] == "refreshed-access-token"
    assert result["refresh_token"] == "rotated-refresh-token"
    assert result["expires_at"] == "1893456500"


def test_refresh_access_token_raises_on_revoked_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    client = _mock_client(handler)
    with pytest.raises(StravaAuthError):
        refresh_access_token("12345", "client-secret", "revoked-refresh-token", client)


def _credential_store(tmp_path, credentials):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "strava_credentials.enc")
    store.save(credentials)
    return store


def _valid_credentials(expires_at: int) -> dict[str, str]:
    return {
        "client_id": "12345",
        "client_secret": "client-secret",
        "access_token": "current-access-token",
        "refresh_token": "current-refresh-token",
        "expires_at": str(expires_at),
    }


def test_strava_provider_uses_stored_token_when_not_expired(tmp_path):
    from core.providers.strava import StravaProvider

    store = _credential_store(tmp_path, _valid_credentials(expires_at=9999999999))
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("should not call the token endpoint when not expired")

    client = httpx.Client(base_url="https://www.strava.com", transport=httpx.MockTransport(handler))
    provider = StravaProvider(store, http_client=client, now_fn=lambda: 1000000000.0)

    assert provider._access_token == "current-access-token"
    assert calls == []


def test_strava_provider_refreshes_when_expired(tmp_path):
    from core.providers.strava import StravaProvider

    store = _credential_store(tmp_path, _valid_credentials(expires_at=1000000000))

    def handler(request):
        return httpx.Response(200, json={
            "token_type": "Bearer", "access_token": "refreshed-token",
            "refresh_token": "rotated-refresh-token", "expires_at": 2000000000, "expires_in": 21600,
        })

    client = httpx.Client(base_url="https://www.strava.com", transport=httpx.MockTransport(handler))
    provider = StravaProvider(store, http_client=client, now_fn=lambda: 1500000000.0)

    assert provider._access_token == "refreshed-token"
    assert store.load()["access_token"] == "refreshed-token"
    assert store.load()["refresh_token"] == "rotated-refresh-token"


def test_strava_provider_raises_when_no_credentials(tmp_path):
    from core.providers.strava import StravaProvider

    empty_store = CredentialStore(Fernet.generate_key(), tmp_path / "strava_credentials.enc")

    with pytest.raises(StravaAuthError):
        StravaProvider(empty_store)


def _raw_activity(activity_id, start_date, **overrides):
    base = {
        "id": activity_id,
        "name": "Morning Run",
        "distance": 5000.0,
        "moving_time": 1800,
        "elapsed_time": 1850,
        "total_elevation_gain": 42.0,
        "type": "Run",
        "sport_type": "Run",
        "start_date": start_date,
        "start_date_local": start_date,
        "average_heartrate": 145.0,
        "max_heartrate": 172.0,
        "average_speed": 2.78,
        "max_speed": 3.9,
        "kilojoules": None,
        "calories": None,
    }
    base.update(overrides)
    return base


def _provider_with_activities(tmp_path, pages: list[list[dict]]):
    from core.providers.strava import StravaProvider

    store = _credential_store(tmp_path, _valid_credentials(expires_at=9999999999))
    call_log = []

    def handler(request):
        call_log.append(request)
        page = int(dict(p.split("=") for p in request.url.query.decode().split("&"))["page"])
        body = pages[page - 1] if page <= len(pages) else []
        return httpx.Response(200, json=body)

    client = httpx.Client(base_url="https://www.strava.com", transport=httpx.MockTransport(handler))
    provider = StravaProvider(store, http_client=client, now_fn=lambda: 1000000000.0)
    return provider, call_log


def test_fetch_activities_parses_fields(tmp_path):
    raw = [_raw_activity(111, "2026-01-01T07:00:00Z")]
    provider, _ = _provider_with_activities(tmp_path, [raw])

    activities = provider.fetch_activities(date_cls(2026, 1, 1), date_cls(2026, 1, 1))

    assert len(activities) == 1
    a = activities[0]
    assert a.id == "strava:111"
    assert a.source == "strava"
    assert a.activity_type == "running"
    assert a.duration_seconds == 1800.0
    assert a.distance_meters == 5000.0
    assert a.avg_hr == 145.0
    assert a.max_hr == 172.0
    assert a.elevation_gain == 42.0
    assert a.elevation_loss is None
    assert a.start_time == datetime(2026, 1, 1, 7, 0)


def test_fetch_activities_falls_back_to_kilojoules_for_calories(tmp_path):
    raw = [_raw_activity(111, "2026-01-01T07:00:00Z", kilojoules=1200.0)]
    provider, _ = _provider_with_activities(tmp_path, [raw])

    activities = provider.fetch_activities(date_cls(2026, 1, 1), date_cls(2026, 1, 1))

    assert activities[0].calories == pytest.approx(1200.0 * 0.239006, rel=1e-3)


def test_fetch_activities_paginates_until_short_page(tmp_path):
    full_page = [_raw_activity(i, "2026-01-01T07:00:00Z") for i in range(200)]
    short_page = [_raw_activity(999, "2026-01-02T07:00:00Z")]
    provider, call_log = _provider_with_activities(tmp_path, [full_page, short_page])

    activities = provider.fetch_activities(date_cls(2026, 1, 1), date_cls(2026, 1, 2))

    assert len(activities) == 201
    assert len(call_log) == 2


def test_fetch_activities_caches_per_instance(tmp_path):
    raw = [_raw_activity(111, "2026-01-01T07:00:00Z")]
    provider, call_log = _provider_with_activities(tmp_path, [raw])

    provider.fetch_activities(date_cls(2026, 1, 1), date_cls(2026, 1, 1))
    provider.fetch_activities(date_cls(2026, 1, 1), date_cls(2026, 1, 1))

    assert len(call_log) == 1


def test_supported_metric_types():
    from core.providers.strava import STRAVA_METRIC_TYPES

    assert STRAVA_METRIC_TYPES == ["activity_duration", "activity_distance", "activity_calories"]


def test_fetch_activity_duration(tmp_path):
    raw = [_raw_activity(111, "2026-01-01T07:00:00Z", moving_time=1800)]
    provider, _ = _provider_with_activities(tmp_path, [raw])

    readings = provider.fetch("activity_duration", date_cls(2026, 1, 1), date_cls(2026, 1, 1))

    assert len(readings) == 1
    assert readings[0].source == "strava"
    assert readings[0].metric_type == "activity_duration"
    assert readings[0].value == 30.0  # minutes
    assert readings[0].unit == "minutes"


def test_fetch_activity_distance(tmp_path):
    raw = [_raw_activity(111, "2026-01-01T07:00:00Z", distance=5000.0)]
    provider, _ = _provider_with_activities(tmp_path, [raw])

    readings = provider.fetch("activity_distance", date_cls(2026, 1, 1), date_cls(2026, 1, 1))

    assert readings[0].value == 5.0  # km
    assert readings[0].unit == "km"


def test_fetch_activity_calories_skips_activities_without_calorie_data(tmp_path):
    raw = [_raw_activity(111, "2026-01-01T07:00:00Z", kilojoules=None, calories=None)]
    provider, _ = _provider_with_activities(tmp_path, [raw])

    readings = provider.fetch("activity_calories", date_cls(2026, 1, 1), date_cls(2026, 1, 1))

    assert readings == []


def test_fetch_unsupported_metric_type_raises(tmp_path):
    provider, _ = _provider_with_activities(tmp_path, [[]])

    with pytest.raises(ValueError):
        provider.fetch("resting_hr", date_cls(2026, 1, 1), date_cls(2026, 1, 1))


def test_call_proactively_backs_off_when_rate_limit_usage_high(tmp_path):
    from core.providers.strava import StravaProvider

    store = _credential_store(tmp_path, _valid_credentials(expires_at=9999999999))
    call_count = [0]

    def handler(request):
        call_count[0] += 1
        headers = {"X-ReadRateLimit-Usage": "95,500", "X-ReadRateLimit-Limit": "100,1000"}
        return httpx.Response(200, json=[], headers=headers)

    client = httpx.Client(base_url="https://www.strava.com", transport=httpx.MockTransport(handler))
    sleep_calls = []
    provider = StravaProvider(
        store, http_client=client, now_fn=lambda: 1000000000.0, sleep_fn=lambda s: sleep_calls.append(s)
    )

    provider._call("GET", "/api/v3/athlete/activities", params={"page": 1, "per_page": 200})
    assert sleep_calls == []  # no prior usage recorded yet, first call goes through immediately

    provider._call("GET", "/api/v3/athlete/activities", params={"page": 1, "per_page": 200})
    assert sleep_calls == [60.0]  # previous response's 95/100 usage ratio triggers backoff before this call


def test_call_does_not_back_off_when_usage_low(tmp_path):
    from core.providers.strava import StravaProvider

    store = _credential_store(tmp_path, _valid_credentials(expires_at=9999999999))

    def handler(request):
        headers = {"X-ReadRateLimit-Usage": "5,50", "X-ReadRateLimit-Limit": "100,1000"}
        return httpx.Response(200, json=[], headers=headers)

    client = httpx.Client(base_url="https://www.strava.com", transport=httpx.MockTransport(handler))
    sleep_calls = []
    provider = StravaProvider(
        store, http_client=client, now_fn=lambda: 1000000000.0, sleep_fn=lambda s: sleep_calls.append(s)
    )

    provider._call("GET", "/api/v3/athlete/activities", params={"page": 1, "per_page": 200})
    provider._call("GET", "/api/v3/athlete/activities", params={"page": 1, "per_page": 200})

    assert sleep_calls == []
