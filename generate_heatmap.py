import argparse
import csv
import os
import sys
from datetime import datetime

# typing imports are required for the type hints used throughout the
# script.  Tuple is particularly important for function signatures
# like ``sample_grid``.
from typing import List, Optional, Tuple
from dotenv import load_dotenv
import requests
import textwrap
import folium

# Default bounding box around Farsley, West Yorkshire (min_lon, min_lat, max_lon, max_lat)
DEFAULT_BBOX = (-1.70, 53.79, -1.65, 53.82)

AGE_COLORS = [
    (90, '#00ff00'),    # <3 months
    (365, '#ffff00'),   # <1 year
    (3*365, '#ffa500'), # <3 years
    (float('inf'), '#ff0000')  # >=3 years
]


def sample_grid(bbox: Tuple[float, float, float, float], step: float = 0.005) -> List[Tuple[float, float]]:
    """Return a list of (lat, lon) points within bbox at the given step."""
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
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


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
    <div style="position: fixed; bottom: 50px; left: 50px; width: 150px; background: white; padding: 10px; border: 1px solid #ccc;">
      <b>Image Age</b><br>
      <i style="background:#00ff00;width:10px;height:10px;display:inline-block"></i> <3 months<br>
      <i style="background:#ffff00;width:10px;height:10px;display:inline-block"></i> <1 year<br>
      <i style="background:#ffa500;width:10px;height:10px;display:inline-block"></i> <3 years<br>
      <i style="background:#ff0000;width:10px;height:10px;display:inline-block"></i> >=3 years
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m


def main():
    parser = argparse.ArgumentParser(description="Generate Street View imagery age heatmap")
    parser.add_argument("--bbox", type=float, nargs=4, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                        default=DEFAULT_BBOX,
                        help="Bounding box to sample (default is Farsley)")
    parser.add_argument("--step", type=float, default=0.005, help="Grid step size in degrees")
    parser.add_argument("--output", default="heatmap.html", help="Output HTML file")
    parser.add_argument("--csv", default=None, help="Optional CSV output path")
    args = parser.parse_args()


    # Load environment variables from a .env file if present
    load_dotenv()


    bbox = tuple(args.bbox)
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        print('GOOGLE_MAPS_API_KEY environment variable not set', file=sys.stderr)
        sys.exit(1)

    roads = fetch_osm_roads(bbox)
    road_results: List[Tuple[List[Tuple[float, float]], str]] = []
    for coords in roads:
        latest: Optional[datetime] = None
        for lat, lon in coords:
            data = fetch_streetview_metadata(lat, lon, api_key)
            if data.get('status') == 'OK' and 'date' in data:
                d = parse_date(data['date'])
                if not latest or d > latest:
                    latest = d
        if latest:
            road_results.append((coords, latest.strftime('%Y-%m-%d')))

    if not road_results:
        print('No imagery found')
        return

    center = [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2]
    m = create_map(road_results, center)
    m.save(args.output)
    print(f'Saved {args.output}')

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["lat", "lon", "date"])

            for coords, date in road_results:
                for lat, lon in coords:
                    writer.writerow([lat, lon, date])

        print(f'Saved {args.csv}')


if __name__ == '__main__':
