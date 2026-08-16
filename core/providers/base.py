from datetime import date
from typing import Protocol

from core.storage.models import MetricReading


class RateLimitError(Exception):
    """Raised by Provider.fetch() when the upstream API rate-limits the request.

    Signals the caller (the sync orchestrator) to back off and retry rather
    than treating the fetch as a permanent failure.
    """


class Provider(Protocol):
    name: str

    def supported_metric_types(self) -> list[str]:
        ...

    def fetch(self, metric_type: str, start: date, end: date) -> list[MetricReading]:
        ...
