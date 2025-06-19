import sqlite3
from typing import Optional

_conn = None


def init_db(path: str) -> None:
    """Initialise the SQLite database at the given path."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(path)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata (lat REAL, lon REAL, date TEXT, PRIMARY KEY(lat, lon))"
        )
        _conn.commit()


def get_metadata(lat: float, lon: float) -> Optional[str]:
    """Return the stored date string for a location or None."""
    if _conn is None:
        raise RuntimeError("Database not initialised")
    cur = _conn.execute(
        "SELECT date FROM metadata WHERE lat=? AND lon=?", (lat, lon)
    )
    row = cur.fetchone()
    return row[0] if row else None


def save_metadata(lat: float, lon: float, date: str) -> None:
    """Save metadata to the database."""
    if _conn is None:
        raise RuntimeError("Database not initialised")
    _conn.execute(
        "INSERT OR REPLACE INTO metadata (lat, lon, date) VALUES (?, ?, ?)",
        (lat, lon, date),
    )
    _conn.commit()


