"""Hexagon overview for low zooms, and the numbers behind the page's "in view" panel
and update digest.

Each shard tallies road length per H3 hexagon (resolution 6, about 36 km²) and per
value: a capture month, "none" or "other". Tallies add up across shards, so a
hexagon split by a shard boundary comes out whole once merged. The overview
then turns the merged tallies into polygons with the length in each age band,
and repeats them at resolution 5 (about 250 km²) for the most zoomed-out view.

H3 is Uber's global hexagon grid: every point has one cell ID per resolution.
"""

import json
from bisect import bisect_right
from collections import defaultdict
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import h3

from pipeline.export import month_index
from pipeline.geo import point_at
from pipeline.roads import Segment
from pipeline.sampler import Piece
from pipeline.streetview import NO_COVERAGE, NON_GOOGLE

# Upper bounds in months of every age band but the last; matches AGE_BANDS in web/index.html
BAND_EDGES_MONTHS = (6, 12, 36, 60, 120)
STATS_RES = 6
COARSE_RES = 5
# The update digest names the areas with the most new imagery: resolution-4
# hexagons, about 1,800 km² (roughly county-sized)
TOP_AREAS_RES = 4
TOP_AREAS = 5

# Pseudo-value in the tallies for length with new imagery this build (counted on
# top of its real value, so it isn't part of the bands or the total)
UPDATED = "+new"

# Pseudo-value for private ways without imagery, tallied apart from "none" so
# the coverage gap counts only roads the Street View car could have used
PRIVATE = "private"

# hexagon -> value -> metres of road
HexStats = Dict[str, Dict[str, float]]


def piece_points(pieces: Iterable[Piece], segments: Iterable[Segment]
                 ) -> Iterator[Tuple[float, float, float, str]]:
    """(lat, lon, length in metres, value) at the middle of each piece.

    A private way without imagery has the value PRIVATE.
    """
    by_id = {s.id: s for s in segments}
    for p in pieces:
        seg = by_id[p.segment_id]
        lat, lon = point_at(seg.coords, seg.cum, (p.start_m + p.end_m) / 2)
        value = PRIVATE if seg.private and p.value == NO_COVERAGE else p.value
        yield lat, lon, p.end_m - p.start_m, value


def with_updates(points: Iterable[Tuple[float, float, float, str]],
                 updates: Iterable[Optional[int]]) -> Iterator[Tuple[float, float, float, str]]:
    """The points, plus an UPDATED copy of each one whose piece has new imagery."""
    for point, update in zip(points, updates):
        yield point
        if update is not None:
            yield (*point[:3], UPDATED)


def hex_stats(points: Iterable[Tuple[float, float, float, str]], res: int = STATS_RES) -> HexStats:
    stats: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for lat, lon, length, value in points:
        stats[h3.latlng_to_cell(lat, lon, res)][value] += length
    return {cell: dict(values) for cell, values in stats.items()}


def merge_stats(parts: Iterable[HexStats]) -> HexStats:
    merged: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for part in parts:
        for cell, values in part.items():
            for value, length in values.items():
                merged[cell][value] += length
    return {cell: dict(values) for cell, values in merged.items()}


def coarsen(stats: HexStats, res: int) -> HexStats:
    """Add each cell's tallies into its parent at a coarser resolution."""
    return merge_stats({h3.cell_to_parent(cell, res): values} for cell, values in stats.items())


def band_of(age_months: int) -> int:
    """Index into the age bands; an age equal to an edge falls in the older band, like the map."""
    return bisect_right(BAND_EDGES_MONTHS, age_months)


def summarise(values: Dict[str, float], built_index: int) -> Dict:
    """Kilometres per age band (as of the build month), with and without imagery, and the median month."""
    bands = [0.0] * (len(BAND_EDGES_MONTHS) + 1)
    none = other = private = 0.0
    dated: List[Tuple[int, float]] = []
    updated = 0.0
    for value, length in values.items():
        if value == UPDATED:
            updated += length
        elif value == NO_COVERAGE:
            none += length
        elif value == NON_GOOGLE:
            other += length
        elif value == PRIVATE:
            private += length
        else:
            idx = month_index(value)
            bands[band_of(max(0, built_index - idx))] += length
            dated.append((idx, length))
    # Kilometres to one decimal place: plenty for percentages, and the file
    # for the whole UK stays small
    props = {f"b{i}": round(b / 1000, 1) for i, b in enumerate(bands)}
    props.update(n=round(none / 1000, 1), o=round(other / 1000, 1), p=round(private / 1000, 1),
                 k=round((sum(bands) + none + other + private) / 1000, 1), m=weighted_median(dated),
                 u=round(updated / 1000, 1))
    return props


def weighted_median(items: List[Tuple[int, float]]) -> Optional[int]:
    """The value at half the total weight, or None if there's nothing."""
    total = sum(w for _, w in items)
    if total <= 0:
        return None
    running = 0.0
    for value, weight in sorted(items):
        running += weight
        if running >= total / 2:
            return value
    return None  # unreachable


def _feature(cell: str, res: int, props: Dict) -> Dict:
    # 3 decimal places is about 100 m, plenty for hexagons 6 km across or more
    ring = [[round(lng, 3), round(lat, 3)] for lat, lng in h3.cell_to_boundary(cell)]
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring + ring[:1]]},
            "properties": {"r": res, **props}}


def top_updated(stats: HexStats, res: int = TOP_AREAS_RES, n: int = TOP_AREAS,
                rings: bool = False) -> List[Dict]:
    """The `n` areas with the most new imagery: centre (x, y) and kilometres, most first.

    With `rings`, also each hexagon's id and outline, for the page to draw.
    """
    by_area: Dict[str, float] = defaultdict(float)
    for cell, values in stats.items():
        if values.get(UPDATED):
            by_area[h3.cell_to_parent(cell, res)] += values[UPDATED]
    top = []
    for cell, metres in sorted(by_area.items(), key=lambda kv: -kv[1])[:n]:
        lat, lng = h3.cell_to_latlng(cell)
        area = {"x": round(lng, 3), "y": round(lat, 3), "km": round(metres / 1000, 1)}
        if rings:
            area.update(id=cell, ring=_feature(cell, res, {})["geometry"]["coordinates"][0])
        top.append(area)
    return top


def build_overview(stats: HexStats, built: str) -> Dict:
    """GeoJSON FeatureCollection of both resolutions, with the build month ("YYYY-MM").

    Also carries the whole area's totals and the areas with the most new
    imagery, for the page's update digest and history. "u" is left out of the
    totals when the build had nothing to compare with.
    """
    built_index = month_index(built)
    features = [_feature(cell, STATS_RES, summarise(values, built_index))
                for cell, values in sorted(stats.items())]
    features += [_feature(cell, COARSE_RES, summarise(values, built_index))
                 for cell, values in sorted(coarsen(stats, COARSE_RES).items())]
    # Every hexagon's tallies added together
    everything = merge_stats({"all": values} for values in stats.values()).get("all", {})
    totals = summarise(everything, built_index)
    if not any(UPDATED in values for values in stats.values()):
        del totals["u"]
    return {"type": "FeatureCollection", "built": built, "totals": totals,
            "top": top_updated(stats), "features": features}


def save_json(data, path: str) -> None:
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))


def load_json(path: str):
    with open(path) as f:
        return json.load(f)
