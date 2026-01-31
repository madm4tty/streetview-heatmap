import argparse
import asyncio
import csv
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp
import certifi
import folium
import requests
import ssl
import textwrap
from dotenv import load_dotenv

import database

# Configure logging - both console and file
LOG_FILE = "heatmap_generation.log"

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)

# File handler (overwrites each time)
file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(funcName)s - %(message)s")
file_handler.setFormatter(file_formatter)
# Ensure immediate flushing
import sys
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

# Configure root logger - this applies to all loggers
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[console_handler, file_handler]
)

logger = logging.getLogger(__name__)

# Default bounding box around Farsley, West Yorkshire (min_lon, min_lat, max_lon, max_lat)
DEFAULT_BBOX = (-1.70, 53.79, -1.65, 53.82)

AGE_COLORS = [
    (90, "#00ff00"),  # <3 months
    (365, "#ffff00"),  # <1 year
    (3 * 365, "#ffa500"),  # <3 years
    (float("inf"), "#ff0000"),  # >=3 years
]

# Road importance classification for adaptive sampling
# Maps OSM highway tags to sample multipliers
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

# Default concurrency - Google's Street View Metadata API is generous
# Free tier allows ~28,500 requests/month, which is ~950/day or ~40/hour continuous
# We can safely do 20 concurrent requests without hitting rate limits
DEFAULT_CONCURRENCY = 20


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
    """
    logger.info("Fetching OSM roads from bounding box: %s", bbox)
    min_lon, min_lat, max_lon, max_lat = bbox
    query = textwrap.dedent(
        """
        [out:json];
        (
          way['highway']({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out geom;
    """
    ).format(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    url = "https://overpass-api.de/api/interpreter"
    logger.debug("Overpass query: %s", query.strip())

    for attempt in range(retries):
        try:
            resp = requests.get(url, params={"data": query}, timeout=60)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt < retries - 1:
                delay = retry_delay * (2**attempt)
                logger.warning(
                    "Overpass API error (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt + 1,
                    retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                logger.error("Failed to fetch OSM data after %d attempts: %s", retries, exc)
                return []

    data = resp.json()
    roads = []
    highway_type_counts = {}
    for elem in data.get("elements", []):
        geom = elem.get("geometry")
        if geom:
            coords = [(pt["lat"], pt["lon"]) for pt in geom]
            tags = elem.get("tags", {})
            name = tags.get("name")
            highway_type = tags.get("highway")
            roads.append((coords, name, highway_type))
            # Track road types
            highway_type_counts[highway_type] = highway_type_counts.get(highway_type, 0) + 1

    logger.info("Fetched %d roads from Overpass API", len(roads))
    logger.debug("Road type distribution: %s", dict(sorted(highway_type_counts.items(), key=lambda x: x[1], reverse=True)))
    return roads


def fetch_streetview_metadata(lat: float, lon: float, api_key: str) -> dict:
    """Fetch Street View metadata for a single point (synchronous fallback)."""
    url = "https://maps.googleapis.com/maps/api/streetview/metadata"
    params = {"location": f"{lat},{lon}", "key": api_key}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Error fetching Street View metadata for %s,%s: %s", lat, lon, exc)
        return {}
    return resp.json()


def sample_coords(
    coords: List[Tuple[float, float]], n: int
) -> List[Tuple[float, float]]:
    """Return up to n evenly spaced points from coords."""
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

    More important roads (motorways, primary roads) get more samples.
    Less important roads (footways, paths) get fewer samples.
    """
    multiplier = ROAD_IMPORTANCE.get(highway_type, 1.0)
    adaptive_count = int(base_samples * multiplier)
    # Ensure at least 1 sample, at most the number of coordinates
    return max(1, min(adaptive_count, coord_count))


async def fetch_missing_metadata(
    points: List[Tuple[float, float]],
    api_key: str,
    concurrency: int,
) -> Dict[Tuple[float, float], Optional[str]]:
    """Fetch metadata for points not already cached.

    Returns a dict mapping (lat, lon) -> date string or None.
    Also saves results to database in batches.
    """
    if not points:
        logger.debug("No missing points to fetch")
        return {}

    # Deduplicate points
    unique_points = list(set(points))
    logger.info("Fetching metadata for %d unique points (concurrency=%d)", len(unique_points), concurrency)
    start_time = time.time()

    sem = asyncio.Semaphore(max(1, concurrency))
    url = "https://maps.googleapis.com/maps/api/streetview/metadata"
    results: Dict[Tuple[float, float], Optional[str]] = {}
    batch_to_save: List[Tuple[float, float, str]] = []
    batch_lock = asyncio.Lock()
    completed_count = [0]  # Use list for mutable counter in async context
    last_log_time = [time.time()]
    status_counts = {}  # Track API response statuses

    # Create SSL context using certifi's CA bundle
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:

        async def fetch_point(lat: float, lon: float) -> None:
            params = {"location": f"{lat},{lon}", "key": api_key}
            try:
                async with sem:
                    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
            except Exception as exc:
                logger.warning("Error fetching %s,%s: %s (type: %s)", lat, lon, exc, type(exc).__name__)
                results[(lat, lon)] = None
                completed_count[0] += 1
                # Track errors
                async with batch_lock:
                    status_counts["ERROR"] = status_counts.get("ERROR", 0) + 1
                # Log progress every 5 seconds
                if time.time() - last_log_time[0] >= 5:
                    logger.info("Progress: %d/%d points fetched (%.1f%%)",
                               completed_count[0], len(unique_points),
                               (completed_count[0] / len(unique_points) * 100))
                    last_log_time[0] = time.time()
                return

            status = data.get("status")
            date = data.get("date") if status == "OK" else None
            results[(lat, lon)] = date
            completed_count[0] += 1

            # Track status codes
            async with batch_lock:
                status_counts[status] = status_counts.get(status, 0) + 1

            # Debug: log unexpected statuses
            if status != "OK":
                logger.debug("Point %s,%s returned status: %s (data: %s)", lat, lon, status, data)

            # Log progress every 5 seconds
            if time.time() - last_log_time[0] >= 5:
                logger.info("Progress: %d/%d points fetched (%.1f%%)",
                           completed_count[0], len(unique_points),
                           (completed_count[0] / len(unique_points) * 100))
                last_log_time[0] = time.time()

            if date:
                async with batch_lock:
                    batch_to_save.append((lat, lon, date))
                    # Batch save every 100 entries
                    if len(batch_to_save) >= 100:
                        entries = batch_to_save.copy()
                        batch_to_save.clear()
                        # Run database save in thread pool to avoid blocking
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, database.save_metadata_batch, entries)
                        logger.debug("Saved batch of 100 entries to database")

        logger.debug("Creating %d async tasks...", len(unique_points))
        tasks = [asyncio.create_task(fetch_point(lat, lon)) for lat, lon in unique_points]
        logger.debug("Gathering results...")
        await asyncio.gather(*tasks, return_exceptions=True)

    # Save any remaining entries
    if batch_to_save:
        database.save_metadata_batch(batch_to_save)

    elapsed = time.time() - start_time
    results_with_dates = sum(1 for v in results.values() if v)
    logger.info("Fetched %d results, %d with dates in %.1fs (%.1f points/sec)",
                len(results), results_with_dates, elapsed, len(results) / elapsed if elapsed > 0 else 0)
    logger.info("API response status distribution: %s", status_counts)
    logger.debug("Success rate: %.1f%%", (results_with_dates / len(results) * 100) if results else 0)
    return results


def parse_date(date_str: str) -> datetime:
    """Parse date string from Street View API."""
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unknown date format: {date_str}")


def age_to_color(date_str: str) -> str:
    """Convert date string to color based on age."""
    from datetime import timezone
    capture_date = parse_date(date_str)
    # Use timezone-aware datetime
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    age_days = (now_utc - capture_date).days
    for limit, color in AGE_COLORS:
        if age_days <= limit:
            return color
    return "#ff0000"


def create_map(
    roads: List[Tuple[List[Tuple[float, float]], str, Optional[str]]],
    center: Tuple[float, float],
) -> folium.Map:
    """Return a Folium map with colored road segments and tooltips."""
    m = folium.Map(location=center, zoom_start=14)
    for coords, date, name in roads:
        color = age_to_color(date)
        tooltip = None
        try:
            tooltip_date = parse_date(date).strftime("%d/%m/%Y")
        except ValueError:
            tooltip_date = date
        if name or tooltip_date:
            road_name = name if name else "Unknown"
            tooltip = f"{road_name}<br>{tooltip_date}"
        folium.PolyLine(
            coords, color=color, weight=4, opacity=0.9, tooltip=tooltip
        ).add_to(m)
    legend_html = """
    <div style="position: fixed; bottom: 50px; left: 50px; width: 150px; background: white; padding: 10px; border: 1px solid #ccc; z-index: 1000;">
      <b>Image Age</b><br>
      <i style="background:#00ff00;width:10px;height:10px;display:inline-block"></i> <3 months<br>
      <i style="background:#ffff00;width:10px;height:10px;display:inline-block"></i> <1 year<br>
      <i style="background:#ffa500;width:10px;height:10px;display:inline-block"></i> <3 years<br>
      <i style="background:#ff0000;width:10px;height:10px;display:inline-block"></i> >=3 years
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m


def generate_for_bbox(
    bbox: Tuple[float, float, float, float],
    step: float,
    output: str,
    csv_path: Optional[str],
    db_path: str,
    api_key: str,
    samples: int = 5,
    concurrency: int = DEFAULT_CONCURRENCY,
    adaptive_sampling: bool = True,
) -> None:
    """Generate a heatmap for a single bounding box.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat)
        step: Grid step size (unused, kept for backward compatibility)
        output: Output HTML file path
        csv_path: Optional CSV output path
        db_path: SQLite database path for caching
        api_key: Google Maps API key
        samples: Base number of sample points per road
        concurrency: Maximum concurrent API requests
        adaptive_sampling: Whether to adjust samples based on road importance
    """
    logger.info("=" * 60)
    logger.info("Starting heatmap generation")
    logger.info("Bounding box: %s", bbox)
    logger.info("Output file: %s", output)
    logger.info("Database: %s", db_path)
    logger.info("Base samples per road: %d", samples)
    logger.info("Concurrency: %d", concurrency)
    logger.info("Adaptive sampling: %s", adaptive_sampling)
    logger.info("=" * 60)

    start_time = time.time()
    database.init_db(db_path)
    logger.debug("Database initialized")
    roads = fetch_osm_roads(bbox)

    if not roads:
        logger.warning("No roads found in bounding box")
        return

    sample_size = max(1, samples)

    # Pre-compute sampled coordinates for each road (avoid sampling twice)
    logger.info("Sampling points from roads...")
    road_samples: List[Tuple[List[Tuple[float, float]], Optional[str], Optional[str], List[Tuple[float, float]]]] = []
    all_sample_points: List[Tuple[float, float]] = []
    sampling_stats = {"total_samples": 0, "min_samples": float('inf'), "max_samples": 0}

    for coords, name, highway_type in roads:
        if adaptive_sampling:
            n_samples = get_adaptive_sample_count(highway_type, sample_size, len(coords))
        else:
            n_samples = sample_size
        sampled = sample_coords(coords, n_samples)
        road_samples.append((coords, name, highway_type, sampled))
        all_sample_points.extend(sampled)

        # Track stats
        sampling_stats["total_samples"] += len(sampled)
        sampling_stats["min_samples"] = min(sampling_stats["min_samples"], len(sampled))
        sampling_stats["max_samples"] = max(sampling_stats["max_samples"], len(sampled))

    avg_samples = sampling_stats["total_samples"] / len(roads) if roads else 0
    logger.info("Sampling complete: %d total points (avg: %.1f, min: %d, max: %d per road)",
                sampling_stats["total_samples"], avg_samples,
                sampling_stats["min_samples"], sampling_stats["max_samples"])

    # Deduplicate all sample points
    unique_points = list(set(all_sample_points))
    logger.info(
        "Processing %d roads with %d unique sample points",
        len(roads),
        len(unique_points),
    )

    # Batch query cache for all points at once
    logger.info("Querying database cache for %d points...", len(unique_points))
    cached = database.get_metadata_batch(unique_points)

    # Find points not in cache
    missing_points = [p for p in unique_points if cached.get(p) is None]
    cache_hit_rate = ((len(unique_points) - len(missing_points)) / len(unique_points) * 100) if unique_points else 0
    logger.info("Cache hit: %d (%.1f%%), Cache miss: %d",
                len(unique_points) - len(missing_points), cache_hit_rate, len(missing_points))

    # Fetch missing points asynchronously
    if missing_points:
        fetched = asyncio.run(fetch_missing_metadata(missing_points, api_key, concurrency))
        # Merge fetched results into cached
        cached.update(fetched)

    # Process results - find latest date for each road
    logger.info("Processing results to find latest imagery date per road...")
    road_results: List[Tuple[List[Tuple[float, float]], str, Optional[str]]] = []
    roads_processed = 0
    for coords, name, highway_type, sampled in road_samples:
        roads_processed += 1
        if roads_processed % 100 == 0:
            logger.debug("Processed %d/%d roads...", roads_processed, len(road_samples))
        latest: Optional[datetime] = None
        for lat, lon in sampled:
            # Round to match database precision
            rounded_point = (round(lat, database.COORD_PRECISION), round(lon, database.COORD_PRECISION))
            date_str = cached.get(rounded_point)

            # Fallback to individual lookup if not in batch result
            if date_str is None:
                date_str = database.get_metadata(lat, lon)

            if date_str:
                try:
                    d = parse_date(date_str)
                    if not latest or d > latest:
                        latest = d
                except ValueError:
                    logger.debug("Invalid date format: %s", date_str)
                    continue

        if latest:
            road_results.append((coords, latest.strftime("%Y-%m-%d"), name))

    if not road_results:
        logger.warning("No imagery found for any roads")
        return

    logger.info("Generated results for %d/%d roads (%.1f%% coverage)",
                len(road_results), len(roads),
                (len(road_results) / len(roads) * 100) if roads else 0)

    logger.info("Creating map visualization...")
    center = [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2]
    m = create_map(road_results, center)
    m.save(output)
    logger.info("Saved map to %s", output)

    if csv_path:
        logger.info("Writing CSV data...")
        with open(csv_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["lat", "lon", "date", "road_name"])
            row_count = 0
            for coords, date, name in road_results:
                for lat, lon in coords:
                    writer.writerow([lat, lon, date, name or ""])
                    row_count += 1
        logger.info("Saved CSV to %s (%d rows)", csv_path, row_count)

    # Log cache statistics
    stats = database.get_cache_stats()
    logger.info("Cache stats: %d total entries, %d with dates", stats["total_entries"], stats["entries_with_date"])

    total_time = time.time() - start_time
    logger.info("=" * 60)
    logger.info("Heatmap generation complete in %.1fs", total_time)
    logger.info("=" * 60)


def main():
    logger.info("Street View Heatmap Generator started")
    parser = argparse.ArgumentParser(
        description="Generate Street View imagery age heatmap"
    )
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        default=DEFAULT_BBOX,
        help="Bounding box to sample (default is Farsley)",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.005,
        help="Grid step size in degrees (legacy, now uses road-based sampling)",
    )
    parser.add_argument("--output", default="heatmap.html", help="Output HTML file")
    parser.add_argument("--csv", default=None, help="Optional CSV output path")
    parser.add_argument("--db", default=None, help="Path to metadata cache database")
    parser.add_argument(
        "--samples", type=int, default=5, help="Base sample points per road"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Concurrent Street View requests (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--no-adaptive",
        action="store_true",
        help="Disable adaptive sampling based on road importance",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    # Configure logging level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Load environment variables from a .env file if present
    load_dotenv()

    api_key_env = os.getenv("GMAPS_APIKEY")
    if api_key_env:
        logger.info("API key loaded from GMAPS_APIKEY")
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", api_key_env)

    bbox = tuple(args.bbox)

    db_path = args.db or os.environ.get("HEATMAP_DB", "metadata.db")
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        logger.error("GOOGLE_MAPS_API_KEY environment variable not set")
        sys.exit(1)

    generate_for_bbox(
        bbox,
        args.step,
        args.output,
        args.csv,
        db_path,
        api_key,
        args.samples,
        args.concurrency,
        adaptive_sampling=not args.no_adaptive,
    )
    database.close_db()

    # Ensure logs are flushed
    logging.shutdown()


if __name__ == "__main__":
    main()
