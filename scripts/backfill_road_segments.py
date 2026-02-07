#!/usr/bin/env python3
"""Backfill road_segments table for tiles that already have point data.

This script populates the road_segments table by:
1. Finding tiles that have point data in `metadata` but no roads in `road_segments`
2. For each tile: fetching OSM road geometries from the Overpass API
3. Using PostGIS spatial proximity (ST_DWithin) to match roads to nearby
   existing metadata points and assign Street View capture dates
4. Saving road LineString geometries with dates into `road_segments`

No Google API calls are made — existing point dates from `metadata` are reused.
Overpass API calls are rate-limited with a configurable delay.

Usage:
    python3 scripts/backfill_road_segments.py [--limit N] [--delay SECONDS] [--dry-run]

Examples:
    python3 scripts/backfill_road_segments.py --limit 5 --dry-run
    python3 scripts/backfill_road_segments.py --limit 50 --delay 2
    python3 scripts/backfill_road_segments.py  # Process all tiles
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from geographic_scope import get_tile_bbox
from app.processing import (
    fetch_osm_roads,
    age_to_color,
    parse_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Maximum distance in metres to match a metadata point to a road
MATCH_DISTANCE_METRES = 30


def get_tiles_needing_backfill() -> list:
    """Find tile_ids that have point data but no road segments."""
    if not database.is_postgresql():
        logger.error("Backfill requires PostgreSQL with PostGIS")
        return []

    with database._conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT m.tile_id
            FROM metadata m
            WHERE m.tile_id IS NOT NULL
              AND m.date IS NOT NULL
              AND m.tile_id NOT IN (
                  SELECT DISTINCT tile_id FROM road_segments
              )
            ORDER BY m.tile_id
        """)
        return [row[0] for row in cur.fetchall()]


def backfill_tile(tile_id: str) -> dict:
    """Backfill road segments for a single tile using spatial proximity.

    For each OSM road in the tile, uses PostGIS ST_DWithin to find existing
    metadata points within MATCH_DISTANCE_METRES of the road's LineString.
    The most recent capture date from nearby points becomes the road's date.

    No Google API calls are made.

    Returns:
        Dict with stats about what was done.
    """
    bbox = get_tile_bbox(tile_id)

    # Fetch road geometries from Overpass
    roads = fetch_osm_roads(bbox)
    if not roads:
        return {"tile_id": tile_id, "roads_found": 0, "roads_saved": 0}

    # For each road, do a spatial proximity query against metadata
    segments = []
    points_matched = 0

    for coords, name, highway_type in roads:
        if len(coords) < 2:
            continue

        # Build WKT for spatial query (lon lat order for PostGIS)
        wkt_coords = ", ".join(f"{lon} {lat}" for lat, lon in coords)
        line_wkt = f"SRID=4326;LINESTRING({wkt_coords})"

        # Find metadata points within MATCH_DISTANCE_METRES of this road
        with database._conn.cursor() as cur:
            cur.execute("""
                SELECT date FROM metadata
                WHERE date IS NOT NULL
                  AND ST_DWithin(
                      location,
                      ST_GeomFromEWKT(%s)::geography,
                      %s
                  )
                ORDER BY date DESC
                LIMIT 10
            """, (line_wkt, MATCH_DISTANCE_METRES))
            rows = cur.fetchall()

        if not rows:
            continue

        # Find the latest date among matching points
        latest: Optional[datetime] = None
        for (date_str,) in rows:
            try:
                d = parse_date(date_str)
                if not latest or d > latest:
                    latest = d
            except ValueError:
                continue

        if latest:
            points_matched += len(rows)
            date_str = latest.strftime("%Y-%m-%d")
            segments.append({
                "osm_name": name,
                "highway_type": highway_type,
                "coords": coords,
                "capture_date": date_str,
                "color": age_to_color(date_str),
                "sample_count": len(rows),
            })

    # Save all segments for this tile in one batch
    roads_saved = database.save_road_segments_batch(tile_id, segments)

    return {
        "tile_id": tile_id,
        "roads_found": len(roads),
        "roads_saved": roads_saved,
        "points_matched": points_matched,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Backfill road_segments for tiles with existing point data"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max tiles to process (0 = all, default: 0)"
    )
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Delay in seconds between Overpass API requests (default: 1.5)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes"
    )
    args = parser.parse_args()

    # Initialise database
    database.init_db()
    if not database.is_postgresql():
        logger.error("This script requires PostgreSQL. Set DATABASE_URL.")
        sys.exit(1)

    # Find tiles needing backfill
    tiles = get_tiles_needing_backfill()
    if args.limit > 0:
        tiles = tiles[:args.limit]

    logger.info("Found %d tile(s) needing road segment backfill", len(tiles))

    if not tiles:
        logger.info("Nothing to do — all tiles with data already have road segments")
        return

    if args.dry_run:
        logger.info("DRY RUN — would process these tiles:")
        for t in tiles:
            logger.info("  %s", t)
        return

    # Process tiles
    total_roads_saved = 0
    errors = 0

    for i, tile_id in enumerate(tiles, 1):
        logger.info("[%d/%d] Processing %s ...", i, len(tiles), tile_id)

        try:
            result = backfill_tile(tile_id)
            total_roads_saved += result["roads_saved"]
            logger.info(
                "  -> %d roads found, %d saved (%d metadata points matched)",
                result["roads_found"],
                result["roads_saved"],
                result["points_matched"],
            )
        except Exception as e:
            logger.error("  -> ERROR: %s", e)
            errors += 1

        # Rate-limit Overpass API requests
        if i < len(tiles) and args.delay > 0:
            time.sleep(args.delay)

    logger.info("=" * 60)
    logger.info(
        "Backfill complete: %d tiles processed, %d road segments saved, %d errors",
        len(tiles), total_roads_saved, errors,
    )


if __name__ == "__main__":
    main()
