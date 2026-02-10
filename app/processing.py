"""Core processing logic for Street View heatmap generation.

This module provides reusable functions for:
- Fetching OSM road data
- Querying Street View metadata
- Processing tiles and bounding boxes
- Generating GeoJSON for visualization
"""

import asyncio
import logging
import ssl
import textwrap
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import certifi
import requests

import database
from geographic_scope import (
    generate_tile_id,
    get_tile_bbox,
    get_tile_priority,
    TILE_SIZE,
)

logger = logging.getLogger(__name__)

# Age-based colors for visualization (days, color)
AGE_COLORS = [
    (90, "#00ff00"),      # < 3 months - green
    (365, "#ffff00"),     # < 1 year - yellow
    (3 * 365, "#ffa500"), # < 3 years - orange
    (float("inf"), "#ff0000"),  # >= 3 years - red
]

# Road importance multipliers for adaptive sampling
ROAD_IMPORTANCE = {
    "motorway": 3.0,
    "motorway_link": 2.0,
    "trunk": 2.5,
    "trunk_link": 2.0,
    "primary": 2.0,
    "primary_link": 1.5,
    "secondary": 1.5,
    "secondary_link": 1.2,
    "tertiary": 1.2,
    "tertiary_link": 1.0,
    "residential": 1.0,
    "unclassified": 0.8,
    "service": 0.5,
    "living_street": 0.8,
    "pedestrian": 0.3,
    "footway": 0.2,
    "cycleway": 0.3,
    "path": 0.2,
    "track": 0.3,
}


def fetch_osm_roads(
    bbox: Tuple[float, float, float, float],
    retries: int = 3,
    retry_delay: float = 2.0,
) -> List[Tuple[List[Tuple[float, float]], Optional[str], Optional[str]]]:
    """Download highway geometries, names, and types from the Overpass API.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat)
        retries: Number of retry attempts on failure
        retry_delay: Base delay between retries (exponential backoff)

    Returns:
        List of (coordinates, name, highway_type) tuples
        where coordinates is a list of (lat, lon) tuples
    """
    logger.info("Fetching OSM roads from bbox: %s", bbox)
    min_lon, min_lat, max_lon, max_lat = bbox

    query = textwrap.dedent("""
        [out:json];
        (
          way['highway']({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out geom;
    """).format(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)

    url = "https://overpass-api.de/api/interpreter"

    for attempt in range(retries):
        try:
            resp = requests.get(url, params={"data": query}, timeout=60)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt < retries - 1:
                delay = retry_delay * (2 ** attempt)
                logger.warning(
                    "Overpass API error (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt + 1, retries, exc, delay
                )
                time.sleep(delay)
            else:
                logger.error("Failed to fetch OSM data after %d attempts: %s", retries, exc)
                return []

    data = resp.json()
    roads = []

    for elem in data.get("elements", []):
        geom = elem.get("geometry")
        if geom:
            coords = [(pt["lat"], pt["lon"]) for pt in geom]
            tags = elem.get("tags", {})
            name = tags.get("name")
            highway_type = tags.get("highway")
            roads.append((coords, name, highway_type))

    logger.info("Fetched %d roads from Overpass API", len(roads))
    return roads


def sample_coords(
    coords: List[Tuple[float, float]], n: int
) -> List[Tuple[float, float]]:
    """Return up to n evenly spaced points from coords.

    Args:
        coords: List of (lat, lon) coordinate tuples
        n: Maximum number of samples to return

    Returns:
        List of evenly-spaced coordinate samples
    """
    if n <= 0 or n >= len(coords):
        return coords
    if n == 1:
        return [coords[len(coords) // 2]]

    indices = [round(i * (len(coords) - 1) / (n - 1)) for i in range(n)]
    seen = set()
    result = []
    for idx in indices:
        if idx not in seen:
            result.append(coords[idx])
            seen.add(idx)
    return result


def get_adaptive_sample_count(
    highway_type: Optional[str], base_samples: int, coord_count: int
) -> int:
    """Calculate sample count based on road importance.

    More important roads (motorways, primary) get more samples.
    Less important roads (footways, paths) get fewer samples.

    Args:
        highway_type: OSM highway type
        base_samples: Base number of samples
        coord_count: Total coordinates available

    Returns:
        Adjusted sample count
    """
    multiplier = ROAD_IMPORTANCE.get(highway_type, 1.0)
    adaptive_count = int(base_samples * multiplier)
    return max(1, min(adaptive_count, coord_count))


def parse_date(date_str: str) -> datetime:
    """Parse date string from Street View API.

    Args:
        date_str: Date in YYYY-MM-DD or YYYY-MM format

    Returns:
        Parsed datetime object

    Raises:
        ValueError: If date format is unrecognized
    """
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unknown date format: {date_str}")


def age_to_color(date_str: str) -> str:
    """Convert date string to color based on imagery age.

    Args:
        date_str: Date in YYYY-MM-DD or YYYY-MM format

    Returns:
        Hex color code based on age
    """
    try:
        capture_date = parse_date(date_str)
    except ValueError:
        return "#808080"  # Gray for invalid dates

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    age_days = (now_utc - capture_date).days

    for limit, color in AGE_COLORS:
        if age_days <= limit:
            return color
    return "#ff0000"


async def fetch_streetview_metadata_batch(
    points: List[Tuple[float, float]],
    api_key: str,
    concurrency: int = 20,
    batch_save_size: int = 100,
    on_progress: Optional[callable] = None,
    priority: Optional[str] = None,
) -> Dict[Tuple[float, float], Optional[str]]:
    """Fetch Street View metadata for multiple points asynchronously.

    Args:
        points: List of (lat, lon) tuples
        api_key: Google Maps API key
        concurrency: Maximum concurrent requests
        batch_save_size: Save to database every N entries
        on_progress: Optional callback(completed, total) for progress updates
        priority: Tile priority level (high/medium/low) for DB storage

    Returns:
        Dict mapping (lat, lon) -> date string or None
    """
    if not points:
        return {}

    unique_points = list(set(points))
    logger.info("Fetching metadata for %d unique points (concurrency=%d)",
                len(unique_points), concurrency)

    start_time = time.time()
    sem = asyncio.Semaphore(max(1, concurrency))
    url = "https://maps.googleapis.com/maps/api/streetview/metadata"

    results: Dict[Tuple[float, float], Optional[str]] = {}
    batch_to_save: List[Tuple[float, float, str]] = []
    batch_lock = asyncio.Lock()
    completed_count = [0]
    status_counts: Dict[str, int] = {}

    # SSL context with proper certificates
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:

        async def fetch_point(lat: float, lon: float) -> None:
            params = {"location": f"{lat},{lon}", "key": api_key}
            try:
                async with sem:
                    async with session.get(
                        url, params=params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
            except Exception as exc:
                logger.warning("Error fetching %s,%s: %s", lat, lon, type(exc).__name__)
                results[(lat, lon)] = None
                completed_count[0] += 1
                async with batch_lock:
                    status_counts["ERROR"] = status_counts.get("ERROR", 0) + 1
                return

            status = data.get("status")
            date = data.get("date") if status == "OK" else None
            results[(lat, lon)] = date
            completed_count[0] += 1

            async with batch_lock:
                status_counts[status] = status_counts.get(status, 0) + 1

                if date:
                    batch_to_save.append((lat, lon, date))
                    if len(batch_to_save) >= batch_save_size:
                        entries = batch_to_save.copy()
                        batch_to_save.clear()
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, database.save_metadata_batch, entries, priority
                        )

            if on_progress:
                on_progress(completed_count[0], len(unique_points))

        tasks = [asyncio.create_task(fetch_point(lat, lon))
                 for lat, lon in unique_points]
        await asyncio.gather(*tasks, return_exceptions=True)

    # Save remaining entries
    if batch_to_save:
        database.save_metadata_batch(batch_to_save, priority=priority)

    elapsed = time.time() - start_time
    results_with_dates = sum(1 for v in results.values() if v)
    logger.info("Fetched %d results, %d with dates in %.1fs",
                len(results), results_with_dates, elapsed)
    logger.debug("API status distribution: %s", status_counts)

    return results


def process_tile(
    tile_id: str,
    api_key: str,
    samples_per_road: int = 5,
    concurrency: int = 20,
    adaptive_sampling: bool = True,
    overpass_delay: float = 0.0,
) -> Dict[str, Any]:
    """Process a single tile: fetch OSM roads, query Street View, update DB.

    Args:
        tile_id: Tile identifier (e.g., "tile_123_456")
        api_key: Google Maps API key
        samples_per_road: Base samples per road segment
        concurrency: Concurrent API requests
        adaptive_sampling: Adjust samples based on road importance
        overpass_delay: Delay before querying Overpass (rate limiting)

    Returns:
        Dictionary with processing results:
        {
            "tile_id": str,
            "roads_found": int,
            "locations_checked": int,
            "locations_updated": int,
            "api_calls": int,
            "duration_seconds": float
        }
    """
    start_time = time.time()
    bbox = get_tile_bbox(tile_id)
    priority = get_tile_priority(tile_id)

    logger.info("Processing tile %s (priority: %s, bbox: %s)",
                tile_id, priority, bbox)

    # Apply Overpass delay if specified
    if overpass_delay > 0:
        time.sleep(overpass_delay)

    # Fetch roads from Overpass
    roads = fetch_osm_roads(bbox)

    if not roads:
        return {
            "tile_id": tile_id,
            "roads_found": 0,
            "locations_checked": 0,
            "locations_updated": 0,
            "api_calls": 0,
            "duration_seconds": time.time() - start_time
        }

    # Sample points from roads, tracking which samples belong to which road
    all_sample_points: List[Tuple[float, float]] = []
    road_samples: List[Tuple[List[Tuple[float, float]], Optional[str], Optional[str], List[Tuple[float, float]]]] = []

    for coords, name, highway_type in roads:
        if adaptive_sampling:
            n_samples = get_adaptive_sample_count(highway_type, samples_per_road, len(coords))
        else:
            n_samples = samples_per_road
        sampled = sample_coords(coords, n_samples)
        road_samples.append((coords, name, highway_type, sampled))
        all_sample_points.extend(sampled)

    unique_points = list(set(all_sample_points))
    logger.info("Sampled %d unique points from %d roads", len(unique_points), len(roads))

    # Check database cache
    cached = database.get_metadata_batch(unique_points)
    missing_points = [p for p in unique_points if cached.get(p) is None]

    logger.info("Cache: %d hits, %d misses",
                len(unique_points) - len(missing_points), len(missing_points))

    # Fetch missing points
    api_calls = 0
    if missing_points:
        fetched = asyncio.run(fetch_streetview_metadata_batch(
            missing_points, api_key, concurrency, priority=priority
        ))
        api_calls = len(missing_points)
        cached.update(fetched)

    # Count locations with dates
    locations_updated = sum(1 for v in cached.values() if v)

    # Save road geometries with dates if using PostgreSQL
    roads_saved = 0
    if database.is_postgresql():
        roads_saved = _save_road_segments(tile_id, road_samples, cached)

    duration = time.time() - start_time
    logger.info("Tile %s complete: %d roads (%d saved), %d locations, %d API calls, %.1fs",
                tile_id, len(roads), roads_saved, len(unique_points), api_calls, duration)

    return {
        "tile_id": tile_id,
        "roads_found": len(roads),
        "roads_saved": roads_saved,
        "locations_checked": len(unique_points),
        "locations_updated": locations_updated,
        "api_calls": api_calls,
        "duration_seconds": duration
    }


def _save_road_segments(
    tile_id: str,
    road_samples: List[Tuple[List[Tuple[float, float]], Optional[str], Optional[str], List[Tuple[float, float]]]],
    date_lookup: Dict[Tuple[float, float], Optional[str]],
) -> int:
    """Save road geometries with computed dates to the database.

    For each road, finds the latest Street View date among its sampled points
    and stores the full LineString geometry with that date.

    Args:
        tile_id: Tile identifier
        road_samples: List of (coords, name, highway_type, sampled_points)
        date_lookup: Dict mapping (lat, lon) -> date string

    Returns:
        Number of road segments saved.
    """
    segments = []
    for coords, name, highway_type, sampled in road_samples:
        # Find the latest date among this road's sampled points
        latest: Optional[datetime] = None
        for lat, lon in sampled:
            rounded = (round(lat, database.COORD_PRECISION),
                       round(lon, database.COORD_PRECISION))
            date_str = date_lookup.get(rounded)
            if date_str:
                try:
                    d = parse_date(date_str)
                    if not latest or d > latest:
                        latest = d
                except ValueError:
                    continue

        if latest:
            date_str = latest.strftime("%Y-%m-%d")
            segments.append({
                "osm_name": name,
                "highway_type": highway_type,
                "coords": coords,
                "capture_date": date_str,
                "color": age_to_color(date_str),
                "sample_count": len(sampled),
            })

    return database.save_road_segments_batch(tile_id, segments)


def fetch_and_process_bbox(
    bbox: Tuple[float, float, float, float],
    api_key: str,
    samples_per_road: int = 5,
    concurrency: int = 20,
    adaptive_sampling: bool = True,
) -> Dict[str, Any]:
    """Fetch OSM roads for bbox, sample points, query Street View, return results.

    Similar to process_tile but for arbitrary bounding boxes.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat)
        api_key: Google Maps API key
        samples_per_road: Base samples per road segment
        concurrency: Concurrent API requests
        adaptive_sampling: Adjust samples based on road importance

    Returns:
        Dictionary with:
        {
            "bbox": tuple,
            "roads": [{"coords": [...], "name": str, "date": str, "color": str}, ...],
            "stats": {...}
        }
    """
    start_time = time.time()

    # Fetch roads
    roads = fetch_osm_roads(bbox)

    if not roads:
        return {
            "bbox": bbox,
            "roads": [],
            "stats": {
                "roads_found": 0,
                "locations_checked": 0,
                "locations_with_data": 0,
                "api_calls": 0,
                "duration_seconds": time.time() - start_time
            }
        }

    # Sample and collect points
    road_samples: List[Tuple[List[Tuple[float, float]], Optional[str], Optional[str], List[Tuple[float, float]]]] = []
    all_sample_points: List[Tuple[float, float]] = []

    for coords, name, highway_type in roads:
        if adaptive_sampling:
            n_samples = get_adaptive_sample_count(highway_type, samples_per_road, len(coords))
        else:
            n_samples = samples_per_road
        sampled = sample_coords(coords, n_samples)
        road_samples.append((coords, name, highway_type, sampled))
        all_sample_points.extend(sampled)

    unique_points = list(set(all_sample_points))

    # Check cache and fetch missing
    cached = database.get_metadata_batch(unique_points)
    missing_points = [p for p in unique_points if cached.get(p) is None]

    api_calls = 0
    if missing_points:
        fetched = asyncio.run(fetch_streetview_metadata_batch(
            missing_points, api_key, concurrency
        ))
        api_calls = len(missing_points)
        cached.update(fetched)

    # Build road results with dates
    processed_roads = []

    for coords, name, highway_type, sampled in road_samples:
        # Find latest date among sampled points
        latest: Optional[datetime] = None
        for lat, lon in sampled:
            rounded = (round(lat, database.COORD_PRECISION),
                      round(lon, database.COORD_PRECISION))
            date_str = cached.get(rounded)
            if date_str:
                try:
                    d = parse_date(date_str)
                    if not latest or d > latest:
                        latest = d
                except ValueError:
                    continue

        if latest:
            date_str = latest.strftime("%Y-%m-%d")
            processed_roads.append({
                "coords": coords,
                "name": name,
                "highway_type": highway_type,
                "date": date_str,
                "color": age_to_color(date_str)
            })

    duration = time.time() - start_time

    return {
        "bbox": bbox,
        "roads": processed_roads,
        "stats": {
            "roads_found": len(roads),
            "roads_with_data": len(processed_roads),
            "locations_checked": len(unique_points),
            "locations_with_data": sum(1 for v in cached.values() if v),
            "api_calls": api_calls,
            "duration_seconds": duration
        }
    }


def get_tile_geojson(tile_id: str) -> Dict[str, Any]:
    """Generate GeoJSON for a tile's roads with dates and colors.

    Args:
        tile_id: Tile identifier (e.g., "tile_123_456")

    Returns:
        Standard GeoJSON FeatureCollection with road LineStrings
    """
    bbox = get_tile_bbox(tile_id)

    # Get cached points in the bbox
    min_lon, min_lat, max_lon, max_lat = bbox
    points = database.get_points_in_bbox(min_lon, min_lat, max_lon, max_lat)

    if not points:
        return {
            "type": "FeatureCollection",
            "features": [],
            "properties": {
                "tile_id": tile_id,
                "bbox": bbox,
                "point_count": 0
            }
        }

    # Build features from points with dates
    features = []
    for point in points:
        if point.get("date"):
            color = age_to_color(point["date"])
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [point["lon"], point["lat"]]
                },
                "properties": {
                    "date": point["date"],
                    "color": color
                }
            })

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "tile_id": tile_id,
            "bbox": bbox,
            "point_count": len(features)
        }
    }


def get_tile_road_geojson(
    tile_id: str,
    api_key: str,
    samples_per_road: int = 5,
    concurrency: int = 20,
    adaptive_sampling: bool = True,
) -> Dict[str, Any]:
    """Generate GeoJSON with road LineStrings for a tile.

    This fetches road geometry from OSM and adds date/color properties.

    Args:
        tile_id: Tile identifier
        api_key: Google Maps API key
        samples_per_road: Base samples per road
        concurrency: Concurrent API requests
        adaptive_sampling: Adjust samples by road importance

    Returns:
        GeoJSON FeatureCollection with LineString features
    """
    bbox = get_tile_bbox(tile_id)
    result = fetch_and_process_bbox(
        bbox, api_key, samples_per_road, concurrency, adaptive_sampling
    )

    features = []
    for road in result["roads"]:
        # Convert coords to GeoJSON format [lon, lat]
        coordinates = [[lon, lat] for lat, lon in road["coords"]]

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates
            },
            "properties": {
                "name": road["name"],
                "highway_type": road["highway_type"],
                "date": road["date"],
                "color": road["color"]
            }
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "tile_id": tile_id,
            "bbox": bbox,
            "road_count": len(features),
            "stats": result["stats"]
        }
    }


def get_tile_road_geojson_from_db(tile_id: str) -> Optional[Dict[str, Any]]:
    """Get pre-computed road LineString GeoJSON for a tile from the database.

    Returns None if no road data exists for the tile (e.g. tile hasn't been
    processed yet with road segment storage enabled).

    Args:
        tile_id: Tile identifier

    Returns:
        GeoJSON FeatureCollection with LineString features, or None.
    """
    segments = database.get_road_segments_for_tile(tile_id)
    if not segments:
        return None

    features = []
    for seg in segments:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": seg["coordinates"],
            },
            "properties": {
                "name": seg["name"],
                "highway_type": seg["highway_type"],
                "date": seg["capture_date"],
                "color": seg["color"],
            }
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "tile_id": tile_id,
            "road_count": len(features),
        }
    }
