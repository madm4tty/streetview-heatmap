"""Upload the built site, and the pipeline's state, to Cloudflare R2.

R2 speaks the S3 API, so boto3 talks to it with an R2 endpoint URL.

Two buckets:
- the site bucket is public on a custom domain. Everything goes under a secret
  folder (R2_SITE_PREFIX): <prefix>/index.html and <prefix>/tiles/<area>-<stamp>.pmtiles.
  R2 never lists a bucket, so only people with the link can find the map;
- the state bucket (private, no custom domain) holds the lookup cache and the last
  sampling result, so the monthly job on a fresh machine can `refresh` instead of
  starting again.
"""

import gzip
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("pipeline")


# The current tiles plus the previous ones, so a page loaded just before a
# publish keeps working.
KEEP_VERSIONS = 2

# Each tiles file has a unique name, so browsers may cache it forever.
# index.html changes on every publish, so browsers must re-check it.
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"

DEFAULT_TILES_RE = re.compile(r'(const DEFAULT_TILES = )"[^"]*";')

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


def tiles_key(area: str, now: datetime) -> str:
    return f"tiles/{area}-{now:%Y%m%d-%H%M%S}.pmtiles"


def point_page_at(html: str, tiles_path: str) -> str:
    """Set the page's default tiles file to `tiles_path`."""
    new_html, n = DEFAULT_TILES_RE.subn(lambda m: f'{m.group(1)}"{tiles_path}";', html)
    if n != 1:
        raise ValueError("index.html must contain exactly one `const DEFAULT_TILES = \"...\";` line")
    return new_html


def prune_tiles(client, bucket: str, prefix: str, area: str,
                keep: int = KEEP_VERSIONS) -> List[str]:
    """Delete all but the newest `keep` tiles files for `area`. Returns the deleted keys."""
    # Match the exact area, so pruning "leeds" never touches "leeds-centre"
    pattern = re.compile(rf"{re.escape(prefix)}/tiles/{re.escape(area)}-\d{{8}}-\d{{6}}\.pmtiles$")
    resp = client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/tiles/{area}-")
    keys = sorted(o["Key"] for o in resp.get("Contents", []) if pattern.match(o["Key"]))
    old = keys[:-keep] if keep > 0 else keys
    for key in old:
        client.delete_object(Bucket=bucket, Key=key)
        logger.info("Deleted old tiles %s", redact(key, prefix))
    return old


def publish_site(client, bucket: str, prefix: str, area: str, site_dir: str,
                 now: Optional[datetime] = None) -> str:
    """Upload the area's tiles under a new name, then an index.html pointing at them.

    Returns the key of the uploaded page.
    """
    pmtiles = os.path.join(site_dir, f"{area}.pmtiles")
    index = os.path.join(site_dir, "index.html")
    for path in (pmtiles, index):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found. Run: python3 -m pipeline.run tiles --area {area}")

    # Relative to the page, so the page itself never needs to know the prefix
    tiles = tiles_key(area, now or datetime.now(timezone.utc))
    tiles_object = f"{prefix}/{tiles}"
    # Tiles first, so the live page never points at a file that isn't there yet
    logger.info("Uploading %s -> %s/%s", pmtiles, bucket, redact(tiles_object, prefix))
    client.upload_file(pmtiles, bucket, tiles_object, ExtraArgs={
        "ContentType": "application/octet-stream", "CacheControl": IMMUTABLE})

    with open(index, encoding="utf-8") as f:
        page = point_page_at(f.read(), tiles)
    page_object = f"{prefix}/index.html"
    client.put_object(Bucket=bucket, Key=page_object, Body=page.encode("utf-8"),
                      ContentType="text/html; charset=utf-8", CacheControl=NO_CACHE)
    logger.info("Uploaded %s", redact(page_object, prefix))

    prune_tiles(client, bucket, prefix, area)
    return page_object


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
