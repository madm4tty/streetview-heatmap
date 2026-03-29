"""Database module supporting both SQLite and PostgreSQL/PostGIS backends.

The backend is determined by the DATABASE_URL environment variable:
- If DATABASE_URL is set and starts with "postgresql://", uses PostgreSQL
- Otherwise, uses SQLite (backward compatible)

PostgreSQL requires PostGIS extension for spatial operations.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Coordinate precision: 6 decimal places (~0.1m accuracy)
COORD_PRECISION = 6

# Module-level connection state
_conn: Any = None
_backend: str = "sqlite"  # "sqlite" or "postgresql"


def _round_coord(val: float) -> float:
    """Round coordinate to consistent precision to improve cache hits."""
    return round(val, COORD_PRECISION)


def _get_backend() -> str:
    """Determine which database backend to use based on DATABASE_URL."""
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        return "postgresql"
    return "sqlite"


def _compute_tile_id(lon: float, lat: float) -> str:
    """Compute tile ID for a coordinate.

    Uses the same algorithm as geographic_scope.py to avoid circular imports.
    """
    # UK bounds and tile size (must match geographic_scope.py)
    min_lon, min_lat = -8.0, 49.9
    tile_size = 0.05

    lon_idx = int((lon - min_lon) / tile_size)
    lat_idx = int((lat - min_lat) / tile_size)
    return f"tile_{lon_idx}_{lat_idx}"


def init_db(path: str = None) -> None:
    """Initialise the database connection.

    For SQLite: uses the provided path or HEATMAP_DB env var.
    For PostgreSQL: ignores path and uses DATABASE_URL env var.

    Args:
        path: SQLite database path (ignored for PostgreSQL)
    """
    global _conn, _backend

    if _conn is not None:
        return

    _backend = _get_backend()

    if _backend == "postgresql":
        _init_postgresql()
    else:
        _init_sqlite(path)


def _init_sqlite(path: str = None) -> None:
    """Initialize SQLite database."""
    global _conn

    if path is None:
        path = os.environ.get("HEATMAP_DB", "metadata.db")

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


def _init_postgresql() -> None:
    """Initialize PostgreSQL database with PostGIS."""
    global _conn

    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL support. "
            "Install with: pip install psycopg2-binary"
        )

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is required for PostgreSQL")

    _conn = psycopg2.connect(db_url)
    _conn.autocommit = False

    with _conn.cursor() as cur:
        # Enable PostGIS extension
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")

        # Create metadata table with PostGIS geometry
        cur.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
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
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_location ON metadata USING GIST(location)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_tile_id ON metadata(tile_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_last_checked ON metadata(last_checked)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_priority ON metadata(priority)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_lat_lon_pg ON metadata(lat, lon)
        """)

        # Create road_segments table for pre-computed road geometries
        cur.execute("""
            CREATE TABLE IF NOT EXISTS road_segments (
                id SERIAL PRIMARY KEY,
                tile_id VARCHAR(20) NOT NULL,
                osm_name TEXT,
                highway_type VARCHAR(30),
                geometry GEOMETRY(LineString, 4326) NOT NULL,
                capture_date TEXT,
                color VARCHAR(7),
                sample_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_road_segments_tile
            ON road_segments(tile_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_road_segments_geom
            ON road_segments USING GIST(geometry)
        """)

        # Create empty_tiles table to track tiles with no OSM roads
        # Prevents the scheduler from re-processing ocean/sea tiles endlessly
        cur.execute("""
            CREATE TABLE IF NOT EXISTS empty_tiles (
                tile_id VARCHAR(20) PRIMARY KEY,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Backfill NULL tile_ids on existing rows (one-time migration).
        # Uses the same formula as _compute_tile_id().
        cur.execute("""
            UPDATE metadata SET tile_id = 'tile_' ||
                FLOOR((lon - (-8.0)) / 0.05)::int || '_' ||
                FLOOR((lat - 49.9) / 0.05)::int
            WHERE tile_id IS NULL
        """)

        # Backfill NULL priority on existing rows using geographic tile priority.
        # This ensures the reset script and scheduler can filter by priority.
        cur.execute("""
            SELECT DISTINCT tile_id FROM metadata
            WHERE priority IS NULL AND tile_id IS NOT NULL
        """)
        null_priority_tiles = [row[0] for row in cur.fetchall()]
        if null_priority_tiles:
            from geographic_scope import get_tile_priority
            priority_groups: Dict[str, list] = {}
            for tid in null_priority_tiles:
                p = get_tile_priority(tid)
                priority_groups.setdefault(p, []).append(tid)
            for prio, tile_ids in priority_groups.items():
                cur.execute("""
                    UPDATE metadata SET priority = %s
                    WHERE tile_id = ANY(%s) AND priority IS NULL
                """, (prio, tile_ids))
            logger.info("Backfilled priority for %d tiles (%s)",
                        len(null_priority_tiles),
                        {k: len(v) for k, v in priority_groups.items()})

    _conn.commit()


def get_metadata(lat: float, lon: float) -> Optional[str]:
    """Return the stored date string for a location or None."""
    if _conn is None:
        raise RuntimeError("Database not initialised")

    lat, lon = _round_coord(lat), _round_coord(lon)

    if _backend == "postgresql":
        with _conn.cursor() as cur:
            cur.execute(
                "SELECT date FROM metadata WHERE lat = %s AND lon = %s",
                (lat, lon)
            )
            row = cur.fetchone()
            return row[0] if row else None
    else:
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
    result: Dict[Tuple[float, float], Optional[str]] = {p: None for p in rounded}

    if _backend == "postgresql":
        # PostgreSQL batch query
        batch_size = 500
        for i in range(0, len(rounded), batch_size):
            batch = rounded[i: i + batch_size]
            # Use VALUES list for efficient batch lookup
            values_list = ",".join(
                f"({lat}, {lon})" for lat, lon in batch
            )
            query = f"""
                SELECT m.lat, m.lon, m.date
                FROM metadata m
                INNER JOIN (VALUES {values_list}) AS v(lat, lon)
                ON m.lat = v.lat AND m.lon = v.lon
            """
            with _conn.cursor() as cur:
                cur.execute(query)
                for row in cur.fetchall():
                    result[(row[0], row[1])] = row[2]
    else:
        # SQLite batch query
        batch_size = 500  # SQLite variable limit is 999
        for i in range(0, len(rounded), batch_size):
            batch = rounded[i: i + batch_size]
            placeholders = ",".join(["(?,?)"] * len(batch))
            params = [v for lat, lon in batch for v in (lat, lon)]
            query = f"SELECT lat, lon, date FROM metadata WHERE (lat, lon) IN (VALUES {placeholders})"
            cur = _conn.execute(query, params)
            for row in cur.fetchall():
                result[(row[0], row[1])] = row[2]

    return result


def save_metadata(lat: float, lon: float, date: str, commit: bool = True,
                  priority: str = None) -> None:
    """Save metadata to the database.

    Args:
        lat: Latitude
        lon: Longitude
        date: Date string from Street View API
        commit: Whether to commit immediately (set False for batch operations)
        priority: Priority level (high/medium/low) - only used for PostgreSQL
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")

    lat, lon = _round_coord(lat), _round_coord(lon)

    if _backend == "postgresql":
        tile_id = _compute_tile_id(lon, lat)
        with _conn.cursor() as cur:
            cur.execute("""
                INSERT INTO metadata (lat, lon, date, location, tile_id, priority, last_checked, fetched_at)
                VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (lat, lon) DO UPDATE SET
                    date = EXCLUDED.date,
                    tile_id = COALESCE(EXCLUDED.tile_id, metadata.tile_id),
                    priority = COALESCE(EXCLUDED.priority, metadata.priority),
                    last_checked = CURRENT_TIMESTAMP,
                    fetched_at = CURRENT_TIMESTAMP
            """, (lat, lon, date, lon, lat, tile_id, priority))
        if commit:
            _conn.commit()
    else:
        _conn.execute(
            "INSERT OR REPLACE INTO metadata (lat, lon, date, fetched_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (lat, lon, date),
        )
        if commit:
            _conn.commit()


def save_metadata_batch(entries: List[Tuple[float, float, str]],
                        priority: str = None) -> None:
    """Save multiple metadata entries efficiently in a single transaction.

    Args:
        entries: List of (lat, lon, date) tuples
        priority: Priority level (high/medium/low) - only used for PostgreSQL
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if not entries:
        return

    rounded = [
        (_round_coord(lat), _round_coord(lon), date) for lat, lon, date in entries
    ]

    if _backend == "postgresql":
        from psycopg2.extras import execute_values

        # Prepare data with PostGIS geometry
        data = []
        for lat, lon, date in rounded:
            tile_id = _compute_tile_id(lon, lat)
            data.append((lat, lon, date, lon, lat, tile_id, priority))

        with _conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO metadata (lat, lon, date, location, tile_id, priority, last_checked, fetched_at)
                VALUES %s
                ON CONFLICT (lat, lon) DO UPDATE SET
                    date = EXCLUDED.date,
                    tile_id = COALESCE(EXCLUDED.tile_id, metadata.tile_id),
                    priority = COALESCE(EXCLUDED.priority, metadata.priority),
                    last_checked = CURRENT_TIMESTAMP,
                    fetched_at = CURRENT_TIMESTAMP
                """,
                data,
                template="(%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        _conn.commit()
    else:
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

    if _backend == "postgresql":
        with _conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM metadata")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM metadata WHERE date IS NOT NULL")
            with_date = cur.fetchone()[0]
    else:
        cur = _conn.execute("SELECT COUNT(*) FROM metadata")
        total = cur.fetchone()[0]
        cur = _conn.execute("SELECT COUNT(*) FROM metadata WHERE date IS NOT NULL")
        with_date = cur.fetchone()[0]

    return {"total_entries": total, "entries_with_date": with_date}


def close_db() -> None:
    """Close the database connection if it is open."""
    global _conn, _backend
    if _conn is not None:
        _conn.close()
        _conn = None
    _backend = "sqlite"


# ============================================================================
# PostgreSQL-specific functions (no-op for SQLite)
# ============================================================================

def get_stale_locations(min_age_days: int, limit: int = 1000,
                        priority: str = None) -> List[Dict]:
    """Get locations that haven't been checked recently.

    Args:
        min_age_days: Minimum age in days since last check
        limit: Maximum number of results
        priority: Optional filter by priority level

    Returns:
        List of dicts with lat, lon, date, last_checked, tile_id
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")

    if _backend != "postgresql":
        # SQLite doesn't have last_checked column
        return []

    cutoff = datetime.now() - timedelta(days=min_age_days)

    with _conn.cursor() as cur:
        if priority:
            cur.execute("""
                SELECT lat, lon, date, last_checked, tile_id
                FROM metadata
                WHERE last_checked < %s AND priority = %s
                ORDER BY last_checked ASC
                LIMIT %s
            """, (cutoff, priority, limit))
        else:
            cur.execute("""
                SELECT lat, lon, date, last_checked, tile_id
                FROM metadata
                WHERE last_checked < %s
                ORDER BY last_checked ASC
                LIMIT %s
            """, (cutoff, limit))

        return [
            {
                "lat": row[0],
                "lon": row[1],
                "date": row[2],
                "last_checked": row[3],
                "tile_id": row[4]
            }
            for row in cur.fetchall()
        ]


def get_tile_coverage(tile_id: str) -> Dict:
    """Get coverage statistics for a specific tile.

    Args:
        tile_id: Tile identifier (e.g., "tile_123_456")

    Returns:
        Dict with total_points, points_with_date, oldest_check, newest_check
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")

    if _backend != "postgresql":
        return {
            "tile_id": tile_id,
            "total_points": 0,
            "points_with_date": 0,
            "oldest_check": None,
            "newest_check": None
        }

    with _conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(date) as with_date,
                MIN(last_checked) as oldest,
                MAX(last_checked) as newest
            FROM metadata
            WHERE tile_id = %s
        """, (tile_id,))

        row = cur.fetchone()
        return {
            "tile_id": tile_id,
            "total_points": row[0],
            "points_with_date": row[1],
            "oldest_check": row[2],
            "newest_check": row[3]
        }


def get_coverage_stats() -> Dict:
    """Get overall coverage statistics grouped by priority.

    Returns:
        Dict with stats by priority level and overall totals
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")

    if _backend != "postgresql":
        stats = get_cache_stats()
        return {
            "overall": stats,
            "by_priority": {}
        }

    with _conn.cursor() as cur:
        # Overall stats
        cur.execute("""
            SELECT COUNT(*), COUNT(date)
            FROM metadata
        """)
        row = cur.fetchone()
        overall = {
            "total_entries": row[0],
            "entries_with_date": row[1]
        }

        # Stats by priority
        cur.execute("""
            SELECT
                priority,
                COUNT(*) as total,
                COUNT(date) as with_date,
                COUNT(DISTINCT tile_id) as tiles
            FROM metadata
            GROUP BY priority
        """)

        by_priority = {}
        for row in cur.fetchall():
            priority = row[0] or "unset"
            by_priority[priority] = {
                "total_entries": row[1],
                "entries_with_date": row[2],
                "tiles_covered": row[3]
            }

        # Tile statistics
        cur.execute("""
            SELECT COUNT(DISTINCT tile_id) FROM metadata
        """)
        total_tiles = cur.fetchone()[0]

        return {
            "overall": overall,
            "by_priority": by_priority,
            "total_tiles_with_data": total_tiles
        }


def get_points_in_bbox(min_lon: float, min_lat: float,
                       max_lon: float, max_lat: float,
                       limit: int = 10000) -> List[Dict]:
    """Get all cached points within a bounding box using spatial index.

    Args:
        min_lon, min_lat, max_lon, max_lat: Bounding box coordinates
        limit: Maximum number of results

    Returns:
        List of dicts with lat, lon, date
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")

    if _backend == "postgresql":
        with _conn.cursor() as cur:
            cur.execute("""
                SELECT lat, lon, date
                FROM metadata
                WHERE location && ST_MakeEnvelope(%s, %s, %s, %s, 4326)::geography
                LIMIT %s
            """, (min_lon, min_lat, max_lon, max_lat, limit))

            return [
                {"lat": row[0], "lon": row[1], "date": row[2]}
                for row in cur.fetchall()
            ]
    else:
        # SQLite fallback - simple coordinate comparison
        cur = _conn.execute("""
            SELECT lat, lon, date
            FROM metadata
            WHERE lon >= ? AND lon <= ? AND lat >= ? AND lat <= ?
            LIMIT ?
        """, (min_lon, max_lon, min_lat, max_lat, limit))

        return [
            {"lat": row[0], "lon": row[1], "date": row[2]}
            for row in cur.fetchall()
        ]


def get_backend() -> str:
    """Return the current database backend name."""
    return _backend


def is_postgresql() -> bool:
    """Return True if using PostgreSQL backend."""
    return _backend == "postgresql"


# ============================================================================
# Road segments (PostgreSQL/PostGIS only)
# ============================================================================

def save_road_segments_batch(tile_id: str, segments: List[Dict]) -> int:
    """Save pre-computed road segments for a tile.

    Replaces any existing segments for the tile (delete + insert).

    Args:
        tile_id: Tile identifier
        segments: List of dicts with keys:
            osm_name, highway_type, coords (list of (lat, lon) tuples),
            capture_date, color, sample_count

    Returns:
        Number of segments saved.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql":
        return 0
    if not segments:
        return 0

    from psycopg2.extras import execute_values

    with _conn.cursor() as cur:
        # Remove old road data for this tile
        cur.execute("DELETE FROM road_segments WHERE tile_id = %s", (tile_id,))

        # Build rows: (tile_id, osm_name, highway_type, WKT, capture_date, color, sample_count)
        rows = []
        for seg in segments:
            coords = seg["coords"]  # list of (lat, lon) tuples
            if len(coords) < 2:
                continue
            # WKT LineString uses "lon lat" order
            wkt_coords = ", ".join(f"{lon} {lat}" for lat, lon in coords)
            wkt = f"SRID=4326;LINESTRING({wkt_coords})"
            rows.append((
                tile_id,
                seg.get("osm_name"),
                seg.get("highway_type"),
                wkt,
                seg.get("capture_date"),
                seg.get("color"),
                seg.get("sample_count", 0),
            ))

        if rows:
            execute_values(
                cur,
                """
                INSERT INTO road_segments
                    (tile_id, osm_name, highway_type, geometry, capture_date, color, sample_count, updated_at)
                VALUES %s
                """,
                rows,
                template="(%s, %s, %s, ST_GeomFromEWKT(%s), %s, %s, %s, CURRENT_TIMESTAMP)",
            )

    _conn.commit()
    return len(rows)


def get_road_segments_for_tile(tile_id: str) -> List[Dict]:
    """Get pre-computed road segments for a tile.

    Args:
        tile_id: Tile identifier

    Returns:
        List of dicts with keys: name, highway_type, capture_date, color,
        coordinates (list of [lon, lat] pairs in GeoJSON order).
        Returns empty list if no data or not using PostgreSQL.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql":
        return []

    with _conn.cursor() as cur:
        cur.execute("""
            SELECT osm_name, highway_type, capture_date, color,
                   ST_AsGeoJSON(geometry) as geojson
            FROM road_segments
            WHERE tile_id = %s
        """, (tile_id,))

        results = []
        for row in cur.fetchall():
            geom = json.loads(row[4])
            results.append({
                "name": row[0],
                "highway_type": row[1],
                "capture_date": row[2],
                "color": row[3],
                "coordinates": geom["coordinates"],
            })

        return results


def get_tile_summaries(tile_ids: List[str]) -> List[Dict]:
    """Get aggregated coverage summaries for a list of tiles.

    Queries the road_segments table for segment count and latest capture
    date per tile.  Used by the low-zoom summary layer.

    Args:
        tile_ids: List of tile identifiers to summarise.

    Returns:
        List of dicts with keys: tile_id, segment_count, latest_date.
        Returns empty list if not using PostgreSQL or no data found.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql" or not tile_ids:
        return []

    with _conn.cursor() as cur:
        cur.execute("""
            SELECT tile_id,
                   COUNT(*) AS segment_count,
                   MAX(capture_date) AS latest_date
            FROM road_segments
            WHERE tile_id = ANY(%s)
            GROUP BY tile_id
        """, (tile_ids,))

        return [
            {
                "tile_id": row[0],
                "segment_count": row[1],
                "latest_date": row[2],
            }
            for row in cur.fetchall()
        ]


def get_grid_summaries(west, south, east, north, resolution):
    """Aggregate road_segments into grid cells at a given resolution.

    Uses PostGIS ST_SnapToGrid to snap road segment centroids onto a
    regular grid, then groups by cell and returns the segment count and
    latest capture date per cell.  The GIST spatial index on
    road_segments.geometry makes the bounding-box filter efficient.

    Args:
        west, south, east, north: Bounding box coordinates.
        resolution: Grid cell size in degrees (e.g. 0.025).

    Returns:
        List of dicts with keys: cell_lon, cell_lat, segment_count, latest_date.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql":
        return []

    with _conn.cursor() as cur:
        cur.execute("""
            SELECT ST_X(cell) AS cell_lon,
                   ST_Y(cell) AS cell_lat,
                   COUNT(*)   AS segment_count,
                   MAX(capture_date) AS latest_date
            FROM (
                SELECT ST_SnapToGrid(ST_Centroid(geometry), %s, %s, %s, %s) AS cell,
                       capture_date
                FROM road_segments
                WHERE geometry && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
            ) sub
            GROUP BY cell
        """, (0.0, 0.0, resolution, resolution,
              west, south, east, north))

        return [
            {
                "cell_lon": row[0],
                "cell_lat": row[1],
                "segment_count": row[2],
                "latest_date": row[3],
            }
            for row in cur.fetchall()
        ]


# ============================================================================
# Empty tiles tracking (PostgreSQL only)
# ============================================================================

def save_empty_tile(tile_id: str) -> None:
    """Record a tile as having no OSM roads, so the scheduler skips it.

    Uses INSERT ... ON CONFLICT to update the timestamp if already recorded.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql":
        return

    with _conn.cursor() as cur:
        cur.execute("""
            INSERT INTO empty_tiles (tile_id, checked_at)
            VALUES (%s, CURRENT_TIMESTAMP)
            ON CONFLICT (tile_id) DO UPDATE SET checked_at = CURRENT_TIMESTAMP
        """, (tile_id,))
    _conn.commit()


def get_empty_tiles() -> set:
    """Return the set of tile IDs that have been marked as empty (no roads)."""
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql":
        return set()

    with _conn.cursor() as cur:
        cur.execute("SELECT tile_id FROM empty_tiles")
        return {row[0] for row in cur.fetchall()}


def get_tile_freshness_stats() -> Dict[str, Optional[datetime]]:
    """Return {tile_id: oldest_check_timestamp} for all checked tiles.

    Combines metadata and empty_tiles tables. Uses MIN(last_checked) per
    tile so a tile is only "fresh" if its oldest point was checked recently.
    Returns empty dict for SQLite backend.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql":
        return {}

    try:
        with _conn.cursor() as cur:
            cur.execute("""
                WITH tile_ages AS (
                    SELECT tile_id, MIN(last_checked) AS oldest_check
                    FROM metadata
                    WHERE tile_id IS NOT NULL
                    GROUP BY tile_id
                    UNION ALL
                    SELECT tile_id, checked_at AS oldest_check
                    FROM empty_tiles
                )
                SELECT tile_id, MIN(oldest_check)
                FROM tile_ages
                GROUP BY tile_id
            """)
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as e:
        try:
            _conn.rollback()
        except Exception:
            pass
        raise


def get_distinct_tile_ids() -> set:
    """Return set of distinct tile_ids that have data in the metadata table.

    Encapsulates the direct cursor access so routes don't need to touch _conn.
    Returns an empty set for SQLite backend.
    """
    if _conn is None:
        raise RuntimeError("Database not initialised")
    if _backend != "postgresql":
        return set()

    try:
        with _conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tile_id FROM metadata WHERE tile_id IS NOT NULL"
            )
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        try:
            _conn.rollback()
        except Exception:
            pass
        raise
