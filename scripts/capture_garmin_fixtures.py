#!/usr/bin/env python3
"""Capture real Garmin Connect API responses as fixtures for parser development.

Run this once, manually, against your own Garmin account:

    python scripts/capture_garmin_fixtures.py --email you@example.com

You will be prompted for your password interactively (never pass it on the
command line -- it would land in shell history). If your account has MFA
enabled, the script will report that MFA is required and exit; complete an
interactive login with the garminconnect library separately first to
populate the token cache, then re-run this script.

Writes one pretty-printed JSON file per captured method into
tests/fixtures/garmin/<method_name>.json. These fixture files are safe to
commit -- they are your own health data on your own self-hosted instance,
not a secret. Your Garmin email/password are NOT written to any fixture
file, and this script never logs them.
"""
import argparse
import getpass
import json
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "garmin"


def capture_range_metrics(client, start: str, end: str) -> dict[str, object]:
    """Call every range-based (start, end) Garmin method; return raw
    responses keyed by method name."""
    return {
        "get_rhr_daily": client.get_rhr_daily(start, end),
        "get_hrv_data_range": client.get_hrv_data_range(start, end),
        "get_max_metrics_range": client.get_max_metrics_range(start, end),
        "get_body_battery": client.get_body_battery(start, end),
        "get_body_composition": client.get_body_composition(start, end),
        "get_sleep_daily": client.get_sleep_daily(start, end),
    }


def capture_single_day_metrics(client, day: str) -> dict[str, object]:
    """Call every single-day Garmin method; return raw responses keyed by
    method name."""
    return {
        "get_steps_data": client.get_steps_data(day),
        "get_stress_data": client.get_stress_data(day),
        "get_respiration_data": client.get_respiration_data(day),
        "get_spo2_data": client.get_spo2_data(day),
        "get_training_status": client.get_training_status(day),
    }


def capture_race_predictions(client, start: str, end: str) -> dict[str, object]:
    """Call get_race_predictions with an explicit daily range (all three of
    startdate/enddate/_type must be provided together, or none at all)."""
    return {"get_race_predictions": client.get_race_predictions(start, end, "daily")}


def capture_activities(client, start: str, end: str) -> dict[str, object]:
    """Call the range-based, internally-paginated activities method."""
    return {"get_activities_by_date": client.get_activities_by_date(start, end)}


def write_fixtures(responses: dict[str, object], fixture_dir: Path) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for method_name, response in responses.items():
        path = fixture_dir / f"{method_name}.json"
        path.write_text(json.dumps(response, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Garmin Connect account email")
    parser.add_argument(
        "--token-cache",
        default=str(Path.home() / ".garminconnect"),
        help="Directory for Garmin's cached session tokens (passed to Garmin.login)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days of history to request for range-based methods",
    )
    args = parser.parse_args()

    password = getpass.getpass(f"Garmin password for {args.email}: ")

    client = Garmin(args.email, password, return_on_mfa=True)
    needs_mfa, _ = client.login(args.token_cache)
    if needs_mfa:
        print(
            "This account requires MFA. This script cannot prompt for an MFA "
            "code interactively. Complete an interactive garminconnect login "
            "once to populate the token cache at --token-cache, then re-run "
            "this script (it will reuse the cached session)."
        )
        raise SystemExit(1)

    end = date.today()
    start = end - timedelta(days=args.days)

    responses: dict[str, object] = {}
    responses.update(capture_range_metrics(client, start.isoformat(), end.isoformat()))
    responses.update(capture_single_day_metrics(client, end.isoformat()))
    responses.update(capture_race_predictions(client, start.isoformat(), end.isoformat()))
    responses.update(capture_activities(client, start.isoformat(), end.isoformat()))

    write_fixtures(responses, FIXTURE_DIR)
    print(f"Wrote {len(responses)} fixture files to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
