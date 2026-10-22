"""Decide where along each road segment to look up Street View metadata.

Two strategies, both returning "pieces": stretches of a segment that share
one value (a "YYYY-MM" capture month, "none" or "other").

- dense: one lookup every `spacing` metres. Simple, used as ground truth.
- bisect: look up both ends of a stretch; if they agree, assume the whole
  stretch matches; otherwise look up the midpoint and repeat on each half.
  Imagery is captured in long continuous drives, so most stretches resolve
  after very few lookups, and the splits land on the date change points.
"""

import math
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Tuple

from pipeline.cache import Key, make_key
from pipeline.geo import point_at
from pipeline.roads import Segment

# Look up positions this far in from each end of a segment, so samples
# near a junction don't snap to imagery on the crossing road.
END_INSET_M = 10.0

ROAD_RADIUS_M = 25
PATH_RADIUS_M = 10

# A function that takes cache keys and returns the classified value of each
Resolver = Callable[[List[Key]], Awaitable[Dict[Key, str]]]


@dataclass
class Piece:
    """A stretch of a segment, from start_m to end_m metres, with one value."""
    segment_id: str
    start_m: float
    end_m: float
    value: str


def radius_for(segment: Segment) -> int:
    return PATH_RADIUS_M if segment.tight_radius else ROAD_RADIUS_M


def key_at(segment: Segment, dist: float) -> Key:
    lat, lon = point_at(segment.coords, segment.cum, dist)
    return make_key(lat, lon, radius_for(segment))


def merge_pieces(pieces: List[Piece]) -> List[Piece]:
    """Join neighbouring pieces of the same segment that share a value."""
    merged: List[Piece] = []
    for p in pieces:
        last = merged[-1] if merged else None
        if (last and last.segment_id == p.segment_id and last.value == p.value
                and abs(last.end_m - p.start_m) < 1e-6):
            last.end_m = p.end_m
        else:
            merged.append(Piece(p.segment_id, p.start_m, p.end_m, p.value))
    return merged


async def sample_dense(segments: List[Segment], resolve: Resolver,
                       spacing: float = 20.0) -> List[Piece]:
    """Look up the centre of every `spacing`-metre stretch of every segment."""
    plan: List[Tuple[Segment, float, float, Key]] = []
    for seg in segments:
        n = max(1, math.ceil(seg.length / spacing))
        step = seg.length / n
        for i in range(n):
            plan.append((seg, i * step, (i + 1) * step, key_at(seg, (i + 0.5) * step)))

    values = await resolve([k for *_, k in plan])
    return merge_pieces([Piece(seg.id, a, b, values[k]) for seg, a, b, k in plan])


def min_bisect_lookups(length: float, max_span: float = 400.0, min_span: float = 30.0) -> int:
    """Lookups sample_bisect makes on a segment whose imagery never changes.

    Each change of value along the segment adds a few more on top.
    """
    if length <= 2 * END_INSET_M + min_span:
        return 1
    return max(1, math.ceil((length - 2 * END_INSET_M) / max_span)) + 1


async def sample_bisect(segments: List[Segment], resolve: Resolver,
                        max_span: float = 400.0, min_span: float = 30.0) -> List[Piece]:
    """Adaptive sampling: split stretches only where their ends disagree.

    Args:
        segments: Road segments to sample
        resolve: Looks up and classifies a batch of keys
        max_span: Longest stretch assumed uniform without checking inside it.
            Shorter catches more short patches of different imagery.
        min_span: Stop splitting below this; the change point is placed
            at the midpoint of the final stretch.
    """
    # Each interval is (segment, a, b) with lookups at positions a and b.
    intervals: List[Tuple[Segment, float, float]] = []
    pieces: List[Piece] = []
    values: Dict[Key, str] = {}

    for seg in segments:
        if seg.length <= 2 * END_INSET_M + min_span:
            # Too short to bisect: one lookup at the middle covers it
            intervals.append((seg, seg.length / 2, seg.length / 2))
            continue
        a0, b0 = END_INSET_M, seg.length - END_INSET_M
        n = max(1, math.ceil((b0 - a0) / max_span))
        step = (b0 - a0) / n
        intervals.extend((seg, a0 + i * step, a0 + (i + 1) * step) for i in range(n))

    def needed(ivs) -> List[Key]:
        return [k for seg, a, b in ivs for k in (key_at(seg, a), key_at(seg, b))
                if k not in values]

    while intervals:
        values.update(await resolve(needed(intervals)))
        unresolved = []
        for seg, a, b in intervals:
            va, vb = values[key_at(seg, a)], values[key_at(seg, b)]
            if va == vb:
                pieces.append(Piece(seg.id, a, b, va))
            elif b - a <= min_span:
                mid = (a + b) / 2
                pieces.append(Piece(seg.id, a, mid, va))
                pieces.append(Piece(seg.id, mid, b, vb))
            else:
                mid = (a + b) / 2
                unresolved += [(seg, a, mid), (seg, mid, b)]
        intervals = unresolved

    return merge_pieces(_extend_to_ends(pieces, segments))


def _extend_to_ends(pieces: List[Piece], segments: List[Segment]) -> List[Piece]:
    """Stretch each segment's first and last piece to cover the inset ends."""
    lengths = {s.id: s.length for s in segments}
    pieces.sort(key=lambda p: (p.segment_id, p.start_m))
    for i, p in enumerate(pieces):
        if i == 0 or pieces[i - 1].segment_id != p.segment_id:
            p.start_m = 0.0
        if i == len(pieces) - 1 or pieces[i + 1].segment_id != p.segment_id:
            p.end_m = lengths[p.segment_id]
    return pieces
