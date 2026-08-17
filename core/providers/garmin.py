from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from core.providers.base import RateLimitError
from core.providers.normalize import normalize_activity_type
from core.security.credentials import CredentialStore
from core.storage.models import Activity, MetricReading

logger = logging.getLogger(__name__)

GARMIN_METRIC_TYPES: list[str] = [
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


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails, requires MFA this headless
    context cannot complete, or no credentials are configured yet.

    Distinct from RateLimitError: an auth failure is not retryable with
    backoff. A later Dashboard plan catches this to surface a sync-status
    panel prompting the user to reconnect, per the design doc's Error
    Handling section ("Garmin auth failures and MFA challenges are
    surfaced in a sync-status panel, not silently retried forever").
    """


class GarminMfaRequired(GarminAuthError):
    """Raised when Garmin's login stops for an MFA/2FA code.

    Carries the live, partially-authenticated `Garmin` client and the
    `client_state` its `resume_login(client_state, mfa_code)` needs to
    finish -- that session state lives only on this in-memory object and
    can't be reconstructed from credentials alone, so the caller (an HTTP
    route) must keep it alive server-side between the request that
    triggers this and the follow-up request carrying the user's code (see
    complete_garmin_mfa).
    """

    def __init__(self, client, client_state):
        super().__init__(
            "Garmin requires an MFA/2FA code to complete login; enter the "
            "code sent to your phone to finish connecting."
        )
        self.client = client
        self.client_state = client_state


class GarminProvider:
    name = "garmin"

    def __init__(
        self,
        credential_store: CredentialStore,
        token_cache_dir: Path,
        garmin_client_factory: Callable[..., Garmin] = Garmin,
    ):
        credentials = credential_store.load()
        if credentials is None:
            raise GarminAuthError("no Garmin credentials configured; connect a data source first")

        self._client = garmin_client_factory(
            credentials["email"], credentials["password"], return_on_mfa=True
        )
        try:
            needs_mfa, client_state = self._client.login(str(token_cache_dir))
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError(f"Garmin authentication failed: {exc}") from exc

        if needs_mfa:
            raise GarminMfaRequired(self._client, client_state)

        self._race_predictor_cache: dict[tuple[date, date], list[MetricReading]] = {}
        self._activities_cache: dict[tuple[date, date], list[MetricReading]] = {}
        self._activity_records_cache: dict[tuple[date, date], list[Activity]] = {}

        self._registry: dict[str, Callable[[date, date], list[MetricReading]]] = {
            "resting_hr": self._fetch_resting_hr,
            "hrv": self._fetch_hrv,
            "vo2max": self._fetch_vo2max,
            "body_battery": self._fetch_body_battery,
            "weight": self._fetch_weight,
            "sleep_score": self._fetch_sleep,
            "steps": self._fetch_steps,
            "stress": self._fetch_stress,
            "respiration": self._fetch_respiration,
            "spo2": self._fetch_spo2,
            "training_load": self._fetch_training_load,
            "race_predictor_5k": lambda s, e: self._fetch_race_predictor("race_predictor_5k", s, e),
            "race_predictor_10k": lambda s, e: self._fetch_race_predictor("race_predictor_10k", s, e),
            "race_predictor_half_marathon": lambda s, e: self._fetch_race_predictor(
                "race_predictor_half_marathon", s, e
            ),
            "race_predictor_marathon": lambda s, e: self._fetch_race_predictor("race_predictor_marathon", s, e),
            "activity_duration": lambda s, e: self._fetch_activity_metric("activity_duration", s, e),
            "activity_distance": lambda s, e: self._fetch_activity_metric("activity_distance", s, e),
            "activity_calories": lambda s, e: self._fetch_activity_metric("activity_calories", s, e),
        }

    def _fetch_single_day_metric(
        self,
        garmin_method: Callable[[str], object],
        parse_fn: Callable[[object, date], list[MetricReading]],
        start: date,
        end: date,
    ) -> list[MetricReading]:
        """Shared driver for Garmin endpoints that only accept one date at a
        time (no start/end range parameter). Loops one calendar day at a
        time across [start, end], calling garmin_method(day.isoformat())
        and parse_fn(raw_response, day) for each, concatenating results."""
        readings: list[MetricReading] = []
        day = start
        while day <= end:
            try:
                raw = self._call(garmin_method, day.isoformat())
                if raw:
                    readings.extend(parse_fn(raw, day))
            except (RateLimitError, GarminAuthError):
                raise
            except Exception as exc:
                logger.debug("Skipping unrecorded/error day %s for %s: %s", day, getattr(garmin_method, "__name__", str(garmin_method)), exc)
            day += timedelta(days=1)
        return readings

    @staticmethod
    def _parse_resting_hr(raw: list[dict] | dict) -> list[MetricReading]:
        """Map get_rhr_daily() or get_rhr_day() response to MetricReading list."""
        if isinstance(raw, dict):
            metric_list = (
                raw.get("allMetrics", {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE")
                or raw.get("allMetrics", {}).get("metricsMap", {}).get("RESTING_HEART_RATE")
                or []
            )
            if not metric_list and raw.get("restingHeartRate") is not None:
                cdate = raw.get("calendarDate") or raw.get("statisticsStartDate")
                if cdate:
                    metric_list = [{"calendarDate": cdate, "value": raw.get("restingHeartRate")}]
            raw = metric_list

        readings = []
        for entry in raw:
            calendar_date = entry.get("calendarDate")
            rhr_value = entry.get("restingHeartRate") or entry.get("value")
            if calendar_date is None or rhr_value is None:
                continue
            readings.append(
                MetricReading(
                    source="garmin",
                    metric_type="resting_hr",
                    timestamp=datetime.combine(date.fromisoformat(calendar_date), time.min),
                    value=float(rhr_value),
                    unit="bpm",
                )
            )
        return readings

    def _fetch_resting_hr(self, start: date, end: date) -> list[MetricReading]:
        if hasattr(self._client, "get_rhr_daily"):
            raw = self._call(self._client.get_rhr_daily, start.isoformat(), end.isoformat())
            return self._parse_resting_hr(raw)
        return self._fetch_single_day_metric(
            self._client.get_rhr_day, lambda r, d: self._parse_resting_hr(r), start, end
        )

    @staticmethod
    def _parse_hrv(raw: dict | None) -> list[MetricReading]:
        """Map get_hrv_data_range() or get_hrv_data() response to MetricReading list."""
        if raw is None or not isinstance(raw, dict):
            return []
        entries = raw.get("hrvSummaries")
        if entries is None and "hrvSummary" in raw:
            entries = [raw["hrvSummary"]] if raw["hrvSummary"] else []
        elif entries is None:
            entries = []

        readings = []
        for entry in entries:
            calendar_date = entry.get("calendarDate")
            hrv_value = entry.get("lastNightAvg")
            if calendar_date is None or hrv_value is None:
                continue
            readings.append(
                MetricReading(
                    source="garmin",
                    metric_type="hrv",
                    timestamp=datetime.combine(date.fromisoformat(calendar_date), time.min),
                    value=float(hrv_value),
                    unit="ms",
                )
            )
        return readings

    def _fetch_hrv(self, start: date, end: date) -> list[MetricReading]:
        if hasattr(self._client, "get_hrv_data_range"):
            raw = self._call(self._client.get_hrv_data_range, start.isoformat(), end.isoformat())
            return self._parse_hrv(raw)
        return self._fetch_single_day_metric(
            self._client.get_hrv_data, lambda r, d: self._parse_hrv(r), start, end
        )

    @staticmethod
    def _parse_vo2max(raw: dict | list, day: date | None = None) -> list[MetricReading]:
        """Map get_max_metrics_range(), get_max_metrics(), or get_training_status() response to MetricReading list."""
        if not raw:
            return []
        if isinstance(raw, dict) and "mostRecentVO2Max" in raw:
            generic = raw.get("mostRecentVO2Max", {}).get("generic") or {}
            val = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
            cdate_str = generic.get("calendarDate")
            if val is not None:
                ts_date = date.fromisoformat(cdate_str) if cdate_str else (day or date.today())
                return [
                    MetricReading(
                        source="garmin",
                        metric_type="vo2max",
                        timestamp=datetime.combine(ts_date, time.min),
                        value=float(val),
                        unit="ml/kg/min",
                    )
                ]

        if isinstance(raw, dict):
            entries = raw.get("generic") or [raw]
        else:
            entries = raw
        readings = []
        for entry in entries:
            calendar_date = entry.get("calendarDate")
            vo2max_value = entry.get("vo2MaxValue") or entry.get("vo2MaxPreciseValue")
            if calendar_date is None or vo2max_value is None:
                continue
            readings.append(
                MetricReading(
                    source="garmin",
                    metric_type="vo2max",
                    timestamp=datetime.combine(date.fromisoformat(calendar_date), time.min),
                    value=float(vo2max_value),
                    unit="ml/kg/min",
                )
            )
        return readings

    def _fetch_vo2max(self, start: date, end: date) -> list[MetricReading]:
        if hasattr(self._client, "get_max_metrics_range"):
            raw = self._call(self._client.get_max_metrics_range, start.isoformat(), end.isoformat())
            res = self._parse_vo2max(raw)
            if res:
                return res
        if hasattr(self._client, "get_training_status"):
            return self._fetch_single_day_metric(
                self._client.get_training_status, lambda r, d: self._parse_vo2max(r, d), start, end
            )
        return self._fetch_single_day_metric(
            self._client.get_max_metrics, lambda r, d: self._parse_vo2max(r, d), start, end
        )

    @staticmethod
    def _parse_body_battery(raw: list[dict] | dict) -> list[MetricReading]:
        """Map get_body_battery()'s response to MetricReading list."""
        if not raw:
            return []
        if isinstance(raw, dict):
            raw = [raw]
        readings = []
        for day_entry in raw:
            # Legacy format: bodyBatteryValues
            intraday = day_entry.get("bodyBatteryValues") or []
            for point in intraday:
                raw_timestamp = point.get("timestamp")
                value = point.get("value")
                if raw_timestamp is None or value is None:
                    continue
                if isinstance(raw_timestamp, str):
                    ts = datetime.fromisoformat(raw_timestamp)
                    if ts.tzinfo is not None:
                        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    ts = raw_timestamp
                readings.append(
                    MetricReading(
                        source="garmin",
                        metric_type="body_battery",
                        timestamp=ts,
                        value=float(value),
                        unit="percent",
                    )
                )

            # Modern format: bodyBatteryValuesArray: [[epoch_ms, level], ...]
            arr = day_entry.get("bodyBatteryValuesArray") or []
            for item in arr:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    ts_ms, val = item[0], item[1]
                    if val is not None and ts_ms is not None:
                        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
                        readings.append(
                            MetricReading(
                                source="garmin",
                                metric_type="body_battery",
                                timestamp=ts,
                                value=float(val),
                                unit="percent",
                            )
                        )
        return readings

    def _fetch_body_battery(self, start: date, end: date) -> list[MetricReading]:
        raw = self._call(self._client.get_body_battery, start.isoformat(), end.isoformat())
        return self._parse_body_battery(raw)

    @staticmethod
    def _parse_weight(raw: dict) -> list[MetricReading]:
        """Map get_body_composition()'s response to MetricReading list."""
        entries = raw.get("dateWeightList") or []
        readings = []
        for entry in entries:
            calendar_date = entry.get("calendarDate")
            weight_raw = entry.get("weight")
            if calendar_date is None or weight_raw is None:
                continue
            weight_kg = float(weight_raw) / 1000.0
            readings.append(
                MetricReading(
                    source="garmin",
                    metric_type="weight",
                    timestamp=datetime.combine(date.fromisoformat(calendar_date), time.min),
                    value=weight_kg,
                    unit="kg",
                )
            )
        return readings

    def _fetch_weight(self, start: date, end: date) -> list[MetricReading]:
        raw = self._call(self._client.get_body_composition, start.isoformat(), end.isoformat())
        return self._parse_weight(raw)

    @staticmethod
    def _parse_sleep(raw: list[dict] | dict) -> list[MetricReading]:
        """Map get_sleep_daily() or get_sleep_data() response to MetricReading list."""
        if not raw:
            return []
        if isinstance(raw, dict):
            dto = raw.get("dailySleepDTO") or raw
            raw = [dto]
        readings = []
        for entry in raw:
            calendar_date = entry.get("calendarDate")
            overall_score_obj = (
                entry.get("overallSleepScore")
                or entry.get("sleepScores", {}).get("overall")
                or entry.get("sleepScore")
            )
            if isinstance(overall_score_obj, dict):
                sleep_score = overall_score_obj.get("value")
            else:
                sleep_score = overall_score_obj
            if calendar_date is None or sleep_score is None:
                continue
            readings.append(
                MetricReading(
                    source="garmin",
                    metric_type="sleep_score",
                    timestamp=datetime.combine(date.fromisoformat(calendar_date), time.min),
                    value=float(sleep_score),
                    unit="score",
                )
            )
        return readings

    def _fetch_sleep(self, start: date, end: date) -> list[MetricReading]:
        if hasattr(self._client, "get_sleep_daily"):
            raw = self._call(self._client.get_sleep_daily, start.isoformat(), end.isoformat())
            return self._parse_sleep(raw)
        return self._fetch_single_day_metric(
            self._client.get_sleep_data, lambda r, d: self._parse_sleep(r), start, end
        )

    @staticmethod
    def _parse_steps(raw: list[dict], day: date) -> list[MetricReading]:
        """Map one day's get_steps_data() response to a single daily-total
        MetricReading, summing all intraday entries."""
        total = sum(entry.get("steps") or 0 for entry in raw)
        return [
            MetricReading(
                source="garmin",
                metric_type="steps",
                timestamp=datetime.combine(day, time.min),
                value=float(total),
                unit="count",
            )
        ]

    def _fetch_steps(self, start: date, end: date) -> list[MetricReading]:
        return self._fetch_single_day_metric(self._client.get_steps_data, self._parse_steps, start, end)

    @staticmethod
    def _parse_stress(raw: dict, day: date) -> list[MetricReading]:
        """Map one day's get_stress_data() response to a single daily
        summary-stress MetricReading."""
        stress_value = raw.get("avgStressLevel")
        if stress_value is None:
            return []
        return [
            MetricReading(
                source="garmin",
                metric_type="stress",
                timestamp=datetime.combine(day, time.min),
                value=float(stress_value),
                unit="score",
            )
        ]

    def _fetch_stress(self, start: date, end: date) -> list[MetricReading]:
        return self._fetch_single_day_metric(self._client.get_stress_data, self._parse_stress, start, end)

    @staticmethod
    def _parse_respiration(raw: dict, day: date) -> list[MetricReading]:
        """Map get_respiration_data()'s response to MetricReading list."""
        value = raw.get("avgWakingRespirationValue") or raw.get("avgRespiration") or raw.get("avgSleepRespirationValue")
        if value is None:
            return []
        return [
            MetricReading(
                source="garmin",
                metric_type="respiration",
                timestamp=datetime.combine(day, time.min),
                value=float(value),
                unit="breaths_per_min",
            )
        ]

    def _fetch_respiration(self, start: date, end: date) -> list[MetricReading]:
        return self._fetch_single_day_metric(self._client.get_respiration_data, self._parse_respiration, start, end)

    @staticmethod
    def _parse_spo2(raw: dict, day: date) -> list[MetricReading]:
        """Map get_spo2_data()'s response to MetricReading list."""
        if not raw or not isinstance(raw, dict):
            return []
        value = (
            raw.get("averageSpO2")
            or raw.get("avgSleepSpO2")
            or raw.get("latestSpO2")
            or raw.get("lastSevenDaysAvgSpO2")
        )
        if value is None:
            return []
        return [
            MetricReading(
                source="garmin",
                metric_type="spo2",
                timestamp=datetime.combine(day, time.min),
                value=float(value),
                unit="percent",
            )
        ]

    def _fetch_spo2(self, start: date, end: date) -> list[MetricReading]:
        return self._fetch_single_day_metric(self._client.get_spo2_data, self._parse_spo2, start, end)

    @staticmethod
    def _parse_training_load(raw: dict, day: date) -> list[MetricReading]:
        """Map get_training_status()'s response to MetricReading list."""
        if not raw or not isinstance(raw, dict):
            return []
        value = raw.get("trainingLoad")
        if value is None and isinstance(raw.get("mostRecentTrainingStatus"), dict):
            status_dict = raw["mostRecentTrainingStatus"]
            value = status_dict.get("trainingLoad")
            if value is None and isinstance(status_dict.get("latestTrainingStatusData"), dict):
                for dev_data in status_dict["latestTrainingStatusData"].values():
                    if isinstance(dev_data, dict):
                        acute_dto = dev_data.get("acuteTrainingLoadDTO") or {}
                        value = acute_dto.get("dailyTrainingLoadAcute") or dev_data.get("weeklyTrainingLoad")
                        if value is not None:
                            break
        if value is None:
            return []
        return [
            MetricReading(
                source="garmin",
                metric_type="training_load",
                timestamp=datetime.combine(day, time.min),
                value=float(value),
                unit="load",
            )
        ]

    def _fetch_training_load(self, start: date, end: date) -> list[MetricReading]:
        return self._fetch_single_day_metric(self._client.get_training_status, self._parse_training_load, start, end)

    @staticmethod
    def _parse_race_predictions(raw: dict | list) -> list[MetricReading]:
        """Map get_race_predictions response to MetricReading list:
        4 metric types (5K/10K/half/marathon) per calendar day present in the response."""
        if not raw:
            return []
        if isinstance(raw, list):
            entries = raw
        elif isinstance(raw, dict):
            entries = raw.get("dailyPredictions") or [raw]
        else:
            entries = []
        distance_to_metric_type = {
            "time5K": "race_predictor_5k",
            "time10K": "race_predictor_10k",
            "timeHalfMarathon": "race_predictor_half_marathon",
            "timeMarathon": "race_predictor_marathon",
        }
        readings = []
        for entry in entries:
            calendar_date = entry.get("calendarDate")
            if calendar_date is None:
                continue
            timestamp = datetime.combine(date.fromisoformat(calendar_date), time.min)
            for raw_key, metric_type in distance_to_metric_type.items():
                seconds = entry.get(raw_key)
                if seconds is None:
                    continue
                readings.append(
                    MetricReading(
                        source="garmin",
                        metric_type=metric_type,
                        timestamp=timestamp,
                        value=float(seconds),
                        unit="seconds",
                    )
                )
        return readings

    def _fetch_race_predictor(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        cache_key = (start, end)
        if cache_key not in self._race_predictor_cache:
            raw = self._call(
                self._client.get_race_predictions, start.isoformat(), end.isoformat(), "daily"
            )
            self._race_predictor_cache[cache_key] = self._parse_race_predictions(raw)
        return [r for r in self._race_predictor_cache[cache_key] if r.metric_type == metric_type]

    @staticmethod
    def _parse_activities(raw: list[dict]) -> list[MetricReading]:
        """Map get_activities_by_date()'s response to MetricReading list:
        3 metric types (duration/distance/calories) per activity."""
        readings = []
        for activity in raw:
            raw_start = activity.get("startTimeGMT") or activity.get("startTimeLocal")
            if raw_start is None:
                continue
            if isinstance(raw_start, str):
                ts = datetime.fromisoformat(raw_start)
                if ts.tzinfo is not None:
                    ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                ts = raw_start

            duration_raw = activity.get("duration")
            distance_raw = activity.get("distance")
            calories_raw = activity.get("calories")

            if duration_raw is not None:
                readings.append(
                    MetricReading(
                        "garmin", "activity_duration", ts, float(duration_raw) / 60.0, "minutes"
                    )
                )
            if distance_raw is not None:
                readings.append(
                    MetricReading(
                        "garmin", "activity_distance", ts, float(distance_raw) / 1000.0, "km"
                    )
                )
            if calories_raw is not None:
                readings.append(
                    MetricReading("garmin", "activity_calories", ts, float(calories_raw), "kcal")
                )
        return readings

    @staticmethod
    def _parse_activity_records(raw: list[dict]) -> list[Activity]:
        """Map get_activities_by_date()'s response to full normalized Activity records."""
        activities = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for act in raw:
            raw_id = act.get("activityId")
            raw_start = act.get("startTimeGMT") or act.get("startTimeLocal")
            if raw_start is None:
                continue
            if isinstance(raw_start, str):
                ts = datetime.fromisoformat(raw_start)
                if ts.tzinfo is not None:
                    ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                ts = raw_start

            act_id_str = str(raw_id) if raw_id is not None else str(int(ts.timestamp()))

            name = act.get("activityName") or "Workout"
            type_obj = act.get("activityType")
            if isinstance(type_obj, dict):
                sport_key = type_obj.get("typeKey") or "other"
            elif isinstance(type_obj, str):
                sport_key = type_obj
            else:
                sport_key = "other"

            norm_type = normalize_activity_type(sport_key)

            duration = float(act.get("duration") or 0.0)
            distance = float(act["distance"]) if act.get("distance") is not None else None
            calories = float(act["calories"]) if act.get("calories") is not None else None
            avg_hr = float(act["averageHR"]) if act.get("averageHR") is not None else None
            max_hr = float(act["maxHR"]) if act.get("maxHR") is not None else None
            avg_speed = float(act["averageSpeed"]) if act.get("averageSpeed") is not None else None
            max_speed = float(act["maxSpeed"]) if act.get("maxSpeed") is not None else None
            elev_gain = float(act["elevationGain"]) if act.get("elevationGain") is not None else None
            elev_loss = float(act["elevationLoss"]) if act.get("elevationLoss") is not None else None

            activities.append(
                Activity(
                    id=f"garmin:{act_id_str}",
                    source="garmin",
                    activity_id=act_id_str,
                    activity_name=name,
                    activity_type=norm_type,
                    sport_type=sport_key,
                    start_time=ts,
                    duration_seconds=duration,
                    distance_meters=distance,
                    calories=calories,
                    avg_hr=avg_hr,
                    max_hr=max_hr,
                    avg_speed=avg_speed,
                    max_speed=max_speed,
                    elevation_gain=elev_gain,
                    elevation_loss=elev_loss,
                    created_at=now,
                )
            )
        return activities

    def fetch_activities(self, start: date, end: date) -> list[Activity]:
        """Fetch full Activity objects across [start, end]."""
        cache_key = (start, end)
        if cache_key not in self._activity_records_cache:
            raw = self._call(self._client.get_activities_by_date, start.isoformat(), end.isoformat())
            if not isinstance(raw, list):
                raw = []
            self._activities_cache[cache_key] = self._parse_activities(raw)
            self._activity_records_cache[cache_key] = self._parse_activity_records(raw)
        return self._activity_records_cache[cache_key]

    def _fetch_activity_metric(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        cache_key = (start, end)
        if cache_key not in self._activities_cache:
            raw = self._call(self._client.get_activities_by_date, start.isoformat(), end.isoformat())
            if not isinstance(raw, list):
                raw = []
            self._activities_cache[cache_key] = self._parse_activities(raw)
            self._activity_records_cache[cache_key] = self._parse_activity_records(raw)
        return [r for r in self._activities_cache[cache_key] if r.metric_type == metric_type]

    def supported_metric_types(self) -> list[str]:
        return list(GARMIN_METRIC_TYPES)

    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        if metric_type not in self._registry:
            raise ValueError(f"unsupported metric_type for GarminProvider: {metric_type!r}")
        return self._registry[metric_type](start, end)

    def _call(self, garmin_method: Callable[..., object], *args: object) -> object:
        """Call a garminconnect client method, mapping its exceptions onto
        this codebase's error contract (RateLimitError / GarminAuthError)."""
        try:
            return garmin_method(*args)
        except GarminConnectTooManyRequestsError as exc:
            raise RateLimitError(str(exc)) from exc
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError(f"Garmin session was rejected: {exc}") from exc


def complete_garmin_mfa(client: Garmin, client_state: object, mfa_code: str, token_cache_dir: Path) -> None:
    """Finish a login that stopped for GarminMfaRequired, using the code the
    user just entered. On success, persists the resulting session to
    token_cache_dir so a fresh GarminProvider(...) construction right after
    this (and every sync after that) picks it up from the token cache
    instead of needing MFA again -- resume_login() itself doesn't write the
    token cache, only a normal from-scratch login does.
    """
    try:
        client.resume_login(client_state, mfa_code)
    except GarminConnectAuthenticationError as exc:
        raise GarminAuthError(f"Invalid or expired MFA code: {exc}") from exc
    client.client.dump(str(token_cache_dir))
