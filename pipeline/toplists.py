"""Top-ten lists for the page, all ones that change from build to build.

- oldest:  roads with the oldest imagery still waiting for an update
- leaps:   roads whose imagery jumped furthest forward this build
- updated: roads with the most new imagery this build
- covered: roads with Street View for the first time this build
- areas:   areas with the most new imagery this build (from the hexagon tallies)

Roads only: no paths, service roads (driveways, car parks) or private ways, and
only stretches of MIN_LENGTH_M or more, so a stray fragment never tops a list.
A road is grouped by its ref (A64, B1234) across the whole area, or else by its
name within an area hexagon, or else is just its own OSM way. Each shard keeps
its best candidates; `merge` adds shards together (a road split between shards
comes out whole, give or take where it was cut off) and picks the top ten.
"""

import json
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

import h3

from pipeline.export import month_index
from pipeline.geo import point_at, sub_line
from pipeline.overview import COARSE_RES, HexStats, top_updated
from pipeline.roads import PATH_HIGHWAYS, Segment
from pipeline.sampler import Piece
from pipeline.streetview import NO_COVERAGE, NON_GOOGLE

TOP_N = 10
KEEP_PER_SHARD = 100
MIN_LENGTH_M = 200
# Unnamed roads, and names, are grouped within hexagons of this resolution (about 250 km²)
AREA_RES = COARSE_RES
MAX_LINE_POINTS = 60
ROAD_LISTS = ("oldest", "leaps", "updated", "covered")

Lists = Dict[str, List[Dict]]


def is_listed(seg: Segment) -> bool:
    """Roads only: paths are rarely re-driven, and driveways and private ways never."""
    return not (seg.is_path or seg.highway == "service" or seg.private)


def road_label(seg: Segment) -> str:
    if seg.name and seg.ref:
        return f"{seg.name} ({seg.ref})"
    return seg.name or seg.ref or f"Unnamed {seg.highway.replace('_', ' ')} road"


def road_id(seg: Segment, lat: float, lon: float) -> str:
    if seg.ref:
        return f"ref:{seg.ref}"
    if seg.name:
        return f"name:{seg.name}|{h3.latlng_to_cell(lat, lon, AREA_RES)}"
    return f"way:{seg.id}"


def _line(seg: Segment, piece: Piece) -> List[List[float]]:
    """The piece as [lng, lat] points, thinned to at most MAX_LINE_POINTS."""
    coords = sub_line(seg.coords, seg.cum, piece.start_m, piece.end_m)
    step = max(1, -(-len(coords) // MAX_LINE_POINTS))
    kept = coords[::step] + ([coords[-1]] if (len(coords) - 1) % step else [])
    return [[round(lon, 5), round(lat, 5)] for lat, lon in kept]


class _Group:
    """Metres of road for one list entry, and its longest piece to show."""

    def __init__(self, ident: str, label: str):
        self.id, self.name, self.metres, self.best = ident, label, 0.0, None

    def add(self, seg: Segment, piece: Piece, lat: float, lon: float, pano: Optional[str]):
        length = piece.end_m - piece.start_m
        self.metres += length
        if self.best is None or length > self.best[0]:
            self.best = (length, seg, piece, lat, lon, pano)

    def record(self, **extra) -> Dict:
        _, seg, piece, lat, lon, pano = self.best
        rec = {"id": self.id, "name": self.name, "km": round(self.metres / 1000, 2),
               "x": round(lon, 5), "y": round(lat, 5), "line": _line(seg, piece), **extra}
        if pano:
            rec["pano"] = pano
        return rec


def shard_lists(pieces: List[Piece], segments: Iterable[Segment],
                updates: Optional[List[Optional[int]]] = None,
                panos: Optional[List[Optional[str]]] = None) -> Lists:
    """Candidates for the road lists from one shard's result, best first."""
    by_id = {s.id: s for s in segments}
    oldest: Dict[Tuple[str, int], _Group] = {}
    leaps: Dict[Tuple[str, int, int], _Group] = {}
    updated: Dict[str, _Group] = {}
    covered: Dict[str, _Group] = {}

    def group(table, key, ident, seg):
        if key not in table:
            table[key] = _Group(ident, road_label(seg))
        return table[key]

    for i, p in enumerate(pieces):
        seg = by_id.get(p.segment_id)
        if seg is None or not is_listed(seg) or p.value in (NO_COVERAGE, NON_GOOGLE):
            continue
        lat, lon = point_at(seg.coords, seg.cum, (p.start_m + p.end_m) / 2)
        ident, month = road_id(seg, lat, lon), month_index(p.value)
        pano = panos[i] if panos else None
        group(oldest, (ident, month), ident, seg).add(seg, p, lat, lon, pano)
        update = updates[i] if updates else None
        if update is None:
            continue
        group(updated, ident, ident, seg).add(seg, p, lat, lon, pano)
        if update == 0:
            group(covered, ident, ident, seg).add(seg, p, lat, lon, pano)
        else:
            group(leaps, (ident, update, month), ident, seg).add(seg, p, lat, lon, pano)

    def long_enough(groups):
        return [g for g in groups if g.metres >= MIN_LENGTH_M]

    # Each road once: its oldest month with enough length, and its biggest leap
    oldest_recs = _best_per_road(
        [g.record(m=key[1]) for key, g in oldest.items() if g.metres >= MIN_LENGTH_M],
        key=lambda r: (r["m"], -r["km"]))
    leap_recs = _best_per_road(
        [g.record(fr=key[1], to=key[2]) for key, g in leaps.items() if g.metres >= MIN_LENGTH_M],
        key=lambda r: (r["fr"] - r["to"], -r["km"]))
    return {
        "oldest": oldest_recs[:KEEP_PER_SHARD],
        "leaps": leap_recs[:KEEP_PER_SHARD],
        "updated": _ranked([g.record() for g in long_enough(updated.values())]),
        "covered": _ranked([g.record() for g in long_enough(covered.values())]),
    }


def _best_per_road(records: List[Dict], key) -> List[Dict]:
    """Sorted by `key`, keeping each road's first (best) record only."""
    seen, out = set(), []
    for rec in sorted(records, key=key):
        if rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def _ranked(records: List[Dict], n: int = KEEP_PER_SHARD) -> List[Dict]:
    return sorted(records, key=lambda r: -r["km"])[:n]


def _add(a: Dict, b: Dict) -> Dict:
    """One road's record from two shards: lengths added, shown where it has more."""
    keep = a if a["km"] >= b["km"] else b
    return {**keep, "km": round(a["km"] + b["km"], 2)}


def merge(parts: Iterable[Lists], stats: Optional[HexStats] = None, n: int = TOP_N) -> Lists:
    """The top `n` of each list across shards, plus the busiest areas from `stats`."""
    combined: Dict[str, Dict[str, Dict]] = {name: {} for name in ROAD_LISTS}
    for part in parts:
        for name in ROAD_LISTS:
            table = combined[name]
            for rec in part.get(name, []):
                prev = table.get(rec["id"])
                if prev is None:
                    table[rec["id"]] = rec
                elif name == "oldest":
                    # The same month on both sides adds up; otherwise the older wins
                    table[rec["id"]] = (_add(prev, rec) if prev["m"] == rec["m"]
                                        else min(prev, rec, key=lambda r: r["m"]))
                elif name == "leaps":
                    same = (prev["fr"], prev["to"]) == (rec["fr"], rec["to"])
                    table[rec["id"]] = (_add(prev, rec) if same
                                        else max(prev, rec, key=lambda r: r["to"] - r["fr"]))
                else:
                    table[rec["id"]] = _add(prev, rec)
    out = {
        "oldest": sorted(combined["oldest"].values(), key=lambda r: (r["m"], -r["km"]))[:n],
        "leaps": sorted(combined["leaps"].values(), key=lambda r: (r["fr"] - r["to"], -r["km"]))[:n],
        "updated": _ranked(list(combined["updated"].values()), n),
        "covered": _ranked(list(combined["covered"].values()), n),
    }
    out["areas"] = top_updated(stats, res=AREA_RES, n=n, rings=True) if stats else []
    return out


def compact(lists: Lists) -> Dict[str, List[Dict]]:
    """Just enough of each entry to tell, next build, what joined or left a list."""
    keep = ("id", "name", "km", "m")
    return {name: [{k: r[k] for k in keep if k in r} for r in recs] for name, recs in lists.items()}


def save(lists: Lists, path: str) -> None:
    with open(path, "w") as f:
        json.dump(lists, f, separators=(",", ":"))


def load(path: str) -> Lists:
    with open(path) as f:
        return json.load(f)
