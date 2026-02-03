#!/usr/bin/env python3
"""Migration: Create job_status table for tracking background job execution.

This migration creates the job_status table used by the scheduler to track
update job progress and history.

Usage:
    python migrations/001_create_job_status.py
    python migrations/001_create_job_status.py --dry-run
"""

import argparse
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv


MIGRATION_NAME = "001_create_job_status"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS job_status (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(50) UNIQUE NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    priority_filter VARCHAR(10),
    tile_limit INTEGER,
    tiles_processed INTEGER DEFAULT 0,
    tiles_total INTEGER,
    locations_updated INTEGER DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    error_message TEXT
);

COMMENT ON TABLE job_status IS 'Tracks background update job execution and progress';
COMMENT ON COLUMN job_status.job_id IS 'Unique identifier for the job (format: job_YYYYMMDD_HHMMSS)';
COMMENT ON COLUMN job_status.status IS 'Current job status: running, completed, failed, or cancelled';
COMMENT ON COLUMN job_status.priority_filter IS 'Priority level filter used for this job (high/medium/low)';
COMMENT ON COLUMN job_status.tile_limit IS 'Maximum tiles to process in this job';
COMMENT ON COLUMN job_status.tiles_processed IS 'Number of tiles processed so far';
COMMENT ON COLUMN job_status.tiles_total IS 'Total tiles to process in this job';
COMMENT ON COLUMN job_status.locations_updated IS 'Number of Street View locations updated';
COMMENT ON COLUMN job_status.api_calls IS 'Number of Street View API calls made';
"""

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_job_status_job_id ON job_status(job_id);
CREATE INDEX IF NOT EXISTS idx_job_status_status ON job_status(status);
CREATE INDEX IF NOT EXISTS idx_job_status_started_at ON job_status(started_at);
CREATE INDEX IF NOT EXISTS idx_job_status_completed_at ON job_status(completed_at);
"""

DROP_TABLE_SQL = """
DROP TABLE IF EXISTS job_status CASCADE;
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
        sql = CREATE_TABLE_SQL + "\n" + CREATE_INDEXES_SQL
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
        description="Create job_status table for tracking background jobs"
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
