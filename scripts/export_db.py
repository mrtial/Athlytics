#!/usr/bin/env python3
"""Dump the Athlytics SQLite database to a portable .sql text file.

Uses sqlite3.Connection.iterdump() rather than copying the .db file
directly -- the app runs with journal_mode=WAL (app/db.py), so a raw
file copy can leave out rows still sitting in the -wal file. Reading
through a live connection always sees the full committed state
regardless of journal mode.

Usage (run inside the container so it can see the mounted volume):
    docker exec athlytics python scripts/export_db.py --output /data/seed.sql
    docker cp athlytics:/data/seed.sql seed/athlytics_seed.sql

Or, against a local data dir (no Docker):
    python scripts/export_db.py --output seed/athlytics_seed.sql
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import data_dir_from_env  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to the sqlite db file (default: $ATHLYTICS_DATA_DIR/athlytics.db)",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output .sql file path")
    args = parser.parse_args()

    db_path = args.db or (data_dir_from_env() / "athlytics.db")
    if not db_path.exists():
        raise SystemExit(f"No database found at {db_path}")

    conn = sqlite3.connect(db_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        for line in conn.iterdump():
            f.write(f"{line}\n")
    conn.close()
    print(f"Dumped {db_path} -> {args.output}")


if __name__ == "__main__":
    main()
