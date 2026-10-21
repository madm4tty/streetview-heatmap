"""Async client for the Street View Static API metadata endpoint.

Metadata requests are free and don't use quota (verified Oct 2026), but the
API is still limited to 30,000 queries/minute, so concurrency is capped and
transient failures back off and retry.
"""

import asyncio
import logging
import ssl
from typing import Dict, List, Optional

import aiohttp
import certifi

from pipeline.cache import Key, Lookup, MetadataCache

logger = logging.getLogger(__name__)

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"

# Statuses worth retrying; anything else except OK/ZERO_RESULTS/NOT_FOUND is fatal
TRANSIENT_STATUSES = {"OVER_QUERY_LIMIT", "UNKNOWN_ERROR"}
DEFINITIVE_STATUSES = {"OK", "ZERO_RESULTS", "NOT_FOUND"}

NO_COVERAGE = "none"
NON_GOOGLE = "other"


def classify(lookup: Lookup) -> str:
    """Reduce a lookup to the value the map shows.

    Returns:
        "YYYY-MM" for Google's own imagery, NO_COVERAGE when there is no
        panorama, or NON_GOOGLE for user-contributed photospheres.
    """
    if lookup.status != "OK":
        return NO_COVERAGE
    if not lookup.copyright or "Google" not in lookup.copyright or not lookup.date:
        return NON_GOOGLE
    return lookup.date[:7]


class FatalAPIError(RuntimeError):
    """The API rejected the request (bad key, API not enabled, etc.)."""


class BudgetExceeded(RuntimeError):
    """A run would make more API calls than --max-calls allows."""


class RateLimiter:
    """Spaces out requests so no more than `per_second` start each second."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self.next_slot = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = asyncio.get_running_loop().time()
            delay = self.next_slot - now
            self.next_slot = max(now, self.next_slot) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


class StreetViewClient:
    """Looks up metadata for many points, using and filling the cache."""

    def __init__(self, api_key: str, cache: MetadataCache, concurrency: int = 50,
                 max_calls: Optional[int] = None, save_every: int = 500,
                 rate_per_second: float = 300.0):
        self.api_key = api_key
        self.cache = cache
        self.concurrency = concurrency
        self.max_calls = max_calls
        self.save_every = save_every
        # Google's limit is 30,000/min (500/s); 300/s leaves headroom
        self.rate_per_second = rate_per_second
        self.api_calls = 0
        self.cache_hits = 0

    async def lookup_many(self, keys: List[Key],
                          fresh_after: Optional[str] = None) -> Dict[Key, Lookup]:
        """Return a Lookup for every key, querying the API only for misses.

        Args:
            keys: Points to look up
            fresh_after: Treat cached entries older than this UTC timestamp
                ("YYYY-MM-DD HH:MM:SS") as misses; used by refresh runs.
        """
        keys = list(dict.fromkeys(keys))
        results = self.cache.get_many(keys, fresh_after=fresh_after)
        self.cache_hits += len(results)
        missing = [k for k in keys if k not in results]
        if not missing:
            return results

        if self.max_calls is not None and self.api_calls + len(missing) > self.max_calls:
            raise BudgetExceeded(
                f"Need {len(missing)} more calls; {self.api_calls} already made, "
                f"limit is {self.max_calls}")

        sem = asyncio.Semaphore(self.concurrency)
        self._limiter = RateLimiter(self.rate_per_second)
        pending: List = []
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=ssl_context, limit=self.concurrency),
                    timeout=timeout) as session:

                async def fetch(key: Key) -> None:
                    async with sem:
                        lookup = await self._fetch_one(session, key)
                    self.api_calls += 1
                    if self.api_calls % 5000 == 0:
                        logger.info("%d API calls made", self.api_calls)
                    results[key] = lookup
                    pending.append((key, lookup))
                    if len(pending) >= self.save_every:
                        self.cache.put_many(pending[:])
                        pending.clear()

                await asyncio.gather(*(fetch(k) for k in missing))
        finally:
            # Keep whatever was fetched, even if the run is aborted
            if pending:
                self.cache.put_many(pending)
        return results

    async def _fetch_one(self, session: aiohttp.ClientSession, key: Key,
                         attempts: int = 5) -> Lookup:
        lat_e6, lon_e6, radius = key
        params = {
            "location": f"{lat_e6 / 1e6:.6f},{lon_e6 / 1e6:.6f}",
            "radius": str(radius),
            "source": "outdoor",
            "key": self.api_key,
        }
        for attempt in range(attempts):
            await self._limiter.wait()
            try:
                async with session.get(METADATA_URL, params=params) as resp:
                    if resp.status >= 500 or resp.status == 429:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status)
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                status, error = "HTTP_ERROR", type(exc).__name__
            else:
                status, error = data.get("status"), data.get("error_message")
                if status in DEFINITIVE_STATUSES:
                    loc = data.get("location") or {}
                    return Lookup(status, data.get("pano_id"), data.get("date"),
                                  loc.get("lat"), loc.get("lng"), data.get("copyright"))
                if status not in TRANSIENT_STATUSES:
                    raise FatalAPIError(f"{status}: {error}")
            if attempt == attempts - 1:
                break
            # Rate limiting resets per minute, so back off harder for it
            delay = 15 * (attempt + 1) if status == "OVER_QUERY_LIMIT" else 2 ** attempt
            logger.debug("Lookup %s failed (%s), retrying in %ds", key, error or status, delay)
            await asyncio.sleep(delay)
        if status == "UNKNOWN_ERROR":
            # Some locations return UNKNOWN_ERROR every time. Cache them
            # (classified as no coverage) so they aren't retried forever.
            logger.warning("Lookup %s gave UNKNOWN_ERROR %d times; treating as no imagery",
                           key, attempts)
            return Lookup("UNKNOWN_ERROR")
        raise RuntimeError(f"Lookup {key} failed after {attempts} attempts ({status})")
