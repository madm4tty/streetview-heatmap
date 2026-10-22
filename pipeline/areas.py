"""Named areas the pipeline can process.

Each area has a bounding box and the Geofabrik extract that contains it.
"""

from typing import Dict, Tuple

GEOFABRIK = "https://download.geofabrik.de/europe/united-kingdom/england"

# bbox is (min_lon, min_lat, max_lon, max_lat)
AREAS: Dict[str, Dict] = {
    "leeds": {
        "bbox": (-1.65, 53.72, -1.40, 53.87),
        "extract": "west-yorkshire",
    },
    # Small city-centre box used for the dense-vs-bisection accuracy comparison
    "leeds-centre": {
        "bbox": (-1.565, 53.788, -1.525, 53.808),
        "extract": "west-yorkshire",
    },
}


def get_area(name: str) -> Dict:
    """Return the area definition, raising a helpful error for unknown names."""
    if name not in AREAS:
        raise SystemExit(f"Unknown area '{name}'. Choose from: {', '.join(AREAS)}")
    return AREAS[name]


def extract_url(name: str) -> str:
    """Return the Geofabrik download URL for an area's extract."""
    return f"{GEOFABRIK}/{get_area(name)['extract']}-latest.osm.pbf"


def in_bbox(lat: float, lon: float, bbox: Tuple[float, float, float, float]) -> bool:
    """Return True if (lat, lon) falls inside bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
