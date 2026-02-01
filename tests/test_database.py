"""Tests for the database module.

Tests run against SQLite by default. Set DATABASE_URL environment variable
to test PostgreSQL functionality.
"""

import os
import pytest
import database


@pytest.fixture
def db(tmp_path):
    """Initialize a fresh database for each test."""
    # Ensure no DATABASE_URL is set for SQLite tests
    old_url = os.environ.pop("DATABASE_URL", None)

    path = tmp_path / "test.db"
    database.init_db(str(path))
    yield database
    database.close_db()

    # Restore DATABASE_URL if it was set
    if old_url:
        os.environ["DATABASE_URL"] = old_url


def test_close_db(tmp_path):
    """close_db clears the connection."""
    # Ensure SQLite mode
    os.environ.pop("DATABASE_URL", None)

    path = tmp_path / "test.db"
    database.init_db(str(path))
    assert database._conn is not None
    database.close_db()
    assert database._conn is None
    # Call again without prior init to ensure no exception
    database.close_db()


def test_save_and_get_metadata(db):
    """Basic save and retrieve."""
    db.save_metadata(53.81, -1.675, "2024-01")
    result = db.get_metadata(53.81, -1.675)
    assert result == "2024-01"


def test_get_metadata_not_found(db):
    """Non-existent location returns None."""
    result = db.get_metadata(99.0, 99.0)
    assert result is None


def test_coordinate_rounding(db):
    """Coordinates are rounded to consistent precision."""
    # Save with many decimal places
    db.save_metadata(53.8100000001, -1.6750000001, "2024-02")
    # Retrieve with slightly different precision
    result = db.get_metadata(53.8100000009, -1.6750000009)
    assert result == "2024-02"


def test_save_metadata_batch(db):
    """Batch save multiple entries."""
    entries = [
        (53.81, -1.67, "2024-01"),
        (53.82, -1.68, "2024-02"),
        (53.83, -1.69, "2024-03"),
    ]
    db.save_metadata_batch(entries)

    assert db.get_metadata(53.81, -1.67) == "2024-01"
    assert db.get_metadata(53.82, -1.68) == "2024-02"
    assert db.get_metadata(53.83, -1.69) == "2024-03"


def test_get_metadata_batch(db):
    """Batch retrieve multiple entries."""
    entries = [
        (53.81, -1.67, "2024-01"),
        (53.82, -1.68, "2024-02"),
        (53.83, -1.69, "2024-03"),
    ]
    db.save_metadata_batch(entries)

    points = [(53.81, -1.67), (53.82, -1.68), (53.84, -1.70)]  # Last one not in DB
    results = db.get_metadata_batch(points)

    # Check rounded coordinates in results
    assert results[(53.81, -1.67)] == "2024-01"
    assert results[(53.82, -1.68)] == "2024-02"
    # Non-existent point returns None
    assert results[(53.84, -1.70)] is None


def test_get_metadata_batch_empty(db):
    """Batch retrieve with empty list returns empty dict."""
    results = db.get_metadata_batch([])
    assert results == {}


def test_update_existing_metadata(db):
    """Updating an existing location replaces the value."""
    db.save_metadata(53.81, -1.67, "2024-01")
    db.save_metadata(53.81, -1.67, "2024-06")
    assert db.get_metadata(53.81, -1.67) == "2024-06"


def test_get_cache_stats(db):
    """Cache stats report correct counts."""
    # Empty database
    stats = db.get_cache_stats()
    assert stats["total_entries"] == 0
    assert stats["entries_with_date"] == 0

    # Add some entries
    db.save_metadata_batch([
        (53.81, -1.67, "2024-01"),
        (53.82, -1.68, "2024-02"),
    ])

    stats = db.get_cache_stats()
    assert stats["total_entries"] == 2
    assert stats["entries_with_date"] == 2


def test_not_initialized_raises():
    """Operations on uninitialized database raise RuntimeError."""
    # Ensure connection is closed
    database.close_db()

    with pytest.raises(RuntimeError, match="Database not initialised"):
        database.get_metadata(1.0, 2.0)

    with pytest.raises(RuntimeError, match="Database not initialised"):
        database.save_metadata(1.0, 2.0, "2024-01")

    with pytest.raises(RuntimeError, match="Database not initialised"):
        database.get_metadata_batch([(1.0, 2.0)])

    with pytest.raises(RuntimeError, match="Database not initialised"):
        database.save_metadata_batch([(1.0, 2.0, "2024-01")])


def test_get_backend_sqlite(db):
    """Backend should be SQLite when DATABASE_URL not set."""
    assert db.get_backend() == "sqlite"
    assert db.is_postgresql() is False


def test_get_points_in_bbox_sqlite(db):
    """get_points_in_bbox works for SQLite."""
    entries = [
        (53.81, -1.67, "2024-01"),
        (53.82, -1.68, "2024-02"),
        (54.00, -2.00, "2024-03"),  # Outside bbox
    ]
    db.save_metadata_batch(entries)

    # Query bbox that includes first two points
    points = db.get_points_in_bbox(-1.70, 53.80, -1.65, 53.85)
    assert len(points) == 2

    lats = [p["lat"] for p in points]
    assert 53.81 in lats
    assert 53.82 in lats


def test_coverage_stats_sqlite(db):
    """get_coverage_stats returns basic info for SQLite."""
    db.save_metadata_batch([
        (53.81, -1.67, "2024-01"),
        (53.82, -1.68, "2024-02"),
    ])

    stats = db.get_coverage_stats()
    assert "overall" in stats
    assert stats["overall"]["total_entries"] == 2


def test_stale_locations_sqlite(db):
    """get_stale_locations returns empty list for SQLite."""
    db.save_metadata(53.81, -1.67, "2024-01")
    result = db.get_stale_locations(min_age_days=1)
    assert result == []


def test_tile_coverage_sqlite(db):
    """get_tile_coverage returns default values for SQLite."""
    result = db.get_tile_coverage("tile_123_456")
    assert result["total_points"] == 0


class TestCoordinatePrecision:
    """Tests for coordinate precision handling."""

    def test_round_coord_function(self):
        """_round_coord rounds to 6 decimal places."""
        assert database._round_coord(53.123456789) == 53.123457
        assert database._round_coord(-1.123456789) == -1.123457

    def test_precision_constant(self):
        """COORD_PRECISION should be 6."""
        assert database.COORD_PRECISION == 6


class TestComputeTileId:
    """Tests for tile ID computation in database module."""

    def test_compute_tile_id_format(self):
        """_compute_tile_id produces valid tile IDs."""
        tile_id = database._compute_tile_id(-1.5, 53.5)
        assert tile_id.startswith("tile_")
        parts = tile_id.split("_")
        assert len(parts) == 3

    def test_compute_tile_id_matches_geographic_scope(self):
        """Tile IDs should match geographic_scope module."""
        from geographic_scope import generate_tile_id

        # Test several coordinates
        coords = [
            (-1.5, 53.5),
            (-0.1, 51.5),
            (-2.35, 53.45),
        ]
        for lon, lat in coords:
            db_tile = database._compute_tile_id(lon, lat)
            geo_tile = generate_tile_id(lon, lat)
            assert db_tile == geo_tile, f"Mismatch at {lon}, {lat}"
