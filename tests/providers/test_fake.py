from datetime import date, datetime

import pytest

from core.providers.base import RateLimitError
from core.providers.fake import FakeProvider
from core.storage.models import MetricReading


def test_fetch_filters_readings_to_requested_date_range():
    reading_in_range = MetricReading("fake", "steps", datetime(2026, 1, 5), 1000.0, "count")
    reading_out_of_range = MetricReading("fake", "steps", datetime(2026, 2, 1), 1000.0, "count")
    provider = FakeProvider(readings_by_metric={"steps": [reading_in_range, reading_out_of_range]})

    result = provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 31))

    assert result == [reading_in_range]


def test_fetch_raises_for_configured_failing_metric():
    provider = FakeProvider(readings_by_metric={"steps": []}, fail_metric_types={"steps"})

    with pytest.raises(RuntimeError):
        provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 31))


def test_fetch_raises_rate_limit_error_configured_number_of_times():
    provider = FakeProvider(readings_by_metric={"steps": []}, rate_limit_metric_types={"steps": 2})

    with pytest.raises(RateLimitError):
        provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(RateLimitError):
        provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 31))

    result = provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 31))

    assert result == []


def test_supported_metric_types_reflects_configured_readings():
    provider = FakeProvider(readings_by_metric={"steps": [], "resting_hr": []})

    assert sorted(provider.supported_metric_types()) == ["resting_hr", "steps"]


def test_fetch_calls_are_recorded_for_assertions():
    provider = FakeProvider(readings_by_metric={"steps": []})

    provider.fetch("steps", date(2026, 1, 1), date(2026, 1, 5))

    assert provider.fetch_calls == [("steps", date(2026, 1, 1), date(2026, 1, 5))]
