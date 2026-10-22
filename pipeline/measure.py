"""Measure a large extract before sampling it: road length, and lookup and tile-size estimates.

Makes no API calls. Ways are tallied by the 1-degree grid cell holding their
middle vertex, so every way counts exactly once and the table shows where the
road is (and whether an extract covers, say, Northern Ireland).

Lookup estimates come from bisection's minimum (every segment uniform) scaled by
the Leeds pilot, where date changes added lookups on top of that minimum.
"""

import json
import math
from collections import Counter, defaultdict
from typing import Dict, Iterable, Tuple

from pipeline.roads import Segment, touches_bbox
from pipeline.sampler import min_bisect_lookups

# Leeds pilot, full bisect run (see README): actual lookups and tiles built
LEEDS_LOOKUPS = 115_000
LEEDS_TILES_MB_PER_KM = 7.9 / 5302

R2_FREE_GB = 10.0
RATE_PER_SECOND = 400


def cell_of(lat: float, lon: float) -> str:
    """Name of the 1-degree cell containing a point, e.g. '53N 2W'."""
    la, lo = math.floor(lat), math.floor(lon)
    return f"{abs(la)}{'N' if la >= 0 else 'S'} {abs(lo)}{'E' if lo >= 0 else 'W'}"


def segment_cell(seg: Segment) -> str:
    """The cell a way belongs to: the one holding its middle vertex."""
    lat, lon = seg.coords[len(seg.coords) // 2]
    return cell_of(lat, lon)


def _empty() -> Dict:
    return {"ways": 0, "km": 0.0, "path_km": 0.0, "min_lookups": 0}


def _add(t: Dict, seg: Segment) -> None:
    t["ways"] += 1
    t["km"] += seg.length / 1000
    if seg.is_path:
        t["path_km"] += seg.length / 1000
    t["min_lookups"] += min_bisect_lookups(seg.length)


def tally(segments: Iterable[Segment],
          calibration_bbox: Tuple[float, float, float, float]) -> Dict:
    """Totals, per-cell and per-highway tallies, plus the calibration area's tally."""
    total, calib = _empty(), _empty()
    cells: Dict[str, Dict] = defaultdict(_empty)
    km_by_type: Counter = Counter()
    for seg in segments:
        _add(total, seg)
        _add(cells[segment_cell(seg)], seg)
        km_by_type[seg.highway] += seg.length / 1000
        if touches_bbox(seg, calibration_bbox):
            _add(calib, seg)
    return {"total": total, "calibration": calib, "cells": dict(cells),
            "km_by_type": dict(km_by_type)}


def estimates(result: Dict) -> Dict:
    """Lookup, time and storage estimates from a tally."""
    total, calib = result["total"], result["calibration"]
    # How many lookups Leeds really needed per lookup in the uniform minimum.
    # Leeds is urban with many date changes, so this is a pessimistic factor.
    factor = LEEDS_LOOKUPS / calib["min_lookups"] if calib["min_lookups"] else float("nan")
    low, high = total["min_lookups"], total["min_lookups"] * factor
    tiles_gb = total["km"] * LEEDS_TILES_MB_PER_KM / 1000
    return {
        "leeds_factor": round(factor, 2),
        "lookups_low": int(low),
        "lookups_high": int(high),
        "hours_low": round(low / RATE_PER_SECOND / 3600, 1),
        "hours_high": round(high / RATE_PER_SECOND / 3600, 1),
        "tiles_gb_z15": round(tiles_gb, 2),
    }


def report_markdown(result: Dict, est: Dict) -> str:
    t = result["total"]
    lines = [
        "## UK extract measurement",
        "",
        f"- **{t['ways']:,} ways, {t['km']:,.0f} km** ({t['path_km']:,.0f} km path-like)",
        f"- Bisection minimum: {est['lookups_low']:,} lookups. Leeds needed "
        f"{est['leeds_factor']}x its minimum, giving up to {est['lookups_high']:,}",
        f"- At {RATE_PER_SECOND}/s: {est['hours_low']}–{est['hours_high']} hours of lookups for a first full run",
        f"- Tiles at Leeds density (max zoom 15): about {est['tiles_gb_z15']} GB per version; "
        f"R2 free tier is {R2_FREE_GB:.0f} GB including two versions and state",
        "",
        "### By 1° cell (cell named by its south-west corner)",
        "",
        "| Cell | Ways | km | Path km | Min lookups |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, c in sorted(result["cells"].items(), key=lambda kv: -kv[1]["km"]):
        lines.append(f"| {name} | {c['ways']:,} | {c['km']:,.0f} | {c['path_km']:,.0f} | "
                     f"{c['min_lookups']:,} |")
    lines += ["", "### Top highway types", "", "| Type | km |", "|---|---:|"]
    for hw, km in sorted(result["km_by_type"].items(), key=lambda kv: -kv[1])[:15]:
        lines.append(f"| {hw} | {km:,.0f} |")
    return "\n".join(lines) + "\n"


def write_json(result: Dict, est: Dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump({**result, "estimates": est}, f, indent=1)
