"""SQLite cache of Street View metadata lookups, so interrupted runs resume.

Keys are integer micro-degrees plus search radius. Integers compare exactly,
so there are no float-rounding cache misses.
Every definitive answer is stored, including ZERO_RESULTS, so points with
no coverage are never re-queried.
"""

import sqlite3
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

Key = Tuple[int, int, int]  # (lat_e6, lon_e6, radius_m)


class Lookup(NamedTuple):
    """A Street View metadata response, reduced to the fields we use."""
    status: str
    pano_id: Optional[str] = None
    date: Optional[str] = None
    pano_lat: Optional[float] = None
    pano_lon: Optional[float] = None
    copyright: Optional[str] = None


def make_key(lat: float, lon: float, radius: int) -> Key:
    """Build a cache key, rounding to 1e-6 degrees (~0.1 m)."""
    return (round(lat * 1_000_000), round(lon * 1_000_000), int(radius))


class MetadataCache:
    """Thin wrapper around a SQLite file of Lookup rows."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS lookups (
                lat_e6 INTEGER NOT NULL,
                lon_e6 INTEGER NOT NULL,
                radius INTEGER NOT NULL,
                status TEXT NOT NULL,
                pano_id TEXT,
                date TEXT,
                pano_lat REAL,
                pano_lon REAL,
                copyright TEXT,
                fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (lat_e6, lon_e6, radius)
            )
        """)
        self.conn.commit()

    def get_many(self, keys: Iterable[Key], fresh_after: Optional[str] = None,
                 fetched_before: Optional[str] = None) -> Dict[Key, Lookup]:
        """Return cached lookups for the keys that have one.

        Args:
            keys: Cache keys to look up
            fresh_after: If set ("YYYY-MM-DD HH:MM:SS", UTC), ignore entries
                fetched before this time so they get looked up again.
            fetched_before: If set, only entries fetched before this time: an
                earlier run's answers, not this one's.
        """
        found: Dict[Key, Lookup] = {}
        keys = list(keys)
        age_filter = ("AND fetched_at >= ?" if fresh_after else
                      "AND fetched_at < ?" if fetched_before else "")
        for i in range(0, len(keys), 300):  # 3 params each; SQLite limit 999
            batch = keys[i:i + 300]
            placeholders = ",".join(["(?,?,?)"] * len(batch))
            params = [v for key in batch for v in key]
            if fresh_after or fetched_before:
                params.append(fresh_after or fetched_before)
            rows = self.conn.execute(
                f"""SELECT lat_e6, lon_e6, radius, status, pano_id, date,
                           pano_lat, pano_lon, copyright
                    FROM lookups
                    WHERE (lat_e6, lon_e6, radius) IN (VALUES {placeholders})
                    {age_filter}""",
                params,
            )
            for row in rows:
                found[(row[0], row[1], row[2])] = Lookup(*row[3:])
        return found

    def put_many(self, items: List[Tuple[Key, Lookup]]) -> None:
        """Insert or replace lookups in one transaction."""
        self.conn.executemany(
            """INSERT OR REPLACE INTO lookups
               (lat_e6, lon_e6, radius, status, pano_id, date,
                pano_lat, pano_lon, copyright, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            [(*key, *lookup) for key, lookup in items],
        )
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM lookups").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
