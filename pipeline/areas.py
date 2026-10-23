"""Named areas the pipeline can process.

An area is either a bounding box (small areas for development) or a UK shard
(a set of 1-degree cells, see shards.py). Each names the Geofabrik extract
that contains it, as a path under europe/.
"""

from typing import Dict, Tuple

from pipeline.shards import SHARD_CELLS

GEOFABRIK = "https://download.geofabrik.de/europe"

# bbox is (min_lon, min_lat, max_lon, max_lat)
AREAS: Dict[str, Dict] = {
    "leeds": {
        "bbox": (-1.65, 53.72, -1.40, 53.87),
        "extract": "united-kingdom/england/west-yorkshire",
    },
    # Small city-centre box used for the dense-vs-bisection accuracy comparison
    "leeds-centre": {
        "bbox": (-1.565, 53.788, -1.525, 53.808),
        "extract": "united-kingdom/england/west-yorkshire",
    },
    # The whole UK is sampled and tiled shard by shard, then the tiles are
    # merged and published under this name
    "uk": {"extract": "united-kingdom", "shards": list(SHARD_CELLS)},
}
AREAS.update({name: {"cells": set(cells), "extract": "united-kingdom"}
              for name, cells in SHARD_CELLS.items()})


def get_area(name: str) -> Dict:
    """Return the area definition, raising a helpful error for unknown names."""
    if name not in AREAS:
        raise SystemExit(f"Unknown area '{name}'. Choose from: {', '.join(AREAS)}")
    return AREAS[name]


def extract_name(name: str) -> str:
    """File name stem of an area's extract, e.g. 'west-yorkshire'."""
    return get_area(name)["extract"].rsplit("/", 1)[-1]


def extract_url(name: str) -> str:
    """Return the Geofabrik download URL for an area's extract."""
    return f"{GEOFABRIK}/{get_area(name)['extract']}-latest.osm.pbf"


def in_bbox(lat: float, lon: float, bbox: Tuple[float, float, float, float]) -> bool:
    """Return True if (lat, lon) falls inside bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
