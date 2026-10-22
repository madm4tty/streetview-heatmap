"""Small geometry helpers for measuring and cutting road polylines.

Coordinates are (lat, lon) tuples.
"""

import bisect
import math
from typing import List, Tuple

EARTH_RADIUS_M = 6_371_000

Coord = Tuple[float, float]


def haversine_m(a: Coord, b: Coord) -> float:
    """Great-circle distance in metres between two (lat, lon) points."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def cumulative_lengths(coords: List[Coord]) -> List[float]:
    """Distance in metres from the start of the line to each vertex."""
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_m(a, b))
    return cum


def point_at(coords: List[Coord], cum: List[float], dist: float) -> Coord:
    """Interpolate the point `dist` metres along the line.

    Args:
        coords: Line vertices
        cum: Output of cumulative_lengths(coords)
        dist: Distance along the line, clamped to [0, length]
    """
    if dist <= 0:
        return coords[0]
    if dist >= cum[-1]:
        return coords[-1]
    i = bisect.bisect_right(cum, dist) - 1
    span = cum[i + 1] - cum[i]
    t = 0.0 if span == 0 else (dist - cum[i]) / span
    (lat1, lon1), (lat2, lon2) = coords[i], coords[i + 1]
    return (lat1 + t * (lat2 - lat1), lon1 + t * (lon2 - lon1))


def sub_line(coords: List[Coord], cum: List[float],
             start: float, end: float) -> List[Coord]:
    """Return the part of the line between `start` and `end` metres."""
    inner = [coords[i] for i, d in enumerate(cum) if start < d < end]
    return [point_at(coords, cum, start), *inner, point_at(coords, cum, end)]
