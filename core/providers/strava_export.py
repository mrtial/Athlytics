"""Strava bulk-export (zip) provider.

Parses the activities.csv from a Strava "Request your Archive" export into
Activity records -- an alternative to the OAuth-based StravaProvider for
users without an active Strava API subscription. Feeds into the same
core.storage.repository.upsert_activities dedup path the OAuth provider
uses, so a zip import and a Garmin-forwarded copy of the same workout
collapse into one record regardless of which was imported first.

Timezone note: Strava's exported "Activity Date" column looks like local
wall-clock time but is actually already UTC -- verified by cross-checking
it against the same activities' own GPX/FIT files (whose timestamps are
unambiguously UTC) across a real export spanning 2020-2026. So it's parsed
directly with no timezone conversion.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from typing import Iterator

from core.providers.normalize import normalize_activity_type
from core.storage.models import Activity

SOURCE = "strava"

ACTIVITY_DATE_FMT = "%b %d, %Y, %I:%M:%S %p"


def _float_or_none(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


class StravaExportProvider:
    name = SOURCE

    def ingest(self, payload: bytes) -> Iterator[Activity]:
        csv_bytes = self._extract_activities_csv(payload)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
        for row in reader:
            activity_id = (row.get("Activity ID") or "").strip()
            if not activity_id:
                continue

            duration_seconds = _float_or_none(row.get("Moving Time"))
            if duration_seconds is None:
                duration_seconds = _float_or_none(row.get("Elapsed Time")) or 0.0

            sport_type = row.get("Activity Type") or "Workout"

            yield Activity(
                id=f"{SOURCE}:{activity_id}",
                source=SOURCE,
                activity_id=activity_id,
                activity_name=(row.get("Activity Name") or "").strip() or "Workout",
                activity_type=normalize_activity_type(sport_type),
                sport_type=sport_type,
                start_time=datetime.strptime(row["Activity Date"], ACTIVITY_DATE_FMT),
                duration_seconds=duration_seconds,
                distance_meters=_float_or_none(row.get("Distance")),
                calories=_float_or_none(row.get("Calories")),
                avg_hr=_float_or_none(row.get("Average Heart Rate")),
                max_hr=_float_or_none(row.get("Max Heart Rate")),
                avg_speed=_float_or_none(row.get("Average Speed")),
                max_speed=_float_or_none(row.get("Max Speed")),
                elevation_gain=_float_or_none(row.get("Elevation Gain")),
                elevation_loss=_float_or_none(row.get("Elevation Loss")),
                created_at=now,
            )

    @staticmethod
    def _extract_activities_csv(payload: bytes) -> bytes:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for name in zf.namelist():
                if name == "activities.csv" or name.endswith("/activities.csv"):
                    return zf.read(name)
        raise ValueError("uploaded zip does not contain an activities.csv")
