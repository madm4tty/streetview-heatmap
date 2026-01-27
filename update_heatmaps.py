import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv

import database
import generate_heatmap

logger = logging.getLogger(__name__)


def _coord_bounds(coords) -> Tuple[float, float, float, float]:
    """Recursively compute bounding box from GeoJSON coordinates."""
    if isinstance(coords[0], (float, int)):
        lon, lat = coords[:2]
        return lon, lat, lon, lat
    min_lon, min_lat, max_lon, max_lat = (
        float("inf"),
        float("inf"),
        -float("inf"),
        -float("inf"),
    )
    for sub in coords:
        b = _coord_bounds(sub)
        min_lon = min(min_lon, b[0])
        min_lat = min(min_lat, b[1])
        max_lon = max(max_lon, b[2])
        max_lat = max(max_lat, b[3])
    return min_lon, min_lat, max_lon, max_lat


def load_bboxes(path: Path) -> List[Tuple[float, float, float, float]]:
    """Load bounding boxes from JSON or GeoJSON file.

    Supports:
    - JSON array of [min_lon, min_lat, max_lon, max_lat] arrays
    - GeoJSON FeatureCollection (computes bounds from geometry)
    """
    data = json.loads(path.read_text())
    boxes: List[Tuple[float, float, float, float]] = []
    if isinstance(data, list):
        for item in data:
            boxes.append(tuple(item))
    elif isinstance(data, dict) and "features" in data:
        for feat in data["features"]:
            geom = feat.get("geometry")
            if not geom:
                continue
            coords = geom.get("coordinates")
            if coords is None:
                continue
            boxes.append(_coord_bounds(coords))
    else:
        raise ValueError("Unsupported bbox file format")
    return boxes


def read_resume(path: Path) -> int:
    """Read the last processed index from resume file."""
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip())
    except ValueError:
        return 0


def write_resume(path: Path, idx: int) -> None:
    """Write the current index to resume file."""
    path.write_text(str(idx))


def process(boxes: List[Tuple[float, float, float, float]], args) -> None:
    """Process all bounding boxes, generating heatmaps for each."""
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not set")

    start = read_resume(Path(args.resume)) if args.resume else 0
    total = len(boxes)

    while True:
        for i, bbox in enumerate(boxes[start:], start=start):
            out_html = Path(args.output_dir) / f"heatmap_{i}.html"
            out_csv = None
            if args.csv_dir:
                out_csv = Path(args.csv_dir) / f"heatmap_{i}.csv"

            logger.info("Processing bbox %s/%s: %s", i + 1, total, bbox)

            try:
                generate_heatmap.generate_for_bbox(
                    bbox,
                    args.step,
                    str(out_html),
                    str(out_csv) if out_csv else None,
                    args.db,
                    api_key,
                    samples=args.samples,
                    concurrency=args.concurrency,
                    adaptive_sampling=not args.no_adaptive,
                )
            except Exception as exc:
                logger.error("Error processing bbox %s: %s", bbox, exc)
                # Continue with next bbox instead of failing completely
                continue

            if args.resume:
                write_resume(Path(args.resume), i + 1)

            # Log cache stats periodically
            stats = database.get_cache_stats()
            logger.info(
                "Progress: %d/%d bboxes, Cache: %d entries",
                i + 1,
                total,
                stats["total_entries"],
            )

        if args.interval <= 0:
            break

        logger.info("Waiting %s seconds before next update cycle", args.interval)
        start = 0
        time.sleep(args.interval)

    database.close_db()


def main():
    parser = argparse.ArgumentParser(description="Batch heatmap generator")
    parser.add_argument(
        "--bbox-file",
        required=True,
        help="JSON or GeoJSON file with bounding boxes",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.005,
        help="Grid step size (legacy parameter)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for HTML output",
    )
    parser.add_argument(
        "--csv-dir",
        default=None,
        help="Optional directory for CSV output",
    )
    parser.add_argument(
        "--db",
        default="metadata.db",
        help="Metadata cache database",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Base sample points per road",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=generate_heatmap.DEFAULT_CONCURRENCY,
        help=f"Concurrent Street View requests (default: {generate_heatmap.DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--no-adaptive",
        action="store_true",
        help="Disable adaptive sampling based on road importance",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Seconds between update cycles, 0 to run once",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="Resume progress file path",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    load_dotenv()

    api_key_env = os.getenv("GMAPS_APIKEY")
    if api_key_env:
        logger.info("API key loaded from GMAPS_APIKEY")
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", api_key_env)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    if args.csv_dir:
        Path(args.csv_dir).mkdir(parents=True, exist_ok=True)

    boxes = load_bboxes(Path(args.bbox_file))
    logger.info("Loaded %d bounding boxes from %s", len(boxes), args.bbox_file)
    process(boxes, args)


if __name__ == "__main__":
    main()
