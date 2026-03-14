#!/usr/bin/env python3
"""Migration: Create empty_tiles table.

Tracks tiles that have no OSM roads (ocean, sea, etc.) so the scheduler
doesn't waste time re-processing them on every run.

Usage:
    python migrations/003_create_empty_tiles.py
    python migrations/003_create_empty_tiles.py --dry-run
"""

import argparse
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv


MIGRATION_NAME = "003_create_empty_tiles"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS empty_tiles (
    tile_id VARCHAR(20) PRIMARY KEY,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE empty_tiles IS 'Tiles with no OSM roads — excluded from scheduler processing';
"""

DROP_TABLE_SQL = """
DROP TABLE IF EXISTS empty_tiles;
"""


def run_migration(dry_run: bool = False, rollback: bool = False) -> bool:
    """Run the migration.

    Args:
        dry_run: If True, print SQL but don't execute
        rollback: If True, drop the table instead of creating

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
        sql = DROP_TABLE_SQL
        action = "rollback"
    else:
        sql = CREATE_TABLE_SQL
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
        description="Create empty_tiles table"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print SQL without executing"
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Drop the table instead of creating"
    )
    args = parser.parse_args()

    success = run_migration(dry_run=args.dry_run, rollback=args.rollback)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
