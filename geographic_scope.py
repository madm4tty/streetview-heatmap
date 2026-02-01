"""Geographic scope definitions for UK-wide Street View coverage.

This module defines:
- UK major cities with bounding boxes and priorities
- Road type priorities for sampling
- Tile system for dividing UK into manageable chunks
"""

from typing import Dict, List, Tuple

# UK bounding box (covers mainland UK including Scotland)
UK_BOUNDS = (-8.0, 49.9, 2.0, 60.9)  # (min_lon, min_lat, max_lon, max_lat)

# Tile size in degrees (~5km x 5km at UK latitudes)
TILE_SIZE = 0.05

# UK Major Cities with bounding boxes and priorities
# Format: {"city_name": {"bbox": (min_lon, min_lat, max_lon, max_lat), "priority": "high/medium/low"}}
UK_MAJOR_CITIES: Dict[str, Dict] = {
    # High priority - Major metropolitan areas
    "london": {"bbox": (-0.51, 51.28, 0.33, 51.69), "priority": "high"},
    "birmingham": {"bbox": (-2.05, 52.38, -1.73, 52.58), "priority": "high"},
    "manchester": {"bbox": (-2.35, 53.35, -2.15, 53.55), "priority": "high"},
    "leeds": {"bbox": (-1.65, 53.72, -1.40, 53.87), "priority": "high"},
    "glasgow": {"bbox": (-4.40, 55.80, -4.10, 55.95), "priority": "high"},
    "liverpool": {"bbox": (-3.05, 53.35, -2.85, 53.50), "priority": "high"},
    "newcastle": {"bbox": (-1.70, 54.95, -1.55, 55.05), "priority": "high"},
    "sheffield": {"bbox": (-1.55, 53.33, -1.35, 53.45), "priority": "high"},
    "bristol": {"bbox": (-2.70, 51.40, -2.50, 51.52), "priority": "high"},
    "edinburgh": {"bbox": (-3.35, 55.90, -3.10, 56.00), "priority": "high"},

    # High priority - Other major cities
    "cardiff": {"bbox": (-3.25, 51.45, -3.10, 51.55), "priority": "high"},
    "belfast": {"bbox": (-6.00, 54.55, -5.85, 54.65), "priority": "high"},
    "nottingham": {"bbox": (-1.22, 52.90, -1.08, 53.00), "priority": "high"},
    "leicester": {"bbox": (-1.18, 52.60, -1.05, 52.68), "priority": "high"},
    "coventry": {"bbox": (-1.58, 52.38, -1.45, 52.45), "priority": "high"},

    # Medium priority - Regional centres
    "bradford": {"bbox": (-1.82, 53.77, -1.70, 53.83), "priority": "medium"},
    "stoke_on_trent": {"bbox": (-2.22, 52.97, -2.10, 53.07), "priority": "medium"},
    "wolverhampton": {"bbox": (-2.18, 52.55, -2.08, 52.62), "priority": "medium"},
    "plymouth": {"bbox": (-4.20, 50.35, -4.08, 50.42), "priority": "medium"},
    "southampton": {"bbox": (-1.47, 50.88, -1.35, 50.95), "priority": "medium"},
    "reading": {"bbox": (-1.02, 51.42, -0.92, 51.48), "priority": "medium"},
    "derby": {"bbox": (-1.52, 52.90, -1.42, 52.96), "priority": "medium"},
    "hull": {"bbox": (-0.42, 53.72, -0.30, 53.78), "priority": "medium"},
    "portsmouth": {"bbox": (-1.12, 50.78, -1.02, 50.85), "priority": "medium"},
    "luton": {"bbox": (-0.48, 51.86, -0.38, 51.92), "priority": "medium"},
    "preston": {"bbox": (-2.75, 53.74, -2.65, 53.80), "priority": "medium"},
    "aberdeen": {"bbox": (-2.20, 57.10, -2.05, 57.20), "priority": "medium"},
    "dundee": {"bbox": (-3.05, 56.45, -2.90, 56.50), "priority": "medium"},
    "swansea": {"bbox": (-4.00, 51.60, -3.90, 51.66), "priority": "medium"},
    "york": {"bbox": (-1.12, 53.93, -1.02, 53.99), "priority": "medium"},
    "oxford": {"bbox": (-1.30, 51.73, -1.20, 51.78), "priority": "medium"},
    "cambridge": {"bbox": (0.08, 52.18, 0.18, 52.23), "priority": "medium"},
    "norwich": {"bbox": (1.25, 52.62, 1.35, 52.68), "priority": "medium"},
    "brighton": {"bbox": (-0.18, 50.82, -0.08, 50.86), "priority": "medium"},
    "peterborough": {"bbox": (-0.28, 52.55, -0.18, 52.62), "priority": "medium"},
    "sunderland": {"bbox": (-1.42, 54.88, -1.32, 54.93), "priority": "medium"},
    "middlesbrough": {"bbox": (-1.28, 54.55, -1.18, 54.60), "priority": "medium"},
    "blackpool": {"bbox": (-3.08, 53.78, -2.98, 53.85), "priority": "medium"},
    "bolton": {"bbox": (-2.48, 53.55, -2.38, 53.62), "priority": "medium"},
    "ipswich": {"bbox": (1.12, 52.05, 1.22, 52.10), "priority": "medium"},
    "exeter": {"bbox": (-3.58, 50.70, -3.48, 50.75), "priority": "medium"},
    "gloucester": {"bbox": (-2.28, 51.85, -2.18, 51.90), "priority": "medium"},
    "bath": {"bbox": (-2.42, 51.37, -2.32, 51.42), "priority": "medium"},
    "chester": {"bbox": (-2.93, 53.17, -2.85, 53.22), "priority": "medium"},

    # Medium priority - Additional towns
    "blackburn": {"bbox": (-2.52, 53.73, -2.44, 53.78), "priority": "medium"},
    "stockport": {"bbox": (-2.18, 53.38, -2.10, 53.43), "priority": "medium"},
    "rochdale": {"bbox": (-2.18, 53.60, -2.10, 53.65), "priority": "medium"},
    "oldham": {"bbox": (-2.15, 53.53, -2.05, 53.58), "priority": "medium"},
    "warrington": {"bbox": (-2.62, 53.37, -2.55, 53.42), "priority": "medium"},
    "wigan": {"bbox": (-2.68, 53.53, -2.60, 53.58), "priority": "medium"},
    "huddersfield": {"bbox": (-1.82, 53.63, -1.74, 53.68), "priority": "medium"},
    "wakefield": {"bbox": (-1.52, 53.67, -1.44, 53.72), "priority": "medium"},
    "doncaster": {"bbox": (-1.18, 53.50, -1.08, 53.56), "priority": "medium"},
    "rotherham": {"bbox": (-1.40, 53.41, -1.32, 53.46), "priority": "medium"},
    "barnsley": {"bbox": (-1.52, 53.53, -1.44, 53.58), "priority": "medium"},
    "grimsby": {"bbox": (-0.12, 53.55, -0.02, 53.60), "priority": "medium"},
    "lincoln": {"bbox": (-0.58, 53.22, -0.50, 53.27), "priority": "medium"},
    "mansfield": {"bbox": (-1.22, 53.13, -1.15, 53.17), "priority": "medium"},
    "chesterfield": {"bbox": (-1.48, 53.22, -1.40, 53.27), "priority": "medium"},
    "worcester": {"bbox": (-2.25, 52.18, -2.17, 52.22), "priority": "medium"},
    "hereford": {"bbox": (-2.75, 52.03, -2.68, 52.08), "priority": "medium"},
    "shrewsbury": {"bbox": (-2.78, 52.70, -2.70, 52.74), "priority": "medium"},
    "telford": {"bbox": (-2.55, 52.66, -2.45, 52.72), "priority": "medium"},
    "walsall": {"bbox": (-2.02, 52.57, -1.95, 52.62), "priority": "medium"},
    "dudley": {"bbox": (-2.12, 52.50, -2.05, 52.54), "priority": "medium"},
    "solihull": {"bbox": (-1.82, 52.40, -1.75, 52.45), "priority": "medium"},
    "sutton_coldfield": {"bbox": (-1.85, 52.55, -1.78, 52.60), "priority": "medium"},
    "nuneaton": {"bbox": (-1.50, 52.50, -1.43, 52.54), "priority": "medium"},
    "northampton": {"bbox": (-0.95, 52.22, -0.85, 52.28), "priority": "medium"},
    "bedford": {"bbox": (-0.50, 52.12, -0.42, 52.16), "priority": "medium"},
    "milton_keynes": {"bbox": (-0.82, 52.00, -0.70, 52.07), "priority": "medium"},
    "aylesbury": {"bbox": (-0.85, 51.80, -0.78, 51.84), "priority": "medium"},
    "colchester": {"bbox": (0.87, 51.88, 0.95, 52.92), "priority": "medium"},
    "chelmsford": {"bbox": (0.45, 51.72, 0.52, 51.76), "priority": "medium"},
    "basildon": {"bbox": (0.42, 51.56, 0.50, 51.60), "priority": "medium"},
    "southend": {"bbox": (0.68, 51.52, 0.76, 51.56), "priority": "medium"},
    "maidstone": {"bbox": (0.50, 51.26, 0.58, 51.30), "priority": "medium"},
    "canterbury": {"bbox": (1.05, 51.26, 1.12, 51.30), "priority": "medium"},
    "ashford": {"bbox": (0.85, 51.13, 0.92, 51.17), "priority": "medium"},
    "hastings": {"bbox": (0.55, 50.85, 0.62, 50.88), "priority": "medium"},
    "eastbourne": {"bbox": (0.25, 50.76, 0.32, 50.80), "priority": "medium"},
    "worthing": {"bbox": (-0.40, 50.80, -0.33, 50.83), "priority": "medium"},
    "crawley": {"bbox": (-0.22, 51.10, -0.15, 51.14), "priority": "medium"},
    "guildford": {"bbox": (-0.60, 51.22, -0.53, 51.26), "priority": "medium"},
    "woking": {"bbox": (-0.58, 51.30, -0.52, 51.34), "priority": "medium"},
    "basingstoke": {"bbox": (-1.12, 51.25, -1.05, 51.28), "priority": "medium"},
    "winchester": {"bbox": (-1.35, 51.05, -1.28, 51.08), "priority": "medium"},
    "bournemouth": {"bbox": (-1.92, 50.72, -1.82, 50.78), "priority": "medium"},
    "poole": {"bbox": (-2.02, 50.70, -1.95, 50.74), "priority": "medium"},
    "salisbury": {"bbox": (-1.82, 51.05, -1.75, 51.09), "priority": "medium"},
    "swindon": {"bbox": (-1.82, 51.54, -1.72, 51.60), "priority": "medium"},
    "cheltenham": {"bbox": (-2.12, 51.88, -2.05, 51.92), "priority": "medium"},
    "taunton": {"bbox": (-3.15, 51.00, -3.08, 51.04), "priority": "medium"},
    "yeovil": {"bbox": (-2.68, 50.93, -2.62, 50.96), "priority": "medium"},
    "torquay": {"bbox": (-3.55, 50.45, -3.48, 50.50), "priority": "medium"},
    "truro": {"bbox": (-5.08, 50.25, -5.02, 50.28), "priority": "medium"},
    "stirling": {"bbox": (-3.98, 56.10, -3.90, 56.14), "priority": "medium"},
    "inverness": {"bbox": (-4.28, 57.45, -4.18, 57.50), "priority": "medium"},
    "perth": {"bbox": (-3.48, 56.38, -3.40, 56.42), "priority": "medium"},

    # Low priority - Smaller towns
    "barrow_in_furness": {"bbox": (-3.25, 54.10, -3.18, 54.14), "priority": "low"},
    "carlisle": {"bbox": (-2.98, 54.88, -2.90, 54.92), "priority": "low"},
    "lancaster": {"bbox": (-2.82, 54.03, -2.75, 54.07), "priority": "low"},
    "kendal": {"bbox": (-2.78, 54.32, -2.72, 54.35), "priority": "low"},
    "penrith": {"bbox": (-2.78, 54.65, -2.72, 54.68), "priority": "low"},
    "whitehaven": {"bbox": (-3.60, 54.53, -3.54, 54.56), "priority": "low"},
    "workington": {"bbox": (-3.58, 54.63, -3.52, 54.66), "priority": "low"},
    "dumfries": {"bbox": (-3.62, 55.05, -3.55, 55.09), "priority": "low"},
    "ayr": {"bbox": (-4.65, 55.45, -4.58, 55.48), "priority": "low"},
    "kilmarnock": {"bbox": (-4.52, 55.60, -4.45, 55.63), "priority": "low"},
    "greenock": {"bbox": (-4.80, 55.93, -4.73, 55.97), "priority": "low"},
    "paisley": {"bbox": (-4.48, 55.83, -4.40, 55.87), "priority": "low"},
    "motherwell": {"bbox": (-4.00, 55.77, -3.93, 55.80), "priority": "low"},
    "east_kilbride": {"bbox": (-4.22, 55.75, -4.15, 55.78), "priority": "low"},
    "livingston": {"bbox": (-3.55, 55.88, -3.48, 55.91), "priority": "low"},
    "falkirk": {"bbox": (-3.82, 55.98, -3.75, 56.02), "priority": "low"},
    "kirkcaldy": {"bbox": (-3.18, 56.10, -3.12, 56.13), "priority": "low"},
    "dunfermline": {"bbox": (-3.48, 56.06, -3.42, 56.09), "priority": "low"},
    "fort_william": {"bbox": (-5.12, 56.81, -5.05, 56.84), "priority": "low"},
    "oban": {"bbox": (-5.50, 56.40, -5.45, 56.43), "priority": "low"},
    "llandudno": {"bbox": (-3.85, 53.32, -3.80, 53.35), "priority": "low"},
    "bangor_wales": {"bbox": (-4.15, 53.22, -4.10, 53.24), "priority": "low"},
    "wrexham": {"bbox": (-3.02, 53.03, -2.95, 53.06), "priority": "low"},
    "newport": {"bbox": (-3.02, 51.57, -2.95, 51.60), "priority": "low"},
    "merthyr_tydfil": {"bbox": (-3.42, 51.74, -3.36, 51.77), "priority": "low"},
    "bridgend": {"bbox": (-3.60, 51.50, -3.55, 51.53), "priority": "low"},
    "neath": {"bbox": (-3.82, 51.65, -3.76, 51.68), "priority": "low"},
    "port_talbot": {"bbox": (-3.82, 51.58, -3.76, 51.61), "priority": "low"},
    "llanelli": {"bbox": (-4.18, 51.67, -4.12, 51.70), "priority": "low"},
    "aberystwyth": {"bbox": (-4.10, 52.40, -4.05, 52.43), "priority": "low"},
    "newtown_wales": {"bbox": (-3.32, 52.50, -3.27, 52.53), "priority": "low"},
}

# Road type priorities for OSM highway classification
# Higher priority roads get more sampling points
PRIORITY_ROADS: Dict[str, List[str]] = {
    "high": ["motorway", "trunk", "primary"],
    "medium": ["secondary", "tertiary"],
    "low": ["residential", "unclassified"],
}

# All road types by priority (flattened for easy lookup)
ALL_ROAD_TYPES: List[str] = (
    PRIORITY_ROADS["high"] + PRIORITY_ROADS["medium"] + PRIORITY_ROADS["low"]
)

# Road type to priority mapping
ROAD_TYPE_TO_PRIORITY: Dict[str, str] = {}
for priority, roads in PRIORITY_ROADS.items():
    for road in roads:
        ROAD_TYPE_TO_PRIORITY[road] = priority


def get_road_priority(highway_type: str) -> str:
    """Get the priority level for a given OSM highway type.

    Args:
        highway_type: OSM highway tag value (e.g., "motorway", "residential")

    Returns:
        Priority level: "high", "medium", or "low"
    """
    return ROAD_TYPE_TO_PRIORITY.get(highway_type, "low")


def generate_tile_id(lon: float, lat: float) -> str:
    """Generate a unique tile ID for a given coordinate.

    Tiles are 0.05° x 0.05° (~5km x 5km at UK latitudes).
    Format: "tile_{lon_idx}_{lat_idx}" where indices are from UK_BOUNDS origin.

    Args:
        lon: Longitude
        lat: Latitude

    Returns:
        Tile ID string (e.g., "tile_123_456")
    """
    min_lon, min_lat, _, _ = UK_BOUNDS
    lon_idx = int((lon - min_lon) / TILE_SIZE)
    lat_idx = int((lat - min_lat) / TILE_SIZE)
    return f"tile_{lon_idx}_{lat_idx}"


def get_tile_bbox(tile_id: str) -> Tuple[float, float, float, float]:
    """Get the bounding box for a tile ID.

    Args:
        tile_id: Tile ID string (e.g., "tile_123_456")

    Returns:
        Tuple of (min_lon, min_lat, max_lon, max_lat)
    """
    parts = tile_id.split("_")
    lon_idx = int(parts[1])
    lat_idx = int(parts[2])

    min_lon, min_lat, _, _ = UK_BOUNDS

    tile_min_lon = min_lon + (lon_idx * TILE_SIZE)
    tile_min_lat = min_lat + (lat_idx * TILE_SIZE)
    tile_max_lon = tile_min_lon + TILE_SIZE
    tile_max_lat = tile_min_lat + TILE_SIZE

    return (tile_min_lon, tile_min_lat, tile_max_lon, tile_max_lat)


def generate_uk_tiles() -> List[Dict]:
    """Generate all tiles covering the UK.

    Returns:
        List of dicts with tile_id, bbox, and default priority
    """
    min_lon, min_lat, max_lon, max_lat = UK_BOUNDS
    tiles = []

    lon = min_lon
    while lon < max_lon:
        lat = min_lat
        while lat < max_lat:
            tile_id = generate_tile_id(lon + TILE_SIZE/2, lat + TILE_SIZE/2)
            bbox = (lon, lat, lon + TILE_SIZE, lat + TILE_SIZE)
            tiles.append({
                "tile_id": tile_id,
                "bbox": bbox,
                "priority": get_tile_priority(tile_id)
            })
            lat += TILE_SIZE
        lon += TILE_SIZE

    return tiles


def get_tile_priority(tile_id: str) -> str:
    """Determine the priority of a tile based on whether it contains a city.

    Args:
        tile_id: Tile ID string

    Returns:
        Priority level: "high", "medium", or "low"
    """
    tile_bbox = get_tile_bbox(tile_id)

    # Check if tile overlaps with any city
    for city_name, city_data in UK_MAJOR_CITIES.items():
        city_bbox = city_data["bbox"]
        if _bboxes_overlap(tile_bbox, city_bbox):
            # Return the highest priority found
            if city_data["priority"] == "high":
                return "high"

    # Second pass for medium priority cities
    for city_name, city_data in UK_MAJOR_CITIES.items():
        city_bbox = city_data["bbox"]
        if _bboxes_overlap(tile_bbox, city_bbox):
            if city_data["priority"] == "medium":
                return "medium"

    return "low"


def _bboxes_overlap(bbox1: Tuple[float, float, float, float],
                    bbox2: Tuple[float, float, float, float]) -> bool:
    """Check if two bounding boxes overlap.

    Args:
        bbox1: First bounding box (min_lon, min_lat, max_lon, max_lat)
        bbox2: Second bounding box (min_lon, min_lat, max_lon, max_lat)

    Returns:
        True if boxes overlap, False otherwise
    """
    min_lon1, min_lat1, max_lon1, max_lat1 = bbox1
    min_lon2, min_lat2, max_lon2, max_lat2 = bbox2

    # Check for no overlap conditions
    if max_lon1 < min_lon2 or max_lon2 < min_lon1:
        return False
    if max_lat1 < min_lat2 or max_lat2 < min_lat1:
        return False

    return True


def get_priority_bboxes(priority: str) -> List[Tuple[float, float, float, float]]:
    """Get all city bounding boxes for a given priority level.

    Args:
        priority: "high", "medium", or "low"

    Returns:
        List of bounding box tuples
    """
    return [
        city_data["bbox"]
        for city_data in UK_MAJOR_CITIES.values()
        if city_data["priority"] == priority
    ]


def get_cities_by_priority(priority: str) -> Dict[str, Dict]:
    """Get all cities with a given priority level.

    Args:
        priority: "high", "medium", or "low"

    Returns:
        Dict of city name -> city data for matching cities
    """
    return {
        name: data
        for name, data in UK_MAJOR_CITIES.items()
        if data["priority"] == priority
    }


def get_tile_count() -> int:
    """Get the total number of tiles covering the UK."""
    min_lon, min_lat, max_lon, max_lat = UK_BOUNDS
    lon_count = int((max_lon - min_lon) / TILE_SIZE)
    lat_count = int((max_lat - min_lat) / TILE_SIZE)
    return lon_count * lat_count


def get_city_tiles(city_name: str) -> List[str]:
    """Get all tile IDs that overlap with a city's bounding box.

    Args:
        city_name: Name of the city (lowercase, as in UK_MAJOR_CITIES)

    Returns:
        List of tile IDs that overlap with the city
    """
    if city_name not in UK_MAJOR_CITIES:
        return []

    city_bbox = UK_MAJOR_CITIES[city_name]["bbox"]
    min_lon, min_lat, max_lon, max_lat = city_bbox

    tiles = []
    lon = min_lon
    while lon < max_lon:
        lat = min_lat
        while lat < max_lat:
            tile_id = generate_tile_id(lon, lat)
            if tile_id not in tiles:
                tiles.append(tile_id)
            lat += TILE_SIZE
        lon += TILE_SIZE

    # Also check corners to ensure full coverage
    for corner_lon, corner_lat in [
        (min_lon, min_lat), (max_lon, min_lat),
        (min_lon, max_lat), (max_lon, max_lat)
    ]:
        tile_id = generate_tile_id(corner_lon, corner_lat)
        if tile_id not in tiles:
            tiles.append(tile_id)

    return tiles
