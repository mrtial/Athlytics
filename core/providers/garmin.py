from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from core.providers.base import RateLimitError
from core.security.credentials import CredentialStore
from core.storage.models import MetricReading


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails, requires MFA this headless
    context cannot complete, or no credentials are configured yet.

    Distinct from RateLimitError: an auth failure is not retryable with
    backoff. A later Dashboard plan catches this to surface a sync-status
    panel prompting the user to reconnect, per the design doc's Error
    Handling section ("Garmin auth failures and MFA challenges are
    surfaced in a sync-status panel, not silently retried forever").
    """


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
            needs_mfa, _ = self._client.login(str(token_cache_dir))
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError(f"Garmin authentication failed: {exc}") from exc

        if needs_mfa:
            raise GarminAuthError(
                "Garmin requires an MFA code to complete login; headless sync "
                "cannot prompt interactively. Reconnect the data source "
                "interactively to complete MFA and refresh the cached session "
                "token."
            )

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
            raw = self._call(garmin_method, day.isoformat())
            readings.extend(parse_fn(raw, day))
            day += timedelta(days=1)
        return readings

    @staticmethod
    def _parse_resting_hr(raw: list[dict]) -> list[MetricReading]:
        """Map get_rhr_daily()'s response to MetricReading list."""
        readings = []
        for entry in raw:
            calendar_date = entry.get("calendarDate")
            rhr_value = entry.get("restingHeartRate")
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
        raw = self._call(self._client.get_rhr_daily, start.isoformat(), end.isoformat())
        return self._parse_resting_hr(raw)

    @staticmethod
    def _parse_hrv(raw: dict | None) -> list[MetricReading]:
        """Map get_hrv_data_range()'s response to MetricReading list.
        Returns [] if raw is None (no HRV data in the requested range)."""
        if raw is None:
            return []
        entries = raw.get("hrvSummaries") or []
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
        raw = self._call(self._client.get_hrv_data_range, start.isoformat(), end.isoformat())
        return self._parse_hrv(raw)

    @staticmethod
    def _parse_vo2max(raw: dict) -> list[MetricReading]:
        """Map get_max_metrics_range()'s response to MetricReading list."""
        entries = raw.get("generic") or []
        readings = []
        for entry in entries:
            calendar_date = entry.get("calendarDate")
            vo2max_value = entry.get("vo2MaxValue")
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
        raw = self._call(self._client.get_max_metrics_range, start.isoformat(), end.isoformat())
        return self._parse_vo2max(raw)

    @staticmethod
    def _parse_body_battery(raw: list[dict]) -> list[MetricReading]:
        """Map get_body_battery()'s response to MetricReading list."""
        readings = []
        for day_entry in raw:
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
    def _parse_sleep(raw: list[dict]) -> list[MetricReading]:
        """Map get_sleep_daily()'s response to MetricReading list."""
        readings = []
        for entry in raw:
            calendar_date = entry.get("calendarDate")
            overall_score_obj = entry.get("overallSleepScore")
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
        raw = self._call(self._client.get_sleep_daily, start.isoformat(), end.isoformat())
        return self._parse_sleep(raw)

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
        value = raw.get("averageSpO2") or raw.get("lastSevenDaysAvgSpO2")
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
        value = raw.get("trainingLoad")
        if value is None and isinstance(raw.get("mostRecentTrainingStatus"), dict):
            value = raw["mostRecentTrainingStatus"].get("trainingLoad")
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

    def supported_metric_types(self) -> list[str]:
        return list(self._registry.keys())

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
