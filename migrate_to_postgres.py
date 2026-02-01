#!/usr/bin/env python3
"""Migration script to transfer data from SQLite to PostgreSQL/PostGIS.

Usage:
    python migrate_to_postgres.py [--sqlite-path PATH] [--dry-run]

Environment variables:
    DATABASE_URL: PostgreSQL connection string (required)

Example:
    DATABASE_URL=postgresql://user:pass@localhost:5432/streetview \
        python migrate_to_postgres.py --sqlite-path metadata.db
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from typing import List, Tuple


def compute_tile_id(lon: float, lat: float) -> str:
    """Compute tile ID for a coordinate.

    Uses the same algorithm as geographic_scope.py.
    """
    min_lon, min_lat = -8.0, 49.9
    tile_size = 0.05

    lon_idx = int((lon - min_lon) / tile_size)
    lat_idx = int((lat - min_lat) / tile_size)
    return f"tile_{lon_idx}_{lat_idx}"


def get_sqlite_data(sqlite_path: str) -> Tuple[List[Tuple], int]:
    """Read all data from SQLite database.

    Returns:
        Tuple of (list of rows, total count)
    """
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    conn = sqlite3.connect(sqlite_path)
    cursor = conn.cursor()

    # Get total count
    cursor.execute("SELECT COUNT(*) FROM metadata")
    total_count = cursor.fetchone()[0]

    # Get all data
    cursor.execute("SELECT lat, lon, date, fetched_at FROM metadata")
    rows = cursor.fetchall()

    conn.close()
    return rows, total_count


def migrate_data(sqlite_path: str, pg_url: str, dry_run: bool = False,
                 batch_size: int = 1000) -> dict:
    """Migrate data from SQLite to PostgreSQL.

    Args:
        sqlite_path: Path to SQLite database
        pg_url: PostgreSQL connection URL
        dry_run: If True, only simulate migration
        batch_size: Number of rows to insert per batch

    Returns:
        Dict with migration statistics
    """
    import psycopg2
    from psycopg2.extras import execute_values

    print(f"Starting migration from {sqlite_path}")
    print(f"PostgreSQL URL: {pg_url[:30]}...")

    # Read SQLite data
    print("\nReading SQLite data...")
    rows, sqlite_count = get_sqlite_data(sqlite_path)
    print(f"  Found {sqlite_count:,} rows in SQLite")

    if dry_run:
        print("\n[DRY RUN] Would migrate the following:")
        print(f"  - {sqlite_count:,} rows")
        # Sample tile distribution
        tile_counts = {}
        for lat, lon, date, fetched_at in rows[:1000]:
            tile_id = compute_tile_id(lon, lat)
            tile_counts[tile_id] = tile_counts.get(tile_id, 0) + 1
        print(f"  - Sample tiles (first 1000 rows): {len(tile_counts)} unique tiles")
        return {
            "sqlite_count": sqlite_count,
            "migrated": 0,
            "dry_run": True
        }

    # Connect to PostgreSQL
    print("\nConnecting to PostgreSQL...")
    pg_conn = psycopg2.connect(pg_url)

    # Enable PostGIS and create table
    print("Initializing PostgreSQL schema...")
    with pg_conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")

        # Drop existing table if exists (migration is a fresh start)
        cur.execute("DROP TABLE IF EXISTS metadata CASCADE")

        # Create new table with PostGIS
        cur.execute("""
            CREATE TABLE metadata (
                id SERIAL PRIMARY KEY,
                location GEOGRAPHY(POINT, 4326),
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                date TEXT,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                priority VARCHAR(10) CHECK (priority IN ('high', 'medium', 'low')),
                tile_id VARCHAR(20),
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(lat, lon)
            )
        """)

        # Create indexes
        cur.execute("CREATE INDEX idx_location ON metadata USING GIST(location)")
        cur.execute("CREATE INDEX idx_tile_id ON metadata(tile_id)")
        cur.execute("CREATE INDEX idx_last_checked ON metadata(last_checked)")
        cur.execute("CREATE INDEX idx_priority ON metadata(priority)")
        cur.execute("CREATE INDEX idx_lat_lon_pg ON metadata(lat, lon)")

    pg_conn.commit()
    print("  Schema created successfully")

    # Migrate data in batches
    print(f"\nMigrating data in batches of {batch_size:,}...")
    migrated = 0
    errors = 0
    start_time = datetime.now()

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]

        # Prepare batch data with computed fields
        batch_data = []
        for lat, lon, date, fetched_at in batch:
            tile_id = compute_tile_id(lon, lat)
            # Default priority is "medium" for migrated data
            priority = "medium"
            batch_data.append((lat, lon, date, lon, lat, tile_id, priority, fetched_at))

        try:
            with pg_conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO metadata (lat, lon, date, location, tile_id, priority, fetched_at, last_checked)
                    VALUES %s
                    ON CONFLICT (lat, lon) DO NOTHING
                    """,
                    batch_data,
                    template="(%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, CURRENT_TIMESTAMP)"
                )
            pg_conn.commit()
            migrated += len(batch)

            # Progress update
            progress = (i + len(batch)) / len(rows) * 100
            elapsed = (datetime.now() - start_time).total_seconds()
            rate = migrated / elapsed if elapsed > 0 else 0
            print(f"  Progress: {progress:.1f}% ({migrated:,}/{sqlite_count:,}) - {rate:.0f} rows/sec", end="\r")

        except Exception as e:
            print(f"\n  Error in batch {i}-{i+batch_size}: {e}")
            errors += 1
            pg_conn.rollback()

    print(f"\n  Migration completed: {migrated:,} rows migrated")

    # Verify migration
    print("\nVerifying migration...")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM metadata")
        pg_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT tile_id) FROM metadata")
        tile_count = cur.fetchone()[0]

        cur.execute("""
            SELECT priority, COUNT(*)
            FROM metadata
            GROUP BY priority
        """)
        priority_counts = {row[0]: row[1] for row in cur.fetchall()}

    pg_conn.close()

    print(f"  PostgreSQL row count: {pg_count:,}")
    print(f"  Unique tiles: {tile_count:,}")
    print(f"  By priority: {priority_counts}")

    if pg_count != sqlite_count:
        print(f"\n  WARNING: Row count mismatch!")
        print(f"    SQLite: {sqlite_count:,}")
        print(f"    PostgreSQL: {pg_count:,}")
        print(f"    Difference: {sqlite_count - pg_count:,}")

    return {
        "sqlite_count": sqlite_count,
        "pg_count": pg_count,
        "migrated": migrated,
        "errors": errors,
        "tiles": tile_count,
        "by_priority": priority_counts,
        "match": pg_count == sqlite_count
    }


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Street View metadata from SQLite to PostgreSQL/PostGIS"
    )
    parser.add_argument(
        "--sqlite-path",
        default="metadata.db",
        help="Path to SQLite database (default: metadata.db)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without making changes"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows per batch (default: 1000)"
    )
    args = parser.parse_args()

    # Check for DATABASE_URL
    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("Error: DATABASE_URL environment variable is required")
        print("\nExample:")
        print("  DATABASE_URL=postgresql://user:pass@localhost:5432/streetview \\")
        print("      python migrate_to_postgres.py")
        sys.exit(1)

    # Check PostgreSQL URL format
    if not (pg_url.startswith("postgresql://") or pg_url.startswith("postgres://")):
        print("Error: DATABASE_URL must start with postgresql:// or postgres://")
        sys.exit(1)

    try:
        result = migrate_data(
            args.sqlite_path,
            pg_url,
            dry_run=args.dry_run,
            batch_size=args.batch_size
        )

        print("\n" + "=" * 50)
        print("Migration Summary")
        print("=" * 50)
        print(f"  SQLite rows: {result['sqlite_count']:,}")

        if not args.dry_run:
            print(f"  PostgreSQL rows: {result['pg_count']:,}")
            print(f"  Migrated: {result['migrated']:,}")
            print(f"  Errors: {result.get('errors', 0)}")
            print(f"  Tiles: {result.get('tiles', 0):,}")

            if result['match']:
                print("\n  ✓ Migration successful - row counts match")
                sys.exit(0)
            else:
                print("\n  ✗ Migration completed with warnings - row counts differ")
                sys.exit(1)
        else:
            print("\n  [DRY RUN] No changes made")
            sys.exit(0)

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except ImportError:
        print("Error: psycopg2 is required for PostgreSQL support")
        print("Install with: pip install psycopg2-binary")
        sys.exit(1)
    except Exception as e:
        print(f"Error during migration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
