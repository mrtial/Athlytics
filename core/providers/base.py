from datetime import date
from typing import Iterator, Protocol

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


class ImportProvider(Protocol):
    name: str

    def ingest(self, payload: bytes) -> Iterator[MetricReading]:
        ...
