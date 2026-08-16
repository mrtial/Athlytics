from datetime import datetime, timezone

import pytest

from core.storage.models import MetricReading


def test_naive_timestamp_is_accepted():
    reading = MetricReading("garmin", "steps", datetime(2026, 1, 1, 0, 0), 1000.0, "count")

    assert reading.timestamp == datetime(2026, 1, 1, 0, 0)


def test_aware_timestamp_is_rejected():
    aware = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="naive datetime"):
        MetricReading("garmin", "steps", aware, 1000.0, "count")
