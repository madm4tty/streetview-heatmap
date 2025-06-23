import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv

import generate_heatmap
import database


def _coord_bounds(coords) -> Tuple[float, float, float, float]:
    if isinstance(coords[0], (float, int)):
        lon, lat = coords[:2]
        return lon, lat, lon, lat
    min_lon, min_lat, max_lon, max_lat = float('inf'), float('inf'), -float('inf'), -float('inf')
    for sub in coords:
        b = _coord_bounds(sub)
        min_lon = min(min_lon, b[0])
        min_lat = min(min_lat, b[1])
        max_lon = max(max_lon, b[2])
        max_lat = max(max_lat, b[3])
    return min_lon, min_lat, max_lon, max_lat


def load_bboxes(path: Path) -> List[Tuple[float, float, float, float]]:
    data = json.loads(path.read_text())
    boxes: List[Tuple[float, float, float, float]] = []
    if isinstance(data, list):
        for item in data:
            boxes.append(tuple(item))
    elif isinstance(data, dict) and 'features' in data:
        for feat in data['features']:
            geom = feat.get('geometry')
            if not geom:
                continue
            coords = geom.get('coordinates')
            if coords is None:
                continue
            boxes.append(_coord_bounds(coords))
    else:
        raise ValueError('Unsupported bbox file format')
    return boxes


def read_resume(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip())
    except ValueError:
        return 0


def write_resume(path: Path, idx: int) -> None:
    path.write_text(str(idx))


def process(boxes: List[Tuple[float, float, float, float]], args) -> None:
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        raise RuntimeError('GOOGLE_MAPS_API_KEY not set')
    start = read_resume(Path(args.resume)) if args.resume else 0
    total = len(boxes)
    while True:
        for i, bbox in enumerate(boxes[start:], start=start):
            out_html = Path(args.output_dir) / f'heatmap_{i}.html'
            out_csv = None
            if args.csv_dir:
                out_csv = Path(args.csv_dir) / f'heatmap_{i}.csv'
            logging.info('Processing bbox %s/%s: %s', i + 1, total, bbox)
            generate_heatmap.generate_for_bbox(
                bbox,
                args.step,
                str(out_html),
                str(out_csv) if out_csv else None,
                args.db,
                api_key,
            )
            if args.resume:
                write_resume(Path(args.resume), i + 1)
        if args.interval <= 0:
            break
        logging.info('Waiting %s seconds before next update', args.interval)
        start = 0
        time.sleep(args.interval)

    database.close_db()


def main():
    parser = argparse.ArgumentParser(description='Batch heatmap generator')
    parser.add_argument('--bbox-file', required=True, help='JSON or GeoJSON file with bounding boxes')
    parser.add_argument('--step', type=float, default=0.005, help='Grid step size')
    parser.add_argument('--output-dir', default='output', help='Directory for HTML output')
    parser.add_argument('--csv-dir', default=None, help='Optional directory for CSV output')
    parser.add_argument('--db', default='metadata.db', help='Metadata cache database')
    parser.add_argument('--interval', type=int, default=0, help='Seconds between updates, 0 to run once')
    parser.add_argument('--resume', default=None, help='Resume progress file path')
    parser.add_argument('--log-level', default='INFO')
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()))
    load_dotenv()

    api_key_env = os.getenv("GMAPS_APIKEY")
    if api_key_env:
        print("API key loaded")
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", api_key_env)
    else:
        print("API key not found")

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    if args.csv_dir:
        Path(args.csv_dir).mkdir(parents=True, exist_ok=True)

    boxes = load_bboxes(Path(args.bbox_file))
    process(boxes, args)


if __name__ == '__main__':
    main()
