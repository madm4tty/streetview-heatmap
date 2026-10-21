"""Turn sampled pieces into map-ready files and accuracy reports."""

import json
import logging
import shutil
import subprocess
from collections import defaultdict
from dataclasses import asdict
from typing import Dict, List

from pipeline.geo import sub_line
from pipeline.roads import Segment
from pipeline.sampler import Piece
from pipeline.streetview import NO_COVERAGE, NON_GOOGLE

logger = logging.getLogger(__name__)


def month_index(ym: str) -> int:
    """'2023-05' -> months since year 0, so the map style can do age maths."""
    year, month = ym.split("-")
    return int(year) * 12 + int(month) - 1


def piece_properties(segment: Segment, value: str) -> Dict:
    props = {"hw": segment.highway}
    if segment.name:
        props["name"] = segment.name
    if value in (NO_COVERAGE, NON_GOOGLE):
        props["cov"] = value
    else:
        props["cov"] = "google"
        props["ym"] = value
        props["m"] = month_index(value)
    return props


def write_geojsonseq(pieces: List[Piece], segments: List[Segment], path: str) -> int:
    """Write one GeoJSON Feature per line (the format tippecanoe reads fastest)."""
    by_id = {s.id: s for s in segments}
    count = 0
    with open(path, "w") as f:
        for p in pieces:
            seg = by_id[p.segment_id]
            coords = sub_line(seg.coords, seg.cum, p.start_m, p.end_m)
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[round(lon, 6), round(lat, 6)] for lat, lon in coords],
                },
                "properties": piece_properties(seg, p.value),
            }
            f.write(json.dumps(feature, separators=(",", ":")) + "\n")
            count += 1
    return count


def build_pmtiles(geojsonseq_path: str, pmtiles_path: str) -> None:
    """Run tippecanoe to build a PMTiles archive from GeoJSONSeq."""
    if not shutil.which("tippecanoe"):
        raise SystemExit("tippecanoe not found. Install it with: brew install tippecanoe")
    cmd = [
        "tippecanoe", "-o", pmtiles_path, "--force",
        "-l", "roads",
        "-Z", "8", "-z", "15",
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        geojsonseq_path,
    ]
    logger.info("Running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def save_pieces(pieces: List[Piece], path: str) -> None:
    with open(path, "w") as f:
        for p in pieces:
            f.write(json.dumps(asdict(p)) + "\n")


def load_pieces(path: str) -> List[Piece]:
    with open(path) as f:
        return [Piece(**json.loads(line)) for line in f]


def agreement_by_length(reference: List[Piece], candidate: List[Piece]) -> Dict:
    """Share of road length where `candidate` gives the same value as `reference`.

    Only segments present in both lists are compared.
    """
    ref_by_seg, cand_by_seg = defaultdict(list), defaultdict(list)
    for p in reference:
        ref_by_seg[p.segment_id].append(p)
    for p in candidate:
        cand_by_seg[p.segment_id].append(p)

    total = agree = 0.0
    disagreements = defaultdict(float)
    for seg_id in ref_by_seg.keys() & cand_by_seg.keys():
        for r in ref_by_seg[seg_id]:
            for c in cand_by_seg[seg_id]:
                overlap = min(r.end_m, c.end_m) - max(r.start_m, c.start_m)
                if overlap <= 0:
                    continue
                total += overlap
                if r.value == c.value:
                    agree += overlap
                else:
                    disagreements[_kind(r.value, c.value)] += overlap

    return {
        "compared_km": round(total / 1000, 2),
        "agreement_pct": round(100 * agree / total, 2) if total else None,
        "disagreement_km": {k: round(v / 1000, 3) for k, v in disagreements.items()},
    }


def _kind(ref: str, cand: str) -> str:
    special = (NO_COVERAGE, NON_GOOGLE)
    if ref in special or cand in special:
        return f"{ref if ref in special else 'date'} vs {cand if cand in special else 'date'}"
    return "different month"
