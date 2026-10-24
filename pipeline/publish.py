"""Upload the built site, and the pipeline's state, to Cloudflare R2.

R2 speaks the S3 API, so boto3 talks to it with an R2 endpoint URL.

Two buckets:
- the site bucket is public on a custom domain. Everything goes under a secret
  folder (R2_SITE_PREFIX): <prefix>/index.html and <prefix>/tiles/<area>-<stamp>.pmtiles,
  plus <prefix>/history/: a copy of every build's overview, kept for good, and
  <area>.json, which lists them with their totals for the page's update digest.
  R2 never lists a bucket, so only people with the link can find the map;
- the state bucket (private, no custom domain) holds the lookup cache and the last
  sampling result, so the monthly job on a fresh machine can `refresh` instead of
  starting again.
"""

import gzip
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("pipeline")


# The current tiles plus the previous ones, so a page loaded just before a
# publish keeps working.
KEEP_VERSIONS = 2

# Refuse to publish beyond this, leaving headroom under R2's 10 GB free tier
STORAGE_LIMIT_BYTES = 8 * 1000 ** 3

# Each tiles file has a unique name, so browsers may cache it forever.
# index.html changes on every publish, so browsers must re-check it.
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"

PAGE_CONSTANT_RE = r'(const {name} = )"[^"]*";'

# Long and random enough that nobody guesses it, and safe in a URL path
SITE_PREFIX_RE = re.compile(r"[A-Za-z0-9_-]{16,}")


def site_bucket() -> str:
    return os.environ.get("R2_BUCKET", "streetview-heatmap")


def state_bucket() -> str:
    return os.environ.get("R2_STATE_BUCKET", "streetview-heatmap-state")


def site_prefix() -> str:
    """The secret folder the site is published under, from R2_SITE_PREFIX."""
    prefix = os.environ.get("R2_SITE_PREFIX", "")
    if not SITE_PREFIX_RE.fullmatch(prefix):
        raise SystemExit("Set R2_SITE_PREFIX to a random string of at least 16 letters, digits, "
                         "'-' or '_'. It becomes the secret part of the map's link.")
    return prefix


def r2_client(bucket: str):
    """Build an S3 client for R2 from R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY.

    Checks up front that `bucket` exists and the token can use it.
    """
    import boto3  # imported here so the rest of the pipeline and its tests don't need it

    names = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in the environment or .env")
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in ("403", "404", "NoSuchBucket", "AccessDenied"):
            raise SystemExit(f"R2 refused access to the {bucket!r} bucket ({code}). Check the bucket "
                             "exists and the API token's permissions include it.")
        raise
    return client


def redact(key: str, prefix: str) -> str:
    """Hide the secret folder in log output; the repo and its build logs are public."""
    return key.replace(prefix, "<prefix>")


def version_keys(area: str, now: datetime) -> Tuple[str, str]:
    """Tiles and overview paths for one publish, relative to the page."""
    stem = f"tiles/{area}-{now:%Y%m%d-%H%M%S}"
    return f"{stem}.pmtiles", f"{stem}.overview.json"


def history_key(area: str) -> str:
    """The area's list of builds, relative to the page."""
    return f"history/{area}.json"


def archive_key(area: str, now: datetime) -> str:
    """Where a build's overview is kept for good, relative to the page."""
    return f"history/{area}-{now:%Y%m%d-%H%M%S}.overview.json"


def set_page_constant(html: str, name: str, value: str) -> str:
    new_html, n = re.subn(PAGE_CONSTANT_RE.format(name=name),
                          lambda m: f'{m.group(1)}"{value}";', html)
    if n != 1:
        raise ValueError(f"index.html must contain exactly one `const {name} = \"...\";` line")
    return new_html


def point_page_at(html: str, tiles_path: str, overview_path: str = "", history_path: str = "") -> str:
    """Set the page's default tiles file, overview and history (empty: none)."""
    html = set_page_constant(html, "DEFAULT_TILES", tiles_path)
    html = set_page_constant(html, "DEFAULT_OVERVIEW", overview_path)
    return set_page_constant(html, "DEFAULT_HISTORY", history_path)


def list_objects(client, bucket: str, prefix: str = "") -> List[Dict]:
    """Every object under `prefix`, following list pagination."""
    objects, token = [], None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        objects += resp.get("Contents", [])
        if not resp.get("IsTruncated"):
            return objects
        token = resp["NextContinuationToken"]


def area_versions(client, bucket: str, prefix: str, area: str) -> List[List[Dict]]:
    """The area's published versions, oldest first; each is its tiles and overview files."""
    # Match the exact area, so "leeds" never picks up "leeds-centre"
    pattern = re.compile(rf"{re.escape(prefix)}/tiles/{re.escape(area)}-(\d{{8}}-\d{{6}})"
                         rf"\.(pmtiles|overview\.json)$")
    versions: Dict[str, List[Dict]] = {}
    for o in list_objects(client, bucket, f"{prefix}/tiles/{area}-"):
        match = pattern.match(o["Key"])
        if match:
            versions.setdefault(match.group(1), []).append(o)
    return [versions[stamp] for stamp in sorted(versions)]


def check_storage(client, buckets: List[str], adding: int, freeing: int,
                  limit: Optional[int] = None) -> int:
    """Raise SystemExit if storage after this publish would exceed `limit`. Returns it."""
    limit = STORAGE_LIMIT_BYTES if limit is None else limit
    used = sum(o.get("Size", 0) for b in buckets for o in list_objects(client, b))
    projected = used + adding - freeing
    logger.info("R2 storage: %.2f GB now, %.2f GB after publishing", used / 1e9, projected / 1e9)
    if projected > limit:
        raise SystemExit(f"Publishing would take R2 storage to {projected / 1e9:.2f} GB, over the "
                         f"{limit / 1e9:.0f} GB safety limit. Nothing was uploaded.")
    return projected


def prune_tiles(client, bucket: str, prefix: str, area: str,
                keep: int = KEEP_VERSIONS) -> List[str]:
    """Delete all but the newest `keep` versions for `area`. Returns the deleted keys."""
    versions = area_versions(client, bucket, prefix, area)
    old = [o["Key"] for v in (versions[:-keep] if keep > 0 else versions) for o in v]
    for key in old:
        client.delete_object(Bucket=bucket, Key=key)
        logger.info("Deleted old tiles %s", redact(key, prefix))
    return old


def publish_site(client, bucket: str, prefix: str, area: str, site_dir: str,
                 now: Optional[datetime] = None, also_count: Tuple[str, ...] = ()) -> str:
    """Upload the area's tiles under a new name, then an index.html pointing at them.

    Checks first that storage across `bucket` and `also_count` stays under the
    limit once old tiles are pruned. Returns the key of the uploaded page.
    """
    pmtiles = os.path.join(site_dir, f"{area}.pmtiles")
    index = os.path.join(site_dir, "index.html")
    for path in (pmtiles, index):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found. Run: python3 -m pipeline.run tiles --area {area}")
    # Optional: without it the page shows roads at every zoom and no "in view" panel
    overview_file = os.path.join(site_dir, f"{area}.overview.json")
    has_overview = os.path.exists(overview_file)

    existing = area_versions(client, bucket, prefix, area)
    pruned = existing[:max(0, len(existing) - (KEEP_VERSIONS - 1))]
    # The overview goes up twice: beside the tiles, and into the history
    adding = os.path.getsize(pmtiles) + (2 * os.path.getsize(overview_file) if has_overview else 0)
    check_storage(client, [bucket, *also_count], adding=adding,
                  freeing=sum(o.get("Size", 0) for v in pruned for o in v))

    # Relative to the page, so the page itself never needs to know the prefix
    now = now or datetime.now(timezone.utc)
    tiles, overview = version_keys(area, now)
    archive = archive_key(area, now)
    # Data first, so the live page never points at a file that isn't there yet
    uploads = [(pmtiles, tiles, "application/octet-stream")]
    if has_overview:
        uploads += [(overview_file, overview, "application/json"),
                    (overview_file, archive, "application/json")]
    for path, key, content_type in uploads:
        logger.info("Uploading %s -> %s/%s", path, bucket, redact(f"{prefix}/{key}", prefix))
        client.upload_file(path, bucket, f"{prefix}/{key}", ExtraArgs={
            "ContentType": content_type, "CacheControl": IMMUTABLE})
    if has_overview:
        record_history(client, bucket, prefix, area, overview_file, archive, now)

    page_object = upload_page(client, bucket, prefix, index, tiles,
                              overview if has_overview else "", history_key(area))
    prune_tiles(client, bucket, prefix, area)
    return page_object


def history_entry(overview: Dict, archive: str, now: datetime) -> Optional[Dict]:
    """One build's line in the history: when, its archived overview, totals and top areas.

    None for an overview from before totals were added.
    """
    if "totals" not in overview:
        return None
    return {"stamp": f"{now:%Y%m%d-%H%M%S}", "built": overview.get("built"), "overview": archive,
            **overview["totals"], "top": overview.get("top", [])}


def read_history(client, bucket: str, key: str) -> Dict:
    """The published history, or an empty one if there isn't one yet."""
    try:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:
        if not _is_missing(exc):
            raise
        return {"builds": []}
    return json.loads(body)


def record_history(client, bucket: str, prefix: str, area: str, overview_file: str,
                   archive: str, now: datetime) -> Optional[Dict]:
    """Add this build to the area's history file, oldest build first. Returns the new history."""
    with open(overview_file, encoding="utf-8") as f:
        entry = history_entry(json.load(f), archive, now)
    if entry is None:
        logger.info("Overview has no totals; history not updated")
        return None
    key = f"{prefix}/{history_key(area)}"
    history = read_history(client, bucket, key)
    builds = [b for b in history.get("builds", []) if b.get("stamp") != entry["stamp"]] + [entry]
    history = {"builds": sorted(builds, key=lambda b: b["stamp"])}
    # Changes on every publish, so browsers must re-check it, like the page
    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(history, separators=(",", ":")).encode(),
                      ContentType="application/json", CacheControl=NO_CACHE)
    logger.info("Recorded build %s in %s (%d builds)", entry["stamp"], redact(key, prefix), len(builds))
    return history


def upload_page(client, bucket: str, prefix: str, index: str, tiles: str, overview: str,
                history: str = "") -> str:
    """Upload index.html pointing at the given data files. Returns the page's key."""
    with open(index, encoding="utf-8") as f:
        page = point_page_at(f.read(), tiles, overview, history)
    page_object = f"{prefix}/index.html"
    client.put_object(Bucket=bucket, Key=page_object, Body=page.encode("utf-8"),
                      ContentType="text/html; charset=utf-8", CacheControl=NO_CACHE)
    logger.info("Uploaded %s", redact(page_object, prefix))
    return page_object


def publish_page(client, bucket: str, prefix: str, area: str, index: str) -> Tuple[str, str, str]:
    """Upload just the page, pointed at the newest data already published for `area`.

    For page-only changes: no lookups, tiles or overview are rebuilt, and no old
    versions are pruned. Returns the page's key and the tiles and overview paths
    it now uses.
    """
    if not os.path.exists(index):
        raise SystemExit(f"{index} not found")
    for version in reversed(area_versions(client, bucket, prefix, area)):
        keys = {o["Key"][len(prefix) + 1:] for o in version}  # relative to the page
        tiles = next((k for k in keys if k.endswith(".pmtiles")), None)
        if tiles:
            overview = tiles[:-len(".pmtiles")] + ".overview.json"
            overview = overview if overview in keys else ""
            page = upload_page(client, bucket, prefix, index, tiles, overview, history_key(area))
            return page, tiles, overview
    raise SystemExit(f"Nothing published for '{area}' yet: run a full build first.")


def _is_missing(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    return code in ("404", "NoSuchKey")


def push_state(client, bucket: str, paths: List[str]) -> List[str]:
    """Upload each existing file gzipped as <basename>.gz. Returns the keys uploaded."""
    uploaded = []
    for path in paths:
        if not os.path.exists(path):
            logger.info("No %s yet, skipping", path)
            continue
        key = os.path.basename(path) + ".gz"
        with tempfile.NamedTemporaryFile(suffix=".gz") as tmp:
            with open(path, "rb") as src, gzip.open(tmp.name, "wb") as dst:
                shutil.copyfileobj(src, dst)
            client.upload_file(tmp.name, bucket, key)
        logger.info("Saved %s -> %s/%s", path, bucket, key)
        uploaded.append(key)
    return uploaded


def pull_state(client, bucket: str, paths: List[str], force: bool = False) -> List[str]:
    """Download <basename>.gz for each path. Keeps local files unless `force`.

    Returns the paths written. A file that isn't in the bucket yet (the first
    run) is skipped, not an error.
    """
    written = []
    for path in paths:
        if os.path.exists(path) and not force:
            logger.info("Keeping local %s (use --force to replace it)", path)
            continue
        key = os.path.basename(path) + ".gz"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".gz") as tmp:
            try:
                client.download_file(bucket, key, tmp.name)
            except Exception as exc:
                if not _is_missing(exc):
                    raise
                logger.info("No %s/%s yet, skipping", bucket, key)
                continue
            # Write beside the target, then rename, so a failed download never
            # leaves a half-written cache
            partial = path + ".part"
            with gzip.open(tmp.name, "rb") as src, open(partial, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(partial, path)
        logger.info("Restored %s from %s/%s", path, bucket, key)
        written.append(path)
    return written
