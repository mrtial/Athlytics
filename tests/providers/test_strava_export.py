import zipfile
from datetime import datetime
from io import BytesIO

import pytest

from core.providers.strava_export import StravaExportProvider

CSV_HEADER = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,"
    "Commute,Filename,Elapsed Time,Moving Time,Distance,Max Speed,Average Speed,"
    "Elevation Gain,Elevation Loss,Max Heart Rate,Average Heart Rate,Calories"
)


def _zip_payload(csv_body: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", CSV_HEADER + "\n" + csv_body)
    return buf.getvalue()


def test_ingest_parses_a_complete_row_into_an_activity():
    csv_body = (
        "19866787708,\"Aug 23, 2026, 3:06:54 PM\",Lunch Run,Run,355,1.02,false,"
        "activities/21001762946.fit.gz,355.0,328.0,1020.3,6.04,3.111,2.0,12.0,150.0,140.0,71.0\n"
    )

    activities = list(StravaExportProvider().ingest(_zip_payload(csv_body)))

    assert len(activities) == 1
    activity = activities[0]
    assert activity.id == "strava:19866787708"
    assert activity.source == "strava"
    assert activity.activity_id == "19866787708"
    assert activity.activity_name == "Lunch Run"
    assert activity.activity_type == "running"
    assert activity.sport_type == "Run"
    # The CSV's "Activity Date" is already UTC wall-clock time (verified against
    # the export's own GPX/FIT files) -- parsed directly, no tz conversion.
    assert activity.start_time == datetime(2026, 8, 23, 15, 6, 54)
    assert activity.start_time.tzinfo is None
    assert activity.duration_seconds == 328.0  # raw "Moving Time" column, not display "Elapsed Time"
    assert activity.distance_meters == 1020.3  # raw "Distance" column (meters), not display "1.02" (miles)
    assert activity.max_speed == 6.04
    assert activity.avg_speed == 3.111
    assert activity.elevation_gain == 2.0
    assert activity.elevation_loss == 12.0
    assert activity.max_hr == 150.0
    assert activity.avg_hr == 140.0
    assert activity.calories == 71.0


def test_ingest_maps_blank_optional_columns_to_none():
    csv_body = (
        "1,\"Jan 1, 2026, 8:00:00 AM\",Morning Ride,Ride,3600,10,false,,3600.0,3500.0,16000.0,,,,,,,\n"
    )

    activity = next(StravaExportProvider().ingest(_zip_payload(csv_body)))

    assert activity.max_speed is None
    assert activity.avg_speed is None
    assert activity.elevation_gain is None
    assert activity.elevation_loss is None
    assert activity.max_hr is None
    assert activity.avg_hr is None
    assert activity.calories is None


def test_ingest_falls_back_to_elapsed_time_when_moving_time_is_blank():
    csv_body = (
        "2,\"Jan 1, 2026, 8:00:00 AM\",Yoga,Yoga,1800,0,false,,1800.0,,,,,,,,,\n"
    )

    activity = next(StravaExportProvider().ingest(_zip_payload(csv_body)))

    assert activity.duration_seconds == 1800.0


def test_ingest_defaults_blank_activity_name_to_workout():
    csv_body = (
        "3,\"Jan 1, 2026, 8:00:00 AM\",,Run,600,1,false,,600.0,600.0,1600.0,,,,,,,\n"
    )

    activity = next(StravaExportProvider().ingest(_zip_payload(csv_body)))

    assert activity.activity_name == "Workout"


def test_ingest_raises_value_error_when_activities_csv_missing():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("profile.csv", "Athlete ID\n1\n")

    with pytest.raises(ValueError, match="activities.csv"):
        list(StravaExportProvider().ingest(buf.getvalue()))


def test_ingest_skips_rows_with_blank_activity_id():
    csv_body = ",\"Jan 1, 2026, 8:00:00 AM\",Run,Run,600,1,false,,600.0,600.0,1600.0,,,,,,,\n"

    activities = list(StravaExportProvider().ingest(_zip_payload(csv_body)))

    assert activities == []
