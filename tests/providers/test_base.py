from datetime import datetime
from typing import Iterator

from core.providers.base import ImportProvider
from core.storage.models import MetricReading


class _FakeImportProvider:
    name = "fake_import"

    def ingest(self, payload: bytes) -> Iterator[MetricReading]:
        yield MetricReading(
            source="fake_import", metric_type="steps", timestamp=datetime(2026, 1, 1), value=100.0, unit="count"
        )


def test_import_provider_protocol_is_satisfied_structurally():
    provider: ImportProvider = _FakeImportProvider()
    results = list(provider.ingest(b"whatever"))
    assert results[0].metric_type == "steps"
