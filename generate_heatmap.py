import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime

# typing imports are required for the type hints used throughout the
# script.  Tuple is particularly important for function signatures
# like ``sample_grid``.
from typing import List, Optional, Tuple

import database
from dotenv import load_dotenv
import requests
import textwrap
import folium
import aiohttp

# Default bounding box around Farsley, West Yorkshire (min_lon, min_lat, max_lon, max_lat)
DEFAULT_BBOX = (-1.70, 53.79, -1.65, 53.82)

AGE_COLORS = [
    (90, '#00ff00'),    # <3 months
    (365, '#ffff00'),   # <1 year
    (3*365, '#ffa500'), # <3 years
    (float('inf'), '#ff0000')  # >=3 years
]


def sample_grid(bbox: Tuple[float, float, float, float], step: float = 0.005) -> List[Tuple[float, float]]:
    """Return a list of (lat, lon) points within ``bbox`` at ``step`` degree increments.

    ``step`` must be a positive number. A ``ValueError`` is raised for ``step``
    values less than or equal to zero.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    min_lon, min_lat, max_lon, max_lat = bbox
    lats = []
    lat = min_lat
    while lat <= max_lat:
        lats.append(lat)
        lat += step
    lons = []
    lon = min_lon
    while lon <= max_lon:
        lons.append(lon)
        lon += step
    points = []
    for la in lats:
        for lo in lons:
            points.append((la, lo))
    return points

def fetch_osm_roads(bbox: Tuple[float, float, float, float]) -> List[List[Tuple[float, float]]]:
    """Download highway geometries from the Overpass API."""
    min_lon, min_lat, max_lon, max_lat = bbox
    query = textwrap.dedent("""
        [out:json];
        (
          way['highway']({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out geom;
    """).format(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    url = "https://overpass-api.de/api/interpreter"
    try:
        resp = requests.get(url, params={"data": query}, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f'Error fetching OSM data: {exc}', file=sys.stderr)
        return []
    data = resp.json()
    roads = []
    for elem in data.get("elements", []):
        geom = elem.get("geometry")
        if geom:
            coords = [(pt["lat"], pt["lon"]) for pt in geom]
            roads.append(coords)
    return roads


def fetch_streetview_metadata(lat: float, lon: float, api_key: str) -> dict:
    url = 'https://maps.googleapis.com/maps/api/streetview/metadata'
    params = {'location': f'{lat},{lon}', 'key': api_key}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(
            f"Error fetching Street View metadata for {lat},{lon}: {exc}",
            file=sys.stderr,
        )
        return {}
    return resp.json()


def sample_coords(coords: List[Tuple[float, float]], n: int) -> List[Tuple[float, float]]:
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


async def fetch_missing_metadata(points: List[Tuple[float, float]], api_key: str, concurrency: int) -> None:
    """Fetch metadata for points not already cached."""
    if not points:
        return

    sem = asyncio.Semaphore(max(1, concurrency))
    url = "https://maps.googleapis.com/maps/api/streetview/metadata"

    async with aiohttp.ClientSession() as session:
        async def fetch_point(lat: float, lon: float) -> None:
            params = {"location": f"{lat},{lon}", "key": api_key}
            try:
                async with sem:
                    async with session.get(url, params=params, timeout=10) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
            except Exception as exc:  # pragma: no cover - network errors
                print(f"Error fetching {lat},{lon}: {exc}", file=sys.stderr)
                return
            date = data.get("date") if data.get("status") == "OK" else None
            if date:
                database.save_metadata(lat, lon, date)

        tasks = [asyncio.create_task(fetch_point(lat, lon)) for lat, lon in points]
        await asyncio.gather(*tasks)


def parse_date(date_str: str) -> datetime:
    for fmt in ('%Y-%m-%d', '%Y-%m'):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f'Unknown date format: {date_str}')


def age_to_color(date_str: str) -> str:
    capture_date = parse_date(date_str)
    age_days = (datetime.utcnow() - capture_date).days
    for limit, color in AGE_COLORS:
        if age_days <= limit:
            return color
    return '#ff0000'



def create_map(roads: List[Tuple[List[Tuple[float, float]], str]], center: Tuple[float, float]) -> folium.Map:
    """Return a Folium map with colored road segments."""
    m = folium.Map(location=center, zoom_start=14)
    for coords, date in roads:
        color = age_to_color(date)
        folium.PolyLine(coords, color=color, weight=4, opacity=0.9).add_to(m)
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
    concurrency: int = 5,
) -> None:
    """Generate a heatmap for a single bounding box."""

    database.init_db(db_path)
    roads = fetch_osm_roads(bbox)
    sample_size = max(1, samples)
    missing_points: List[Tuple[float, float]] = []
    for coords in roads:
        for lat, lon in sample_coords(coords, sample_size):
            if database.get_metadata(lat, lon) is None:
                missing_points.append((lat, lon))

    if missing_points:
        asyncio.run(fetch_missing_metadata(missing_points, api_key, concurrency))
    road_results: List[Tuple[List[Tuple[float, float]], str]] = []
    for coords in roads:
        latest: Optional[datetime] = None
        for lat, lon in sample_coords(coords, sample_size):
            date_str = database.get_metadata(lat, lon)
            if date_str is None:
                data = fetch_streetview_metadata(lat, lon, api_key)
                if not data:
                    continue
                if data.get("status") == "OK" and "date" in data:
                    date_str = data["date"]
                    database.save_metadata(lat, lon, date_str)
            if date_str:
                d = parse_date(date_str)
                if not latest or d > latest:
                    latest = d
        if latest:
            road_results.append((coords, latest.strftime("%Y-%m-%d")))

    if not road_results:
        print("No imagery found")
        return

    center = [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2]
    m = create_map(road_results, center)
    m.save(output)
    print(f'Saved {output}')

    if csv_path:
        with open(csv_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["lat", "lon", "date"])

            for coords, date in road_results:
                for lat, lon in coords:
                    writer.writerow([lat, lon, date])

        print(f"Saved {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate Street View imagery age heatmap")
    parser.add_argument("--bbox", type=float, nargs=4, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                        default=DEFAULT_BBOX,
                        help="Bounding box to sample (default is Farsley)")
    parser.add_argument("--step", type=float, default=0.005, help="Grid step size in degrees")
    parser.add_argument("--output", default="heatmap.html", help="Output HTML file")
    parser.add_argument("--csv", default=None, help="Optional CSV output path")
    parser.add_argument("--db", default=None, help="Path to metadata cache database")
    parser.add_argument("--samples", type=int, default=5, help="Max sample points per road")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent Street View requests")
    args = parser.parse_args()

    # Load environment variables from a .env file if present
    load_dotenv()

    api_key_env = os.getenv("GMAPS_APIKEY")
    if api_key_env:
        print("API key loaded")
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", api_key_env)
    else:
        print("API key not found")

    bbox = tuple(args.bbox)

    db_path = args.db or os.environ.get("HEATMAP_DB", "metadata.db")
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("GOOGLE_MAPS_API_KEY environment variable not set", file=sys.stderr)
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
    )
    database.close_db()


if __name__ == '__main__':
    main()
