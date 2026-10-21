"""Monthly refresh: re-check a sparse set of sentinel points, re-sample where changed.

The cache keeps lookups forever, so rerunning `sample` never notices new
imagery. A refresh instead:

1. Divides the area into ~250 m grid cells and picks one sentinel per cell:
   the middle of the longest stretch of the most important road there,
   preferring roads that already have Google imagery (cars drive main roads).
2. Looks up every sentinel fresh, ignoring the cache.
3. Re-samples (fresh, bisection) every road touching a cell whose sentinel
   changed value, plus roads that are new or reshaped in the OSM extract.
4. Keeps the previous pieces for everything else.

Google captures whole areas in one drive, so one point per cell is usually
enough to notice a new pass. A new capture that misses the sentinel's road
is not detected until a later refresh or a full re-sample.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from pipeline.cache import Key
from pipeline.geo import point_at
from pipeline.roads import Segment
from pipeline.sampler import Piece, Resolver, key_at, merge_pieces, sample_bisect
from pipeline.streetview import NO_COVERAGE, NON_GOOGLE

# Higher = more likely to be driven by the Street View car
ROAD_RANK = {
    "motorway": 9, "trunk": 9, "primary": 8, "secondary": 7, "tertiary": 6,
    "motorway_link": 5, "trunk_link": 5, "primary_link": 5, "secondary_link": 5,
    "tertiary_link": 5, "unclassified": 4, "residential": 4, "living_street": 3,
    "busway": 3, "service": 2, "road": 2, "track": 1,
}

Cell = Tuple[int, int]

# A segment whose length changed by more than this was edited in OSM
RESHAPED_TOLERANCE_M = 1.0


@dataclass
class Sentinel:
    segment_id: str
    dist: float
    old_value: str
    key: Key = None


@dataclass
class RefreshResult:
    pieces: List[Piece]
    sentinels: int = 0
    changed_cells: Set[Cell] = field(default_factory=set)
    resampled: int = 0
    new_or_reshaped: int = 0


def cell_size_deg(cell_m: float, lat: float) -> Tuple[float, float]:
    """Return (lat_deg, lon_deg) for a square cell about `cell_m` wide at `lat`."""
    lat_deg = cell_m / 111_320
    return lat_deg, lat_deg / math.cos(math.radians(lat))


def cell_of(lat: float, lon: float, size: Tuple[float, float]) -> Cell:
    return (math.floor(lat / size[0]), math.floor(lon / size[1]))


def segment_cells(segment: Segment, size: Tuple[float, float],
                  step_m: float = 50.0) -> Set[Cell]:
    """Cells a segment passes through, checked every `step_m` metres."""
    n = max(1, math.ceil(segment.length / step_m))
    return {cell_of(*point_at(segment.coords, segment.cum, i * segment.length / n), size)
            for i in range(n + 1)}


def _sentinel_score(segment: Segment, piece: Piece) -> Tuple:
    has_imagery = piece.value not in (NO_COVERAGE, NON_GOOGLE)
    return (has_imagery, ROAD_RANK.get(segment.highway, 0), piece.end_m - piece.start_m)


def choose_sentinels(segments: Dict[str, Segment], pieces: List[Piece],
                     size: Tuple[float, float]) -> Dict[Cell, Sentinel]:
    """Pick one sentinel per cell from the previous run's pieces."""
    best: Dict[Cell, Tuple[Tuple, Sentinel]] = {}
    for piece in pieces:
        seg = segments.get(piece.segment_id)
        if seg is None:
            continue
        mid = (piece.start_m + piece.end_m) / 2
        cell = cell_of(*point_at(seg.coords, seg.cum, mid), size)
        score = _sentinel_score(seg, piece)
        if cell not in best or score > best[cell][0]:
            best[cell] = (score, Sentinel(seg.id, mid, piece.value, key_at(seg, mid)))
    return {cell: sentinel for cell, (_, sentinel) in best.items()}


async def refresh(segments: List[Segment], old_pieces: List[Piece],
                  resolve_fresh: Resolver, cell_m: float = 250.0,
                  max_span: float = 400.0, min_span: float = 30.0) -> RefreshResult:
    """Update the previous run's pieces using sentinel checks.

    Args:
        segments: Road segments from the current OSM extract
        old_pieces: Pieces saved by the previous sample or refresh run
        resolve_fresh: Resolver that ignores cache entries from before this
            refresh started
        cell_m: Sentinel grid cell size in metres
    """
    by_id = {s.id: s for s in segments}
    size = cell_size_deg(cell_m, segments[0].coords[0][0]) if segments else (1, 1)

    old_by_seg: Dict[str, List[Piece]] = defaultdict(list)
    for p in old_pieces:
        if p.segment_id in by_id:
            old_by_seg[p.segment_id].append(p)

    # Segments not covered by the old pieces, or whose geometry changed
    new_or_reshaped = {
        s.id for s in segments
        if s.id not in old_by_seg
        or abs(max(p.end_m for p in old_by_seg[s.id]) - s.length) > RESHAPED_TOLERANCE_M
    }

    kept_pieces = [p for seg_id, ps in old_by_seg.items()
                   if seg_id not in new_or_reshaped for p in ps]
    sentinels = choose_sentinels(by_id, kept_pieces, size)
    fresh = await resolve_fresh([s.key for s in sentinels.values()])
    changed = {cell for cell, s in sentinels.items() if fresh[s.key] != s.old_value}

    to_resample = set(new_or_reshaped)
    if changed:
        to_resample |= {s.id for s in segments if segment_cells(s, size) & changed}

    resampled = await sample_bisect([by_id[i] for i in sorted(to_resample)], resolve_fresh,
                                    max_span=max_span, min_span=min_span)
    unchanged = [p for p in kept_pieces if p.segment_id not in to_resample]
    pieces = merge_pieces(sorted(unchanged + resampled,
                                 key=lambda p: (p.segment_id, p.start_m)))

    return RefreshResult(pieces=pieces, sentinels=len(sentinels), changed_cells=changed,
                         resampled=len(to_resample), new_or_reshaped=len(new_or_reshaped))
