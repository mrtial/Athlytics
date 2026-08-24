#!/usr/bin/env python3
"""Restore a .sql dump (from export_db.py) into a fresh sqlite database.

Refuses to overwrite an existing, non-empty target file unless --force
is passed -- this is meant for seeding a *new* database (a fresh dev
instance, or disaster recovery into an empty data dir), not for
silently clobbering a live one.

Usage:
    python scripts/import_db.py --input seed/athlytics_seed.sql --db data/athlytics.db
"""
import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="Path to the .sql dump to restore")
    parser.add_argument("--db", type=Path, required=True, help="Target sqlite db file to create")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing target db file")
    args = parser.parse_args()

    if args.db.exists() and args.db.stat().st_size > 0 and not args.force:
        raise SystemExit(f"{args.db} already exists and is non-empty -- pass --force to overwrite")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    if args.db.exists():
        args.db.unlink()

    sql = args.input.read_text()
    conn = sqlite3.connect(args.db)
    conn.executescript(sql)
    conn.commit()
    conn.close()
    print(f"Restored {args.input} -> {args.db}")


if __name__ == "__main__":
    main()
