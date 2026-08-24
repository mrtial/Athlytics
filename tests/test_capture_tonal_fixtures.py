import json

import httpx
import pytest

from scripts.capture_tonal_fixtures import capture_all, write_fixtures

UID = "user-42"


class _StubClient:
    """Stubs TonalClient's low-level ._get(path) and .user_id -- capture_all
    only uses these two, since the high-level named methods it would ideally
    call don't exist yet (they land in a later task)."""

    def __init__(self):
        self.calls = []
        self.user_id = UID

    def _get(self, path, params=None):
        self.calls.append(path)
        return _response_for(path)


def _response_for(path):
    responses = {
        f"/users/{UID}/muscle-readiness/current": {"stub": "muscle_readiness_current"},
        f"/users/{UID}/strength-scores/current": {"stub": "strength_scores_current"},
        f"/users/{UID}/strength-scores/history": {"stub": "strength_scores_history"},
        f"/users/{UID}/strength-scores/distribution": {"stub": "strength_scores_distribution"},
        f"/users/{UID}/activities": [
            {"activityId": "abc123", "workoutPreview": {"totalVolume": 1000}},
            {"activityId": "def456", "workoutPreview": {"totalVolume": 500}},
        ],
        f"/users/{UID}/workout-activities/abc123": {
            "stub": "workout_activity_detail",
            "activity_id": "abc123",
        },
        "/movements": {"stub": "movements"},
    }
    return responses[path]


class _DistributionFailsStubClient:
    """Same as _StubClient, but strength-scores/distribution raises
    httpx.HTTPStatusError, as it would if that unconfirmed endpoint doesn't
    actually exist on a real account. Every other endpoint succeeds
    normally."""

    def __init__(self):
        self.calls = []
        self.user_id = UID

    def _get(self, path, params=None):
        self.calls.append(path)
        if path == f"/users/{UID}/strength-scores/distribution":
            request = httpx.Request("GET", f"https://api.tonal.com/v6{path}")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)
        return _response_for(path)


def test_capture_all_calls_every_endpoint_and_returns_dict_keyed_by_name():
    client = _StubClient()

    result = capture_all(client)

    assert result == {
        "muscle_readiness_current": {"stub": "muscle_readiness_current"},
        "strength_scores_current": {"stub": "strength_scores_current"},
        "strength_scores_history": {"stub": "strength_scores_history"},
        "strength_scores_distribution": {"stub": "strength_scores_distribution"},
        "activities": [
            {"activityId": "abc123", "workoutPreview": {"totalVolume": 1000}},
            {"activityId": "def456", "workoutPreview": {"totalVolume": 500}},
        ],
        "workout_activity_detail": {"stub": "workout_activity_detail", "activity_id": "abc123"},
        "movements": {"stub": "movements"},
    }
    assert f"/users/{UID}/muscle-readiness/current" in client.calls
    assert f"/users/{UID}/strength-scores/current" in client.calls
    assert f"/users/{UID}/strength-scores/history" in client.calls
    assert f"/users/{UID}/strength-scores/distribution" in client.calls
    assert f"/users/{UID}/activities" in client.calls
    # Uses the FIRST activityId from the activities response, not any other.
    assert f"/users/{UID}/workout-activities/abc123" in client.calls
    assert "/movements" in client.calls


def test_capture_all_falls_back_to_workout_activities_path_when_activities_is_empty():
    """If /activities returns no items, capture_all tries /workout-activities
    as a second candidate path before giving up -- covers the real-world case
    where /activities turned out to be the wrong resource name."""

    class _EmptyActivitiesThenWorkoutActivitiesStub:
        def __init__(self):
            self.calls = []
            self.user_id = UID

        def _get(self, path, params=None):
            self.calls.append(path)
            if path == f"/users/{UID}/activities":
                return []
            if path == f"/users/{UID}/workout-activities":
                return [{"activityId": "xyz789", "workoutPreview": {"totalVolume": 42}}]
            if path == f"/users/{UID}/workout-activities/xyz789":
                return {"stub": "detail", "activity_id": "xyz789"}
            return _response_for(path)

    client = _EmptyActivitiesThenWorkoutActivitiesStub()

    result = capture_all(client)

    assert result["activities"] == [{"activityId": "xyz789", "workoutPreview": {"totalVolume": 42}}]
    assert result["workout_activity_detail"] == {"stub": "detail", "activity_id": "xyz789"}
    assert f"/users/{UID}/activities" in client.calls
    assert f"/users/{UID}/workout-activities" in client.calls
    assert f"/users/{UID}/workout-activities/xyz789" in client.calls


def test_capture_all_survives_distribution_endpoint_failure(capsys):
    client = _DistributionFailsStubClient()

    result = capture_all(client)

    assert result["strength_scores_distribution"] is None
    # Every other fixture was still captured normally.
    assert result["muscle_readiness_current"] == {"stub": "muscle_readiness_current"}
    assert result["strength_scores_current"] == {"stub": "strength_scores_current"}
    assert result["strength_scores_history"] == {"stub": "strength_scores_history"}
    assert result["activities"] == [
        {"activityId": "abc123", "workoutPreview": {"totalVolume": 1000}},
        {"activityId": "def456", "workoutPreview": {"totalVolume": 500}},
    ]
    assert result["workout_activity_detail"] == {
        "stub": "workout_activity_detail",
        "activity_id": "abc123",
    }
    assert result["movements"] == {"stub": "movements"}

    warning = capsys.readouterr().err
    assert "strength-scores/distribution" in warning


def test_write_fixtures_writes_one_pretty_printed_json_file_per_endpoint(tmp_path):
    responses = {"muscle_readiness_current": {"a": 1}, "activities": [1, 2, 3]}

    write_fixtures(responses, tmp_path)

    readiness_path = tmp_path / "muscle_readiness_current.json"
    activities_path = tmp_path / "activities.json"
    assert json.loads(readiness_path.read_text()) == {"a": 1}
    assert json.loads(activities_path.read_text()) == [1, 2, 3]
    assert "\n" in readiness_path.read_text()  # pretty-printed, not single-line
