import base64
import json
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from core.providers.base import RateLimitError
from core.providers.tonal_client import TonalAuthError, TonalClient, expand_blocks
from core.security.credentials import CredentialStore

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "tonal"


def _load_fixture(filename: str):
    return json.loads((FIXTURE_DIR / filename).read_text())


def _fake_jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def _credential_store(tmp_path, credentials=None):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "tonal_credentials.enc")
    if credentials is not None:
        store.save(credentials)
    return store


def _ready_client(tmp_path, api_handler, now_fn=None):
    """Build a TonalClient with a cached, unexpired token and user_id, wired to
    api_handler for all /v6 requests. Auth0 calls raise if reached, matching
    the existing tests' convention of asserting no unnecessary relogin."""
    id_token = _fake_jwt(int(time.time()) + 3600)
    store = _credential_store(tmp_path, {
        "email": "a@example.com", "password": "x", "id_token": id_token,
        "refresh_token": "rt-1", "expires_at": str(int(time.time()) + 3600), "user_id": "user-123",
    })

    def auth_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call Auth0 when cached token is still valid")

    auth_client = httpx.Client(base_url="https://tonal.auth0.com", transport=httpx.MockTransport(auth_handler))
    api_client = httpx.Client(base_url="https://api.tonal.com/v6", transport=httpx.MockTransport(api_handler))
    kwargs = {"now_fn": now_fn} if now_fn is not None else {}
    return TonalClient(store, http_client=api_client, auth_http_client=auth_client, **kwargs)


def test_init_raises_tonal_auth_error_when_no_credentials_saved(tmp_path):
    store = _credential_store(tmp_path)

    with pytest.raises(TonalAuthError, match="no Tonal credentials"):
        TonalClient(store)


def test_init_password_login_stores_tokens_and_user_id(tmp_path):
    store = _credential_store(tmp_path, {"email": "a@example.com", "password": "hunter2"})
    id_token = _fake_jwt(int(time.time()) + 3600)

    def auth_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        body = json.loads(request.content)
        assert body["client_id"] == "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
        assert body["grant_type"] == "password"
        return httpx.Response(200, json={"id_token": id_token, "refresh_token": "rt-1"})

    auth_client = httpx.Client(base_url="https://tonal.auth0.com", transport=httpx.MockTransport(auth_handler))

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v6/users/userinfo"
        assert request.headers["authorization"] == f"Bearer {id_token}"
        return httpx.Response(200, json={"id": "user-123"})

    api_client = httpx.Client(base_url="https://api.tonal.com/v6", transport=httpx.MockTransport(api_handler))

    client = TonalClient(store, http_client=api_client, auth_http_client=auth_client)

    assert client.user_id == "user-123"
    saved = store.load()
    assert saved["id_token"] == id_token
    assert saved["user_id"] == "user-123"


def test_init_reuses_cached_token_without_relogin_when_not_expired(tmp_path):
    id_token = _fake_jwt(int(time.time()) + 3600)
    store = _credential_store(tmp_path, {
        "email": "a@example.com", "password": "x", "id_token": id_token,
        "refresh_token": "rt-1", "expires_at": str(int(time.time()) + 3600), "user_id": "user-123",
    })

    def auth_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call Auth0 when cached token is still valid")

    auth_client = httpx.Client(base_url="https://tonal.auth0.com", transport=httpx.MockTransport(auth_handler))
    api_client = httpx.Client(base_url="https://api.tonal.com/v6", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))

    client = TonalClient(store, http_client=api_client, auth_http_client=auth_client)

    assert client.user_id == "user-123"


def test_expired_token_triggers_refresh_grant(tmp_path):
    store = _credential_store(tmp_path, {
        "email": "a@example.com", "password": "x",
        "id_token": _fake_jwt(int(time.time()) - 10), "refresh_token": "rt-old",
        "expires_at": str(int(time.time()) - 10), "user_id": "user-123",
    })
    new_id_token = _fake_jwt(int(time.time()) + 3600)

    def auth_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "rt-old"
        return httpx.Response(200, json={"id_token": new_id_token, "refresh_token": "rt-new"})

    auth_client = httpx.Client(base_url="https://tonal.auth0.com", transport=httpx.MockTransport(auth_handler))
    api_client = httpx.Client(base_url="https://api.tonal.com/v6", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))

    client = TonalClient(store, http_client=api_client, auth_http_client=auth_client)

    assert store.load()["id_token"] == new_id_token
    assert store.load()["refresh_token"] == "rt-new"


def test_refresh_failure_falls_back_to_password_relogin(tmp_path):
    store = _credential_store(tmp_path, {
        "email": "a@example.com", "password": "hunter2",
        "id_token": _fake_jwt(int(time.time()) - 10), "refresh_token": "rt-revoked",
        "expires_at": str(int(time.time()) - 10), "user_id": "user-123",
    })
    relogin_id_token = _fake_jwt(int(time.time()) + 3600)
    calls = []

    def auth_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["grant_type"])
        if body["grant_type"] == "refresh_token":
            return httpx.Response(400, json={"error": "invalid_grant"})
        assert body["grant_type"] == "password"
        return httpx.Response(200, json={"id_token": relogin_id_token, "refresh_token": "rt-fresh"})

    auth_client = httpx.Client(base_url="https://tonal.auth0.com", transport=httpx.MockTransport(auth_handler))
    api_client = httpx.Client(base_url="https://api.tonal.com/v6", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))

    client = TonalClient(store, http_client=api_client, auth_http_client=auth_client)

    assert calls == ["refresh_token", "password"]
    assert store.load()["id_token"] == relogin_id_token


def test_get_retries_once_after_401_by_refreshing(tmp_path):
    id_token = _fake_jwt(int(time.time()) + 3600)
    refreshed_token = _fake_jwt(int(time.time()) + 7200)
    store = _credential_store(tmp_path, {
        "email": "a@example.com", "password": "x", "id_token": id_token,
        "refresh_token": "rt-1", "expires_at": str(int(time.time()) + 3600), "user_id": "user-123",
    })
    call_count = {"n": 0}

    def api_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v6/some/path":
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(401, json={"error": "expired"})
            assert request.headers["authorization"] == f"Bearer {refreshed_token}"
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"id": "user-123"})

    def auth_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id_token": refreshed_token, "refresh_token": "rt-2"})

    auth_client = httpx.Client(base_url="https://tonal.auth0.com", transport=httpx.MockTransport(auth_handler))
    api_client = httpx.Client(base_url="https://api.tonal.com/v6", transport=httpx.MockTransport(api_handler))

    client = TonalClient(store, http_client=api_client, auth_http_client=auth_client)
    result = client._get("/some/path")

    assert result == {"ok": True}
    assert call_count["n"] == 2


def test_post_and_delete_send_bearer_auth(tmp_path):
    id_token = _fake_jwt(int(time.time()) + 3600)
    store = _credential_store(tmp_path, {
        "email": "a@example.com", "password": "x", "id_token": id_token,
        "refresh_token": "rt-1", "expires_at": str(int(time.time()) + 3600), "user_id": "user-123",
    })

    def api_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v6/users/userinfo":
            return httpx.Response(200, json={"id": "user-123"})
        assert request.headers["authorization"] == f"Bearer {id_token}"
        if request.method == "POST":
            assert json.loads(request.content) == {"title": "t"}
            return httpx.Response(200, json={"id": "w1"})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(request.method)

    auth_client = httpx.Client(base_url="https://tonal.auth0.com", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    api_client = httpx.Client(base_url="https://api.tonal.com/v6", transport=httpx.MockTransport(api_handler))
    client = TonalClient(store, http_client=api_client, auth_http_client=auth_client)

    assert client._post("/user-workouts", {"title": "t"}) == {"id": "w1"}
    assert client._delete("/user-workouts/w1") == {}


# --- get_muscle_readiness -------------------------------------------------

def test_get_muscle_readiness_returns_all_present_muscles_from_real_fixture(tmp_path):
    fixture = _load_fixture("muscle_readiness_current.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v6/users/user-123/muscle-readiness/current"
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_muscle_readiness()

    assert result == {
        "Chest": 100.0, "Shoulders": 100.0, "Back": 100.0, "Triceps": 100.0,
        "Biceps": 100.0, "Abs": 100.0, "Obliques": 100.0, "Quads": 100.0,
        "Glutes": 100.0, "Hamstrings": 100.0, "Calves": 100.0,
    }


def test_get_muscle_readiness_skips_missing_muscles_rather_than_defaulting(tmp_path):
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Chest": 80, "Back": 75})

    client = _ready_client(tmp_path, api_handler)
    result = client.get_muscle_readiness()

    assert result == {"Chest": 80.0, "Back": 75.0}
    assert "Quads" not in result


# --- get_strength_score_current -------------------------------------------

def test_get_strength_score_current_from_real_fixture(tmp_path):
    fixture = _load_fixture("strength_scores_current.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v6/users/user-123/strength-scores/current"
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_strength_score_current()

    assert result == [
        {"region": "Upper", "score": 797},
        {"region": "Core", "score": 775},
        {"region": "Lower", "score": 554},
        {"region": "Overall", "score": 709},
    ]
    # no raw Tonal field names should leak through
    assert all(set(entry.keys()) == {"region", "score"} for entry in result)


# --- get_strength_score_history --------------------------------------------

def test_get_strength_score_history_from_real_fixture(tmp_path):
    fixture = _load_fixture("strength_scores_history.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v6/users/user-123/strength-scores/history"
        assert request.url.params.get("limit") == "20"
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_strength_score_history()

    assert result == [
        {"date": "2026-08-18", "overall": 709, "upper": 797, "lower": 554, "core": 775},
    ]


def test_get_strength_score_history_passes_limit_param(tmp_path):
    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("limit") == "5"
        return httpx.Response(200, json=[])

    client = _ready_client(tmp_path, api_handler)
    assert client.get_strength_score_history(limit=5) == []


# --- get_movements / search_movements --------------------------------------

def test_get_movements_from_real_fixture(tmp_path):
    fixture = _load_fixture("movements.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v6/movements"
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_movements()

    assert len(result) == 331
    assert all(set(entry.keys()) == {
        "id", "name", "muscle_groups", "body_region", "count_reps", "is_alternating",
    } for entry in result)
    handle_move = next(m for m in result if m["id"] == "00000000-0000-0000-0000-000000000002")
    assert handle_move["name"] == "Handle Move"
    assert handle_move["muscle_groups"] == []
    assert handle_move["body_region"] is None  # raw fixture entry has no bodyRegion key


def test_get_movements_caches_in_memory_and_avoids_refetch(tmp_path):
    fixture = _load_fixture("movements.json")
    call_count = {"n": 0}

    def api_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    client.get_movements()
    client.get_movements()

    assert call_count["n"] == 1


def test_get_movements_force_refresh_bypasses_cache(tmp_path):
    fixture = _load_fixture("movements.json")
    call_count = {"n": 0}

    def api_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    client.get_movements()
    client.get_movements(force_refresh=True)

    assert call_count["n"] == 2


def test_get_movements_ttl_expiry_triggers_refetch(tmp_path):
    fixture = _load_fixture("movements.json")
    call_count = {"n": 0}
    clock = {"t": 1_000_000.0}

    def api_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler, now_fn=lambda: clock["t"])
    client.get_movements()
    assert call_count["n"] == 1

    client.get_movements()  # still within TTL
    assert call_count["n"] == 1

    clock["t"] += 24 * 60 * 60 + 1  # push past the 24h TTL
    client.get_movements()
    assert call_count["n"] == 2


def test_search_movements_by_query_is_strict_subset(tmp_path):
    fixture = _load_fixture("movements.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    all_movements = client.get_movements()
    bench_movements = client.search_movements(query="bench")

    assert 0 < len(bench_movements) < len(all_movements)
    assert all(m in all_movements for m in bench_movements)
    assert all("bench" in m["name"].lower() for m in bench_movements)


def test_search_movements_by_muscle_group_exact_membership(tmp_path):
    fixture = _load_fixture("movements.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    chest_movements = client.search_movements(muscle_group="Chest")

    assert len(chest_movements) == 39
    assert all("Chest" in m["muscle_groups"] for m in chest_movements)


# --- get_activities ----------------------------------------------------------

def test_get_activities_hits_workout_activities_path_not_activities(tmp_path):
    fixture = _load_fixture("activities.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v6/users/user-123/workout-activities"
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_activities()

    assert len(result) == 2


def test_get_activities_maps_real_fields_and_synthesizes_title(tmp_path):
    # Real fixture is oldest-first on the wire (fixture[0] is 2024-04-07,
    # fixture[1] is 2024-04-09) -- get_activities sorts descending, so the
    # newer 2024-04-09 "Custom" entry (fixture[1]) comes back first.
    fixture = _load_fixture("activities.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_activities()

    first = result[0]
    assert set(first.keys()) == {
        "activity_id", "date", "title", "type", "duration_seconds", "total_volume_lbs",
    }
    assert first["activity_id"] == "bfd8bd25-a29f-4d25-89fe-a552207d5742"
    assert first["date"] == "2024-04-09T01:44:26.57Z"
    assert first["type"] == "Custom"
    assert first["title"] == "Custom Workout"

    second = result[1]
    assert second["activity_id"] == "092d226f-92aa-42f9-b17c-f0581a3069ad"
    assert second["date"] == "2024-04-07T00:14:27.258Z"
    assert second["type"] == "PT"
    assert second["title"] == "PT Workout"  # synthesized: raw fixture has no title field at all
    assert second["duration_seconds"] == 1204
    assert second["total_volume_lbs"] == 1130


def test_get_activities_client_side_slices_to_limit(tmp_path):
    fixture = _load_fixture("activities.json")
    assert len(fixture) == 2  # sanity: fixture itself has 2 entries

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_activities(limit=1)

    assert len(result) == 1
    # Regression: must slice to the most recent entry (2024-04-09), not
    # whichever entry happens to come first on the wire (2024-04-07).
    assert result[0]["activity_id"] == "bfd8bd25-a29f-4d25-89fe-a552207d5742"
    assert result[0]["date"] == "2024-04-09T01:44:26.57Z"


def _activity_stub(activity_id: str, begin_time: str) -> dict:
    """Minimal /workout-activities entry -- only the fields get_activities
    actually reads (id, beginTime, workoutType, totalDuration, totalVolume)."""
    return {
        "id": activity_id,
        "beginTime": begin_time,
        "workoutType": "Custom",
        "totalDuration": 1000,
        "totalVolume": 500,
    }


def test_get_activities_returns_most_recent_first_regardless_of_wire_order(tmp_path):
    """Regression for the ordering bug: the server returns workout history
    oldest-first, so get_activities must sort descending by beginTime
    itself rather than trusting wire order -- confirmed against a real
    account (tests/fixtures/tonal/activities.json's first two entries are
    from 2024, but the same account's strength_scores_history.json shows
    activity through 2026)."""
    raw = [
        _activity_stub("act-mid", "2026-06-15T08:00:00Z"),
        _activity_stub("act-oldest", "2026-01-01T08:00:00Z"),
        _activity_stub("act-newest", "2026-08-18T08:00:00Z"),
    ]

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=raw)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_activities(limit=10)

    assert [entry["activity_id"] for entry in result] == ["act-newest", "act-mid", "act-oldest"]


def test_get_activities_limit_keeps_the_most_recent_entries_not_the_oldest(tmp_path):
    raw = [
        _activity_stub("act-mid", "2026-06-15T08:00:00Z"),
        _activity_stub("act-oldest", "2026-01-01T08:00:00Z"),
        _activity_stub("act-newest", "2026-08-18T08:00:00Z"),
    ]

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=raw)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_activities(limit=2)

    assert [entry["activity_id"] for entry in result] == ["act-newest", "act-mid"]


# --- get_workout_detail -------------------------------------------------------

def test_get_workout_detail_from_real_fixture(tmp_path):
    fixture = _load_fixture("workout_activity_detail.json")

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v6/users/user-123/workout-activities/092d226f-92aa-42f9-b17c-f0581a3069ad"
        return httpx.Response(200, json=fixture)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_workout_detail("092d226f-92aa-42f9-b17c-f0581a3069ad")

    assert result["total_duration_seconds"] == 1204
    assert result["total_volume_lbs"] == 1130
    assert len(result["sets"]) == 8

    first_set = result["sets"][0]
    assert set(first_set.keys()) == {
        "movement_id", "is_warm_up", "reps", "weight_lbs", "volume_lbs",
        "one_rep_max", "max_power_watts", "rom_inches", "struggling_score", "side",
    }
    assert first_set["movement_id"] == "5ac0e785-c473-43d6-b1de-cc7befeac449"
    assert first_set["is_warm_up"] is False
    assert first_set["reps"] == 10
    assert first_set["weight_lbs"] == 25
    assert first_set["volume_lbs"] == 500
    assert first_set["one_rep_max"] == 33.246336284007285
    assert first_set["max_power_watts"] == 1269.7500228881836
    assert first_set["rom_inches"] == 41.019999504089355
    assert first_set["struggling_score"] == 0.5172652041348191
    assert first_set["side"] == "Both"


def test_get_workout_detail_splits_warm_up_vs_working_sets(tmp_path):
    mock_detail = {
        "totalDuration": 100,
        "totalVolume": 50,
        "workoutSetActivity": [
            {
                "movementId": "m1", "warmUp": True, "repCount": 5, "baseWeight": 10,
                "volume": 50, "oneRepMax": 1, "maxConPower": 1, "rom": 1,
                "strugglingScore": 1, "movementSide": "Both",
            },
            {
                "movementId": "m2", "warmUp": False, "repCount": 8, "baseWeight": 20,
                "volume": 160, "oneRepMax": 2, "maxConPower": 2, "rom": 2,
                "strugglingScore": 2, "movementSide": "Left",
            },
        ],
    }

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_detail)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_workout_detail("some-id")

    warm_up_sets = [s for s in result["sets"] if s["is_warm_up"]]
    working_sets = [s for s in result["sets"] if not s["is_warm_up"]]
    assert len(warm_up_sets) == 1
    assert warm_up_sets[0]["movement_id"] == "m1"
    assert len(working_sets) == 1
    assert working_sets[0]["movement_id"] == "m2"


def test_get_workout_detail_falls_back_when_primary_set_fields_missing(tmp_path):
    mock_detail = {
        "totalDuration": 10,
        "totalVolume": 5,
        "workoutSetActivity": [
            {
                "movementId": "m1", "warmUp": False,
                "repCount": None, "prescribedReps": 12,
                "avgWeight": 30,  # no baseWeight
                "totalVolume": 360,  # no volume
                "oneRepMax": 1, "maxConPower": 1, "rom": 1,
                "strugglingScore": 1, "movementSide": "Both",
            },
        ],
    }

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_detail)

    client = _ready_client(tmp_path, api_handler)
    result = client.get_workout_detail("some-id")

    only_set = result["sets"][0]
    assert only_set["reps"] == 12
    assert only_set["weight_lbs"] == 30
    assert only_set["volume_lbs"] == 360


# --- expand_blocks (pure function, no HTTP) ---------------------------------

def test_expand_blocks_single_exercise_three_sets():
    movement_map = {"m1": {"countReps": True, "isAlternating": False}}
    blocks = [{"exercises": [{"movement_id": "m1", "sets": 3, "reps": 10}]}]

    sets = expand_blocks(blocks, movement_map)

    assert len(sets) == 3
    assert [s["round"] for s in sets] == [1, 2, 3]
    assert all(s["prescribedReps"] == 10 for s in sets)
    assert sets[0]["blockStart"] is True
    assert sets[1]["blockStart"] is False


def test_expand_blocks_superset_interleaves_rounds():
    movement_map = {
        "m1": {"countReps": True, "isAlternating": False},
        "m2": {"countReps": True, "isAlternating": False},
    }
    blocks = [{"exercises": [
        {"movement_id": "m1", "sets": 2, "reps": 10},
        {"movement_id": "m2", "sets": 2, "reps": 12},
    ]}]

    sets = expand_blocks(blocks, movement_map)

    assert [s["movementId"] for s in sets] == ["m1", "m2", "m1", "m2"]
    assert [s["round"] for s in sets] == [1, 1, 2, 2]


def test_expand_blocks_alternating_movement_doubles_reps():
    movement_map = {"m1": {"countReps": True, "isAlternating": True}}
    blocks = [{"exercises": [{"movement_id": "m1", "sets": 1, "reps": 10}]}]

    sets = expand_blocks(blocks, movement_map)

    assert sets[0]["prescribedReps"] == 20


def test_expand_blocks_duration_movement_uses_prescribed_duration():
    movement_map = {"m1": {"countReps": False, "isAlternating": False}}
    blocks = [{"exercises": [{"movement_id": "m1", "sets": 1, "duration": 45}]}]

    sets = expand_blocks(blocks, movement_map)

    assert sets[0]["prescribedDuration"] == 45
    assert sets[0]["prescribedResistanceLevel"] == 5
    assert "prescribedReps" not in sets[0]


def test_expand_blocks_two_blocks_increments_block_number():
    movement_map = {"m1": {"countReps": True, "isAlternating": False}}
    blocks = [
        {"exercises": [{"movement_id": "m1", "sets": 1, "reps": 10}]},
        {"exercises": [{"movement_id": "m1", "sets": 1, "reps": 10}]},
    ]

    sets = expand_blocks(blocks, movement_map)

    assert [s["blockNumber"] for s in sets] == [1, 2]


def test_expand_blocks_duration_movement_without_explicit_duration_defaults_not_null():
    movement_map = {"m1": {"countReps": False, "isAlternating": False}}
    blocks = [{"exercises": [{"movement_id": "m1", "sets": 1}]}]  # no "duration" key

    sets = expand_blocks(blocks, movement_map)

    assert sets[0]["prescribedDuration"] == 30
    assert sets[0]["prescribedDuration"] is not None


def test_expand_blocks_skips_block_with_no_exercises_instead_of_raising():
    movement_map = {"m1": {"countReps": True, "isAlternating": False}}
    blocks = [
        {"exercises": []},
        {"exercises": [{"movement_id": "m1", "sets": 1, "reps": 10}]},
    ]

    sets = expand_blocks(blocks, movement_map)

    assert len(sets) == 1
    assert sets[0]["blockNumber"] == 2  # numbering follows block position, not skipped


# --- estimate_workout / create_workout / delete_workout ---------------------

_MOVEMENTS_FIXTURE = [
    {"id": "m1", "name": "Bench Press", "muscleGroups": ["Chest"], "bodyRegion": "Upper",
     "countReps": True, "isAlternating": False},
    {"id": "m2", "name": "Deadlift", "muscleGroups": ["Back"], "bodyRegion": "Lower",
     "countReps": True, "isAlternating": False},
]


def _movements_and_route_handler(routes):
    """Build an api_handler that serves GET /movements from the fixture above
    and dispatches everything else via `routes`, a dict of
    (method, path) -> response-returning callable(request)."""
    def api_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v6/movements":
            return httpx.Response(200, json=_MOVEMENTS_FIXTURE)
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key](request)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")
    return api_handler


def test_estimate_workout_posts_expanded_sets_and_returns_summary(tmp_path):
    def handle_estimate(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"sets": expand_blocks(
            [{"exercises": [{"movement_id": "m1", "sets": 3, "reps": 10}]}],
            {"m1": {"countReps": True, "isAlternating": False}},
        )}
        return httpx.Response(200, json={"duration": 600})

    api_handler = _movements_and_route_handler({
        ("POST", "/v6/user-workouts/estimate"): handle_estimate,
    })
    client = _ready_client(tmp_path, api_handler)

    blocks = [{"exercises": [{"movement_id": "m1", "sets": 3, "reps": 10}]}]
    result = client.estimate_workout(blocks)

    assert result == {"estimated_duration_min": 10, "set_count": 3}  # round(600/60)


def test_estimate_workout_invalid_movement_id_raises_value_error_naming_it(tmp_path):
    def unexpected_post(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not POST when a movement id is invalid")

    api_handler = _movements_and_route_handler({
        ("POST", "/v6/user-workouts/estimate"): unexpected_post,
    })
    client = _ready_client(tmp_path, api_handler)

    blocks = [{"exercises": [{"movement_id": "does-not-exist", "sets": 1, "reps": 10}]}]

    with pytest.raises(ValueError, match="does-not-exist"):
        client.estimate_workout(blocks)


def test_create_workout_nests_expanded_sets_under_sets_key(tmp_path):
    expected_sets = expand_blocks(
        [{"exercises": [
            {"movement_id": "m1", "sets": 2, "reps": 10},
            {"movement_id": "m2", "sets": 2, "reps": 5},
        ]}],
        {
            "m1": {"countReps": True, "isAlternating": False},
            "m2": {"countReps": True, "isAlternating": False},
        },
    )

    def handle_create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["title"] == "Push Day"
        assert body["sets"] == expected_sets
        return httpx.Response(200, json={"id": "workout-1"})

    api_handler = _movements_and_route_handler({
        ("POST", "/v6/user-workouts"): handle_create,
    })
    client = _ready_client(tmp_path, api_handler)

    blocks = [{"exercises": [
        {"movement_id": "m1", "sets": 2, "reps": 10},
        {"movement_id": "m2", "sets": 2, "reps": 5},
    ]}]
    result = client.create_workout("Push Day", blocks)

    assert result == {
        "workout_id": "workout-1",
        "title": "Push Day",
        "set_count": 4,
        "exercise_count": 2,
    }


def test_create_workout_invalid_movement_id_raises_value_error_naming_it(tmp_path):
    def unexpected_post(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not POST when a movement id is invalid")

    api_handler = _movements_and_route_handler({
        ("POST", "/v6/user-workouts"): unexpected_post,
    })
    client = _ready_client(tmp_path, api_handler)

    blocks = [{"exercises": [{"movement_id": "bogus-id", "sets": 1, "reps": 10}]}]

    with pytest.raises(ValueError, match="bogus-id"):
        client.create_workout("Push Day", blocks)


def test_delete_workout_returns_true_on_204(tmp_path):
    def handle_delete(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    api_handler = _movements_and_route_handler({
        ("DELETE", "/v6/user-workouts/workout-1"): handle_delete,
    })
    client = _ready_client(tmp_path, api_handler)

    assert client.delete_workout("workout-1") is True


# --- rate limiting -------------------------------------------------------

def test_get_raises_rate_limit_error_on_429_not_generic_http_error(tmp_path):
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = _ready_client(tmp_path, api_handler)

    with pytest.raises(RateLimitError, match="rate limited"):
        client.get_activities()


def test_post_raises_rate_limit_error_on_429(tmp_path):
    def api_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v6/movements":
            return httpx.Response(200, json=[])
        return httpx.Response(429, text="rate limited")

    client = _ready_client(tmp_path, api_handler)

    with pytest.raises(RateLimitError, match="rate limited"):
        client.estimate_workout([])


def test_delete_raises_rate_limit_error_on_429(tmp_path):
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = _ready_client(tmp_path, api_handler)

    with pytest.raises(RateLimitError, match="rate limited"):
        client.delete_workout("workout-1")
