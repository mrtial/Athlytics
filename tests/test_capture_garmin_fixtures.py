import json

from scripts.capture_garmin_fixtures import (
    capture_activities,
    capture_race_predictions,
    capture_range_metrics,
    capture_single_day_metrics,
    write_fixtures,
)


class _StubClient:
    def __init__(self):
        self.calls = []

    def get_rhr_daily(self, start, end):
        self.calls.append(("get_rhr_daily", start, end))
        return {"stub": "rhr"}

    def get_hrv_data_range(self, start, end):
        self.calls.append(("get_hrv_data_range", start, end))
        return {"stub": "hrv"}

    def get_max_metrics_range(self, start, end):
        self.calls.append(("get_max_metrics_range", start, end))
        return {"stub": "vo2max"}

    def get_body_battery(self, startdate, enddate):
        self.calls.append(("get_body_battery", startdate, enddate))
        return {"stub": "body_battery"}

    def get_body_composition(self, startdate, enddate):
        self.calls.append(("get_body_composition", startdate, enddate))
        return {"stub": "body_composition"}

    def get_sleep_daily(self, start, end):
        self.calls.append(("get_sleep_daily", start, end))
        return {"stub": "sleep"}

    def get_steps_data(self, cdate):
        self.calls.append(("get_steps_data", cdate))
        return {"stub": "steps"}

    def get_stress_data(self, cdate):
        self.calls.append(("get_stress_data", cdate))
        return {"stub": "stress"}

    def get_respiration_data(self, cdate):
        self.calls.append(("get_respiration_data", cdate))
        return {"stub": "respiration"}

    def get_spo2_data(self, cdate):
        self.calls.append(("get_spo2_data", cdate))
        return {"stub": "spo2"}

    def get_training_status(self, cdate):
        self.calls.append(("get_training_status", cdate))
        return {"stub": "training_status"}

    def get_race_predictions(self, startdate, enddate, _type):
        self.calls.append(("get_race_predictions", startdate, enddate, _type))
        return {"stub": "race_predictions"}

    def get_activities_by_date(self, startdate, enddate):
        self.calls.append(("get_activities_by_date", startdate, enddate))
        return {"stub": "activities"}


def test_capture_range_metrics_calls_all_range_methods_with_start_and_end():
    client = _StubClient()

    result = capture_range_metrics(client, "2026-01-01", "2026-01-07")

    assert result == {
        "get_rhr_daily": {"stub": "rhr"},
        "get_hrv_data_range": {"stub": "hrv"},
        "get_max_metrics_range": {"stub": "vo2max"},
        "get_body_battery": {"stub": "body_battery"},
        "get_body_composition": {"stub": "body_composition"},
        "get_sleep_daily": {"stub": "sleep"},
    }
    assert ("get_rhr_daily", "2026-01-01", "2026-01-07") in client.calls


def test_capture_single_day_metrics_calls_all_single_day_methods_with_day():
    client = _StubClient()

    result = capture_single_day_metrics(client, "2026-01-07")

    assert result == {
        "get_steps_data": {"stub": "steps"},
        "get_stress_data": {"stub": "stress"},
        "get_respiration_data": {"stub": "respiration"},
        "get_spo2_data": {"stub": "spo2"},
        "get_training_status": {"stub": "training_status"},
    }
    assert ("get_steps_data", "2026-01-07") in client.calls


def test_capture_race_predictions_calls_with_start_end_and_daily_type():
    client = _StubClient()

    result = capture_race_predictions(client, "2026-01-01", "2026-01-07")

    assert result == {"get_race_predictions": {"stub": "race_predictions"}}
    assert ("get_race_predictions", "2026-01-01", "2026-01-07", "daily") in client.calls


def test_capture_activities_calls_get_activities_by_date_with_start_and_end():
    client = _StubClient()

    result = capture_activities(client, "2026-01-01", "2026-01-07")

    assert result == {"get_activities_by_date": {"stub": "activities"}}
    assert ("get_activities_by_date", "2026-01-01", "2026-01-07") in client.calls


def test_write_fixtures_writes_one_pretty_printed_json_file_per_method(tmp_path):
    responses = {"get_rhr_daily": {"a": 1}, "get_steps_data": [1, 2, 3]}

    write_fixtures(responses, tmp_path)

    rhr_path = tmp_path / "get_rhr_daily.json"
    steps_path = tmp_path / "get_steps_data.json"
    assert json.loads(rhr_path.read_text()) == {"a": 1}
    assert json.loads(steps_path.read_text()) == [1, 2, 3]
    assert "\n" in rhr_path.read_text()  # pretty-printed, not single-line
