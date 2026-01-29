import pytest
import database


@pytest.fixture
def db(tmp_path):
    """Initialize a fresh database for each test."""
    path = tmp_path / "test.db"
    database.init_db(str(path))
    yield database
    database.close_db()


def test_close_db(tmp_path):
    """close_db clears the connection."""
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
