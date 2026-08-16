from datetime import date

from core.providers.base import RateLimitError
from core.storage.models import MetricReading


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        readings_by_metric: dict[str, list[MetricReading]] | None = None,
        fail_metric_types: set[str] | None = None,
        rate_limit_metric_types: dict[str, int] | None = None,
    ):
        self._readings_by_metric = readings_by_metric or {}
        self._fail_metric_types = fail_metric_types or set()
        self._rate_limit_remaining = dict(rate_limit_metric_types or {})
        self.fetch_calls: list[tuple[str, date, date]] = []

    def supported_metric_types(self) -> list[str]:
        return list(self._readings_by_metric.keys())

    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        self.fetch_calls.append((metric_type, start, end))

        if metric_type in self._fail_metric_types:
            raise RuntimeError(f"simulated permanent failure fetching {metric_type}")

        remaining = self._rate_limit_remaining.get(metric_type, 0)
        if remaining > 0:
            self._rate_limit_remaining[metric_type] = remaining - 1
            raise RateLimitError(f"simulated rate limit for {metric_type}")

        readings = self._readings_by_metric.get(metric_type, [])
        return [r for r in readings if start <= r.timestamp.date() <= end]
