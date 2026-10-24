"""Command-line entry point for the batch pipeline.

Usage:
    python3 -m pipeline.run download --area leeds
    python3 -m pipeline.run estimate --area leeds
    python3 -m pipeline.run sample   --area leeds --mode bisect --max-calls 100000
    python3 -m pipeline.run compare  --area leeds-centre
    python3 -m pipeline.run tiles    --area leeds --mode bisect
    python3 -m pipeline.run refresh  --area leeds          # monthly update
    python3 -m pipeline.run publish  --area leeds          # upload the site to R2
    python3 -m pipeline.run publish-page --area uk         # upload only the page, for page-only changes
    python3 -m pipeline.run pull-state --area leeds        # restore cache from R2
    python3 -m pipeline.run push-state --area leeds        # save cache to R2
    python3 -m pipeline.run measure  --pbf uk.osm.pbf      # size up a big extract, no API calls
"""

import argparse
import asyncio
import logging
import os
import shutil
import ssl
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from pipeline import areas, measure, overview, publish
from pipeline.cache import Key, MetadataCache
from pipeline.export import (agreement_by_length, build_pmtiles, load_pieces,
                             save_pieces, write_geojsonseq)
from pipeline.refresh import refresh
from pipeline.roads import iter_segments, load_segments
from pipeline.sampler import sample_bisect, sample_dense
from pipeline.streetview import (BudgetExceeded, FatalAPIError, StreetViewClient, ca_bundle,
                                 classify)

logger = logging.getLogger("pipeline")

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PIPELINE_DIR, "data")
SITE_DIR = os.path.join(DATA_DIR, "site")


def pbf_path(area: str) -> str:
    return os.path.join(DATA_DIR, f"{areas.extract_name(area)}-latest.osm.pbf")


def cache_path(area: str) -> str:
    """One lookup cache per area, so shards can be saved and restored independently."""
    return os.path.join(DATA_DIR, f"{area}.cache.sqlite")


def pieces_path(area: str, mode: str) -> str:
    return os.path.join(DATA_DIR, f"{area}-{mode}.pieces.jsonl")


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(PIPELINE_DIR), ".env"))
    except ImportError:
        pass


def get_api_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GMAPS_APIKEY")
    if not key:
        raise SystemExit("Set GOOGLE_MAPS_API_KEY (or GMAPS_APIKEY) in the environment or .env")
    return key


def cmd_download(args) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    url, dest = areas.extract_url(args.area), pbf_path(args.area)
    logger.info("Downloading %s -> %s", url, dest)
    context = ssl.create_default_context(cafile=ca_bundle())
    with urllib.request.urlopen(url, context=context) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)
    logger.info("Done (%.1f MB)", os.path.getsize(dest) / 1e6)


def load_area_segments(area: str):
    path = pbf_path(area)
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found. Run: python3 -m pipeline.run download --area {area}")
    spec = areas.get_area(area)
    if "shards" in spec:
        raise SystemExit(f"'{area}' is processed shard by shard: use --area {spec['shards'][0]} etc.")
    if "cells" in spec:
        segments = [s for s in iter_segments(path) if measure.segment_cell(s) in spec["cells"]]
        logger.info("Loaded %d segments (%.0f km)",
                    len(segments), sum(s.length for s in segments) / 1000)
        return segments
    return load_segments(path, spec["bbox"])


def cmd_estimate(args) -> None:
    segments = load_area_segments(args.area)
    km_by_type: Counter = Counter()
    for s in segments:
        km_by_type[s.highway] += s.length / 1000
    total_km = sum(km_by_type.values())
    print(f"\n{args.area}: {len(segments):,} segments, {total_km:,.0f} km\n")
    for hw, km in km_by_type.most_common(15):
        print(f"  {hw:<16} {km:8.0f} km")
    print("\nDense sampling would need roughly:")
    for spacing in (20, 50, 100):
        print(f"  every {spacing:>3} m: {total_km * 1000 / spacing:>10,.0f} calls")


def make_client(args) -> StreetViewClient:
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    os.makedirs(DATA_DIR, exist_ok=True)
    return StreetViewClient(get_api_key(), MetadataCache(cache_path(args.area)),
                            concurrency=args.concurrency, max_calls=args.max_calls,
                            rate_per_second=args.rate, deadline=deadline)


def coverage_summary(pieces) -> str:
    values: Counter = Counter()
    for p in pieces:
        values["google" if p.value[:1].isdigit() else p.value] += (p.end_m - p.start_m) / 1000
    return ", ".join(f"{k} {v:,.0f} km" for k, v in values.most_common())


def fresh_cutoff(max_age_days: Optional[float], now: Optional[datetime] = None) -> Optional[str]:
    """Cache timestamp before which lookups count as stale, or None to trust them all."""
    if max_age_days is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")


def cmd_sample(args) -> None:
    segments = load_area_segments(args.area)
    client = make_client(args)
    cache = client.cache
    # By default every cached lookup is reused, however old. A full rebuild sets
    # a maximum age so old answers are asked again, while lookups from an
    # interrupted attempt this month still count.
    cutoff = fresh_cutoff(args.max_age_days)

    async def resolve(keys: List[Key]) -> Dict[Key, str]:
        lookups = await client.lookup_many(keys, fresh_after=cutoff)
        return {k: classify(v) for k, v in lookups.items()}

    start = time.time()
    try:
        if args.mode == "dense":
            pieces = asyncio.run(sample_dense(segments, resolve, spacing=args.spacing))
        else:
            pieces = asyncio.run(sample_bisect(segments, resolve, max_span=args.max_span,
                                               min_span=args.min_span))
    except (FatalAPIError, BudgetExceeded) as exc:
        raise SystemExit(f"Stopped after {client.api_calls:,} API calls: {exc}\n"
                         "Lookups made so far are cached; rerun to resume.")
    finally:
        cache.close()
    elapsed = time.time() - start

    save_pieces(pieces, pieces_path(args.area, args.mode))
    km = sum(s.length for s in segments) / 1000
    lookups = client.api_calls + client.cache_hits
    print(f"\n{args.area} ({args.mode}): {km:,.0f} km of road in {elapsed:,.0f}s")
    print(f"  lookups: {lookups:,} ({lookups / max(km, 0.001):.1f} per km); "
          f"API calls: {client.api_calls:,}, cache hits: {client.cache_hits:,}")
    print(f"  coverage by length: {coverage_summary(pieces)}")
    print(f"  saved {len(pieces):,} pieces to {pieces_path(args.area, args.mode)}")


def cmd_refresh(args) -> None:
    path = pieces_path(args.area, "bisect")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found. Run a full `sample --mode bisect` first.")
    segments = load_area_segments(args.area)
    old_pieces = load_pieces(path)
    client = make_client(args)
    # Cache entries newer than this count as fresh, so a rerun in the same
    # month resumes instead of starting again.
    cutoff = fresh_cutoff(args.max_age_days)

    async def resolve_fresh(keys: List[Key]) -> Dict[Key, str]:
        lookups = await client.lookup_many(keys, fresh_after=cutoff)
        return {k: classify(v) for k, v in lookups.items()}

    start = time.time()
    try:
        result = asyncio.run(refresh(segments, old_pieces, resolve_fresh, cell_m=args.cell_m))
    except (FatalAPIError, BudgetExceeded) as exc:
        raise SystemExit(f"Stopped after {client.api_calls:,} API calls: {exc}\n"
                         "Lookups made so far are cached; rerun to resume.")
    finally:
        client.cache.close()

    shutil.copy(path, path.replace(".pieces.jsonl", ".prev.pieces.jsonl"))
    save_pieces(result.pieces, path)
    print(f"\n{args.area} refresh in {time.time() - start:,.0f}s")
    print(f"  sentinels checked: {result.sentinels:,} ({args.cell_m:.0f} m cells); "
          f"changed: {len(result.changed_cells):,}")
    print(f"  segments re-sampled: {result.resampled:,} "
          f"(of which new or reshaped in OSM: {result.new_or_reshaped:,})")
    print(f"  API calls: {client.api_calls:,}, cache hits: {client.cache_hits:,}")
    print(f"  coverage by length: {coverage_summary(result.pieces)}")
    print(f"  saved {len(result.pieces):,} pieces (was {len(old_pieces):,}); "
          f"previous version kept as {os.path.basename(path).replace('.pieces', '.prev.pieces')}")
    print(f"Next: python3 -m pipeline.run tiles --area {args.area}")


def cmd_compare(args) -> None:
    paths = [pieces_path(args.area, m) for m in ("dense", "bisect")]
    for p in paths:
        if not os.path.exists(p):
            raise SystemExit(f"{p} not found. Run `sample` for both modes on {args.area} first.")
    result = agreement_by_length(load_pieces(paths[0]), load_pieces(paths[1]))
    print(f"\n{args.area}: bisection vs dense ground truth")
    print(f"  compared: {result['compared_km']} km")
    print(f"  agreement by length: {result['agreement_pct']}%")
    for kind, km in sorted(result["disagreement_km"].items(), key=lambda x: -x[1]):
        print(f"  disagree ({kind}): {km} km")


def hexstats_path(area: str) -> str:
    return os.path.join(DATA_DIR, f"{area}.hexstats.json")


def overview_path(area: str) -> str:
    return os.path.join(SITE_DIR, f"{area}.overview.json")


def write_overview(area: str, parts: List[str]) -> None:
    built = datetime.now(timezone.utc).strftime("%Y-%m")
    stats = overview.merge_stats(overview.load_json(p) for p in parts)
    os.makedirs(SITE_DIR, exist_ok=True)
    overview.save_json(overview.build_overview(stats, built), overview_path(area))
    logger.info("Wrote %s (%d hexagons)", overview_path(area), len(stats))


def cmd_overview(args) -> None:
    spec = areas.get_area(args.area)
    parts = [hexstats_path(a) for a in spec.get("shards", [args.area])]
    missing = [p for p in parts if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)}. Run `tiles` for those areas first.")
    write_overview(args.area, parts)


def cmd_tiles(args) -> None:
    segments = load_area_segments(args.area)
    pieces = load_pieces(pieces_path(args.area, args.mode))
    # Hexagon tallies: merged across shards later for the UK, or straight into
    # this area's overview otherwise
    overview.save_json(overview.hex_stats(overview.piece_points(pieces, segments)),
                       hexstats_path(args.area))
    if "cells" not in areas.get_area(args.area):
        write_overview(args.area, [hexstats_path(args.area)])
    os.makedirs(SITE_DIR, exist_ok=True)
    geojsonseq = os.path.join(DATA_DIR, f"{args.area}.geojsonl")
    n = write_geojsonseq(pieces, segments, geojsonseq)
    logger.info("Wrote %d features to %s", n, geojsonseq)
    pmtiles = os.path.join(SITE_DIR, f"{args.area}.pmtiles")
    build_pmtiles(geojsonseq, pmtiles)
    shutil.copy(os.path.join(PIPELINE_DIR, "web", "index.html"), SITE_DIR)
    print(f"\nSite ready in {SITE_DIR} ({os.path.getsize(pmtiles) / 1e6:.1f} MB of tiles)")
    print("Preview with: python3 -m pipeline.serve")


def state_paths(area: str) -> List[str]:
    return [cache_path(area), pieces_path(area, "bisect")]


def cmd_measure(args) -> None:
    result = measure.tally(iter_segments(args.pbf), areas.get_area("leeds")["bbox"])
    est = measure.estimates(result)
    measure.write_json(result, est, args.out)
    report = measure.report_markdown(result, est)
    print(report)
    # In GitHub Actions, also show the report on the run's summary page
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(report)


def cmd_publish(args) -> None:
    bucket, prefix = publish.site_bucket(), publish.site_prefix()
    page = publish.publish_site(publish.r2_client(bucket), bucket, prefix, args.area, SITE_DIR,
                                also_count=(publish.state_bucket(),))
    print(f"\nPublished {publish.redact(page, prefix)} to the {bucket} bucket")


def cmd_publish_page(args) -> None:
    bucket, prefix = publish.site_bucket(), publish.site_prefix()
    index = os.path.join(PIPELINE_DIR, "web", "index.html")
    _, tiles, overview = publish.publish_page(publish.r2_client(bucket), bucket, prefix, args.area, index)
    print(f"\nPublished the page, using {tiles}" + (f" and {overview}" if overview else " (no overview)"))


def cmd_pull_state(args) -> None:
    bucket = publish.state_bucket()
    written = publish.pull_state(publish.r2_client(bucket), bucket, state_paths(args.area),
                                 force=args.force)
    print(f"\nRestored {len(written)} file(s) from the {bucket} bucket")


def cmd_push_state(args) -> None:
    bucket = publish.state_bucket()
    uploaded = publish.push_state(publish.r2_client(bucket), bucket, state_paths(args.area))
    print(f"\nSaved {len(uploaded)} file(s) to the {bucket} bucket")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m pipeline.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("download", "estimate", "sample", "compare", "tiles", "refresh",
                 "publish", "publish-page", "pull-state", "push-state", "overview"):
        p = sub.add_parser(name)
        p.add_argument("--area", required=True, choices=sorted(areas.AREAS))
        if name in ("sample", "tiles"):
            p.add_argument("--mode", choices=("dense", "bisect"), default="bisect")
        if name == "sample":
            p.add_argument("--spacing", type=float, default=20.0,
                           help="Dense mode: metres between lookups (default 20)")
            p.add_argument("--max-span", type=float, default=400.0,
                           help="Bisect mode: longest stretch assumed uniform (default 400)")
            p.add_argument("--max-age-days", type=float, default=None,
                           help="Re-query cached lookups older than this (default: reuse all)")
            p.add_argument("--min-span", type=float, default=30.0,
                           help="Bisect mode: stop splitting below this (default 30)")
        if name == "refresh":
            p.add_argument("--cell-m", type=float, default=250.0,
                           help="Sentinel grid cell size in metres (default 250)")
            p.add_argument("--max-age-days", type=float, default=25.0,
                           help="Re-query lookups older than this (default 25)")
        if name in ("sample", "refresh"):
            p.add_argument("--concurrency", type=int, default=50)
            p.add_argument("--rate", type=float, default=300.0,
                           help="Max API requests per second (Google allows 500; default 300)")
            p.add_argument("--max-calls", type=int, default=None,
                           help="Abort before exceeding this many API calls")
            p.add_argument("--max-minutes", type=float, default=None,
                           help="Stop starting new lookups after this long; rerun to resume")
        if name == "pull-state":
            p.add_argument("--force", action="store_true",
                           help="Replace local files with the copies in R2")

    p = sub.add_parser("measure", help="Road length and lookup estimates for an extract")
    p.add_argument("--pbf", required=True, help="Path to an .osm.pbf extract")
    p.add_argument("--out", default="measure.json", help="Where to write the full results")

    args = parser.parse_args(argv)
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    {"download": cmd_download, "estimate": cmd_estimate, "sample": cmd_sample,
     "compare": cmd_compare, "tiles": cmd_tiles, "refresh": cmd_refresh,
     "publish": cmd_publish, "pull-state": cmd_pull_state,
     "publish-page": cmd_publish_page, "push-state": cmd_push_state, "measure": cmd_measure,
     "overview": cmd_overview}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
