"""Read road geometry from a Geofabrik .osm.pbf extract.

Reading a local extract avoids Overpass rate limits. Each OSM way
becomes one segment, the unit the sampler works on. Ways are deliberately
not split at junctions: in Leeds that gives a median segment of 28 m and
~70% more lookups, and bisection finds the date change points anyway.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from pipeline.areas import in_bbox
from pipeline.geo import Coord, cumulative_lengths

logger = logging.getLogger(__name__)

# Highway values that are not travelable routes (or don't exist yet)
EXCLUDED_HIGHWAYS = {
    "proposed", "construction", "abandoned", "disused", "razed",
    "platform", "bus_stop", "elevator", "corridor", "services", "rest_area",
    "raceway", "emergency_bay", "bus_guideway", "escape",
}

# footway=* values mapped as separate lines beside a road. A sample here
# would snap to the road's car imagery and give the pavement the road's date.
EXCLUDED_FOOTWAYS = {"sidewalk", "crossing"}

# Path-like ways get a tighter search radius so they are less likely to
# borrow imagery from a nearby road.
PATH_HIGHWAYS = {
    "footway", "path", "cycleway", "bridleway", "steps", "pedestrian", "track",
}


@dataclass
class Segment:
    """One OSM highway way."""
    id: str
    highway: str
    name: Optional[str]
    coords: List[Coord]
    cum: List[float] = field(default_factory=list)

    def __post_init__(self):
        if not self.cum:
            self.cum = cumulative_lengths(self.coords)

    @property
    def length(self) -> float:
        return self.cum[-1]

    @property
    def is_path(self) -> bool:
        return self.highway in PATH_HIGHWAYS

    @property
    def tight_radius(self) -> bool:
        """Search close to the way, so it doesn't borrow a nearby road's imagery.

        Paths, and service roads (driveways, car parks), which often run right
        beside a road that has imagery of its own.
        """
        return self.is_path or self.highway == "service"


def include_way(tags: Dict[str, str]) -> bool:
    """Return True if a highway way should be sampled."""
    highway = tags.get("highway")
    if not highway or highway in EXCLUDED_HIGHWAYS:
        return False
    if tags.get("area") == "yes":
        return False
    if highway == "footway" and tags.get("footway") in EXCLUDED_FOOTWAYS:
        return False
    return True


def iter_segments(pbf_path: str) -> Iterator[Segment]:
    """Yield one segment per included highway way in an .osm.pbf extract."""
    import osmium  # Imported here so tests don't need pyosmium installed

    processor = (osmium.FileProcessor(pbf_path)
                 .with_locations()
                 .with_filter(osmium.filter.KeyFilter("highway")))

    for obj in processor:
        if not obj.is_way():
            continue
        tags = {"highway": obj.tags.get("highway"), "name": obj.tags.get("name"),
                "area": obj.tags.get("area"), "footway": obj.tags.get("footway")}
        if not include_way(tags):
            continue
        try:
            coords = [(n.location.lat, n.location.lon) for n in obj.nodes]
        except osmium.InvalidLocationError:
            continue  # Way references nodes outside the extract
        if len(coords) < 2:
            continue
        seg = Segment(id=str(obj.id), highway=tags["highway"], name=tags["name"],
                      coords=coords)
        if seg.length >= 1.0:
            yield seg


def touches_bbox(seg: Segment, bbox: Tuple[float, float, float, float]) -> bool:
    return any(in_bbox(lat, lon, bbox) for lat, lon in seg.coords)


def load_segments(pbf_path: str,
                  bbox: Tuple[float, float, float, float]) -> List[Segment]:
    """Load one segment per highway way that touches bbox.

    Args:
        pbf_path: Path to an .osm.pbf extract
        bbox: (min_lon, min_lat, max_lon, max_lat)
    """
    segments = [s for s in iter_segments(pbf_path) if touches_bbox(s, bbox)]

    logger.info("Loaded %d segments (%.0f km)",
                len(segments), sum(s.length for s in segments) / 1000)
    return segments
