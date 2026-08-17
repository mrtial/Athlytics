from core.providers.normalize import normalize_activity_type


def test_normalize_activity_type_running_variants():
    assert normalize_activity_type("treadmill_running") == "running"
    assert normalize_activity_type("Run") == "running"


def test_normalize_activity_type_cycling_variants():
    assert normalize_activity_type("road_biking") == "cycling"
    assert normalize_activity_type("Ride") == "cycling"


def test_normalize_activity_type_none_or_empty_is_other():
    assert normalize_activity_type(None) == "other"
    assert normalize_activity_type("") == "other"


def test_normalize_activity_type_unknown_passes_through_lowered():
    assert normalize_activity_type("kayaking") == "kayaking"
