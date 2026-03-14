#!/usr/bin/env python3
"""Migration: Add tiles_list column to job_status table.

Stores a JSON array of tile IDs processed by each job, enabling
geographic context (area name + Google Maps link) on the dashboard.

Usage:
    python migrations/002_add_tiles_list.py
    python migrations/002_add_tiles_list.py --dry-run
"""

import argparse
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv


MIGRATION_NAME = "002_add_tiles_list"

ADD_COLUMN_SQL = """
ALTER TABLE job_status ADD COLUMN IF NOT EXISTS tiles_list TEXT;

COMMENT ON COLUMN job_status.tiles_list IS 'JSON array of tile IDs processed by this job';
"""

DROP_COLUMN_SQL = """
ALTER TABLE job_status DROP COLUMN IF EXISTS tiles_list;
"""


def run_migration(dry_run: bool = False, rollback: bool = False) -> bool:
    """Run the migration.

    Args:
        dry_run: If True, print SQL but don't execute
        rollback: If True, drop the column instead of adding

    Returns:
        True if successful
    """
    load_dotenv()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable not set")
        print("This migration requires PostgreSQL.")
        return False

    if not db_url.startswith("postgresql://") and not db_url.startswith("postgres://"):
        print("ERROR: This migration requires PostgreSQL")
        print(f"Got: {db_url[:20]}...")
        return False

    if rollback:
        sql = DROP_COLUMN_SQL
        action = "rollback"
    else:
        sql = ADD_COLUMN_SQL
        action = "migration"

    if dry_run:
        print(f"=== DRY RUN: {MIGRATION_NAME} ({action}) ===")
        print(sql)
        print("=" * 50)
        print("(No changes made)")
        return True

    print(f"=== Running {MIGRATION_NAME} ({action}) ===")

    try:
        import psycopg2

        conn = psycopg2.connect(db_url)
        conn.autocommit = False

        with conn.cursor() as cur:
            cur.execute(sql)

        conn.commit()
        print(f"SUCCESS: {action} completed")
        conn.close()
        return True

    except Exception as e:
        print(f"ERROR: {action} failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Add tiles_list column to job_status table"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print SQL without executing"
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Drop the column instead of adding"
    )
    args = parser.parse_args()

    success = run_migration(dry_run=args.dry_run, rollback=args.rollback)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
