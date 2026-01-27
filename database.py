import sqlite3
from typing import Dict, List, Optional, Tuple

_conn = None

# Coordinate precision: 6 decimal places (~0.1m accuracy)
COORD_PRECISION = 6


def _round_coord(val: float) -> float:
    """Round coordinate to consistent precision to improve cache hits."""
    return round(val, COORD_PRECISION)


def init_db(path: str) -> None:
    """Initialise the SQLite database at the given path."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS metadata (
                lat REAL,
                lon REAL,
                date TEXT,
                fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(lat, lon)
            )"""
        )
        # Create index for faster lookups
        _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lat_lon ON metadata(lat, lon)"
        )
        _conn.commit()


def get_metadata(lat: float, lon: float) -> Optional[str]:
    """Return the stored date string for a location or None."""
    if _conn is None:
        raise RuntimeError("Database not initialised")
    lat, lon = _round_coord(lat), _round_coord(lon)
    cur = _conn.execute(
        "SELECT date FROM metadata WHERE lat=? AND lon=?", (lat, lon)
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_metadata_batch(
    points: List[Tuple[float, float]]
) -> Dict[Tuple[float, float], Optional[str]]:
    """Retrieve metadata for multiple points in a single query.

    Returns a dict mapping (lat, lon) -> date string or None.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if not points:
        return {}

    # Round coordinates for consistent lookups
    rounded = [(_round_coord(lat), _round_coord(lon)) for lat, lon in points]

    # Use a single query with IN clause for efficiency
    # SQLite has a limit on compound SELECT, so we batch if needed
    result: Dict[Tuple[float, float], Optional[str]] = {p: None for p in rounded}

    batch_size = 500  # SQLite variable limit is 999
    for i in range(0, len(rounded), batch_size):
        batch = rounded[i : i + batch_size]
        placeholders = ",".join(["(?,?)"] * len(batch))
        params = [v for lat, lon in batch for v in (lat, lon)]
        query = f"SELECT lat, lon, date FROM metadata WHERE (lat, lon) IN (VALUES {placeholders})"
        cur = _conn.execute(query, params)
        for row in cur.fetchall():
            result[(row[0], row[1])] = row[2]

    return result


def save_metadata(lat: float, lon: float, date: str, commit: bool = True) -> None:
    """Save metadata to the database.

    Args:
        lat: Latitude
        lon: Longitude
        date: Date string from Street View API
        commit: Whether to commit immediately (set False for batch operations)
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    lat, lon = _round_coord(lat), _round_coord(lon)
    _conn.execute(
        "INSERT OR REPLACE INTO metadata (lat, lon, date, fetched_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (lat, lon, date),
    )
    if commit:
        _conn.commit()


def save_metadata_batch(entries: List[Tuple[float, float, str]]) -> None:
    """Save multiple metadata entries efficiently in a single transaction.

    Args:
        entries: List of (lat, lon, date) tuples
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if not entries:
        return

    rounded = [
        (_round_coord(lat), _round_coord(lon), date) for lat, lon, date in entries
    ]
    _conn.executemany(
        "INSERT OR REPLACE INTO metadata (lat, lon, date, fetched_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        rounded,
    )
    _conn.commit()


def commit() -> None:
    """Explicitly commit pending changes."""
    if _conn is not None:
        _conn.commit()


def get_cache_stats() -> Dict[str, int]:
    """Return statistics about the cache."""
    if _conn is None:
        raise RuntimeError("Database not initialised")
    cur = _conn.execute("SELECT COUNT(*) FROM metadata")
    total = cur.fetchone()[0]
    cur = _conn.execute("SELECT COUNT(*) FROM metadata WHERE date IS NOT NULL")
    with_date = cur.fetchone()[0]
    return {"total_entries": total, "entries_with_date": with_date}


def close_db() -> None:
    """Close the SQLite connection if it is open."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
