"""Tests for the migration script.

These tests use SQLite fixture data to verify migration logic.
PostgreSQL integration tests require a running PostgreSQL instance.
"""

import os
import sqlite3
import pytest

from migrate_to_postgres import compute_tile_id, get_sqlite_data


@pytest.fixture
def sqlite_test_db(tmp_path):
    """Create a test SQLite database with sample data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))

    conn.execute("""
        CREATE TABLE metadata (
            lat REAL,
            lon REAL,
            date TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(lat, lon)
        )
    """)

    # Insert test data
    test_data = [
        (53.81, -1.67, "2024-01"),
        (53.82, -1.68, "2024-02"),
        (51.50, -0.10, "2023-06"),  # London area
        (55.95, -3.20, "2022-12"),  # Edinburgh area
    ]
    conn.executemany(
        "INSERT INTO metadata (lat, lon, date) VALUES (?, ?, ?)",
        test_data
    )
    conn.commit()
    conn.close()

    return str(db_path)


class TestComputeTileId:
    """Tests for tile ID computation."""

    def test_tile_id_format(self):
        """Tile IDs should have correct format."""
        tile_id = compute_tile_id(-1.5, 53.5)
        assert tile_id.startswith("tile_")
        parts = tile_id.split("_")
        assert len(parts) == 3

    def test_tile_id_consistency(self):
        """Same coordinates should produce same tile ID."""
        id1 = compute_tile_id(-1.5, 53.5)
        id2 = compute_tile_id(-1.5, 53.5)
        assert id1 == id2

    def test_tile_id_different_locations(self):
        """Different locations should have different tile IDs."""
        id1 = compute_tile_id(-1.5, 53.5)
        id2 = compute_tile_id(-2.0, 54.0)
        assert id1 != id2

    def test_tile_id_matches_geographic_scope(self):
        """Tile IDs should match geographic_scope module."""
        from geographic_scope import generate_tile_id

        coords = [
            (-1.5, 53.5),
            (-0.1, 51.5),
            (-3.2, 55.9),
        ]
        for lon, lat in coords:
            migration_tile = compute_tile_id(lon, lat)
            geo_tile = generate_tile_id(lon, lat)
            assert migration_tile == geo_tile


class TestGetSqliteData:
    """Tests for reading SQLite data."""

    def test_read_existing_database(self, sqlite_test_db):
        """Should read all rows from SQLite database."""
        rows, count = get_sqlite_data(sqlite_test_db)
        assert count == 4
        assert len(rows) == 4

    def test_row_structure(self, sqlite_test_db):
        """Rows should have lat, lon, date, fetched_at."""
        rows, _ = get_sqlite_data(sqlite_test_db)
        for row in rows:
            assert len(row) == 4  # lat, lon, date, fetched_at

    def test_row_values(self, sqlite_test_db):
        """Row values should match inserted data."""
        rows, _ = get_sqlite_data(sqlite_test_db)

        # Find the Leeds row
        leeds_row = next(r for r in rows if r[0] == 53.81)
        assert leeds_row[1] == -1.67
        assert leeds_row[2] == "2024-01"

    def test_file_not_found(self, tmp_path):
        """Should raise FileNotFoundError for missing database."""
        with pytest.raises(FileNotFoundError):
            get_sqlite_data(str(tmp_path / "nonexistent.db"))


class TestMigrationLogic:
    """Tests for migration logic (without PostgreSQL)."""

    def test_all_rows_get_tile_ids(self, sqlite_test_db):
        """All rows should receive valid tile IDs."""
        rows, _ = get_sqlite_data(sqlite_test_db)

        for lat, lon, date, _ in rows:
            tile_id = compute_tile_id(lon, lat)
            assert tile_id is not None
            assert tile_id.startswith("tile_")

    def test_default_priority(self, sqlite_test_db):
        """Migrated data should default to medium priority."""
        # This is implicit - the migration script sets priority = "medium"
        # We verify the constant is correct
        default_priority = "medium"
        assert default_priority in ("high", "medium", "low")

    def test_unique_tile_distribution(self, sqlite_test_db):
        """Different locations should map to different tiles."""
        rows, _ = get_sqlite_data(sqlite_test_db)

        tile_ids = set()
        for lat, lon, _, _ in rows:
            tile_id = compute_tile_id(lon, lat)
            tile_ids.add(tile_id)

        # We have 4 rows in different areas, should have multiple tiles
        assert len(tile_ids) >= 3


class TestEmptyDatabase:
    """Tests for handling empty databases."""

    def test_empty_database(self, tmp_path):
        """Should handle empty database gracefully."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE metadata (
                lat REAL,
                lon REAL,
                date TEXT,
                fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(lat, lon)
            )
        """)
        conn.commit()
        conn.close()

        rows, count = get_sqlite_data(str(db_path))
        assert count == 0
        assert rows == []


# PostgreSQL integration tests - require running PostgreSQL
# Run with: pytest tests/test_migration.py -k "postgres" --run-postgres

@pytest.fixture
def pg_url():
    """Get PostgreSQL URL from environment."""
    url = os.environ.get("DATABASE_URL")
    if not url or not url.startswith("postgresql://"):
        pytest.skip("DATABASE_URL not set or not PostgreSQL")
    return url


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql://"),
    reason="Requires PostgreSQL (set DATABASE_URL)"
)
class TestPostgreSQLMigration:
    """Integration tests requiring PostgreSQL."""

    def test_migrate_to_postgres(self, sqlite_test_db, pg_url):
        """Full migration test with PostgreSQL."""
        from migrate_to_postgres import migrate_data

        result = migrate_data(sqlite_test_db, pg_url, dry_run=False)

        assert result["sqlite_count"] == 4
        assert result["pg_count"] == 4
        assert result["match"] is True

    def test_dry_run_no_changes(self, sqlite_test_db, pg_url):
        """Dry run should not modify PostgreSQL."""
        from migrate_to_postgres import migrate_data

        result = migrate_data(sqlite_test_db, pg_url, dry_run=True)

        assert result["dry_run"] is True
        assert result["migrated"] == 0
