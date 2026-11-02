# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Batch pipeline that maps the age of Google Street View imagery on all UK roads. It reads OSM roads from a Geofabrik
extract, samples them against the Street View metadata API, builds a PMTiles file with tippecanoe and publishes
it with a static MapLibre page to Cloudflare R2. There is no server, scheduler or database beyond a SQLite cache.

## Commands

```bash
python3 -m pytest                                        # all tests (no network, no osmium/tippecanoe needed)
python3 -m pytest tests/test_pipeline.py::TestPublish -v

python3 -m pipeline.run download --area leeds
python3 -m pipeline.run sample   --area leeds --mode bisect
python3 -m pipeline.run refresh  --area leeds           # monthly update after the first full sample
python3 -m pipeline.run tiles    --area leeds           # needs tippecanoe
python3 -m pipeline.serve                               # local preview with Range support
python3 -m pipeline.run publish  --area leeds           # upload to R2
python3 -m pipeline.run publish-page --area uk          # upload only the page (Deploy page workflow does this)
python3 -m pipeline.run pull-state / push-state --area leeds
python3 -m pipeline.run measure  --pbf <extract>         # size up an extract, no API calls
python3 -m pipeline.run overview --area uk              # merge shard hexagon tallies into the overview
python3 -m pipeline.run audit    --area uk              # how much new imagery would a refresh miss? (read-only)
# UK: areas uk-1 … uk-10 are sampled and tiled separately, merged with tile-join, published as --area uk
```

## Layout

- `pipeline/run.py`: CLI entry point; paths for data, cache and site output
- `pipeline/areas.py`: named areas (bounding boxes, or UK shards) and their Geofabrik extracts
- `pipeline/shards.py`: the UK split into ten shards of 1° cells, each about an hour of lookups
- `pipeline/measure.py`: km and lookup estimates for a whole extract, no API calls
- `pipeline/roads.py`: .osm.pbf → one `Segment` per OSM way (osmium)
- `pipeline/sampler.py`: dense and bisection sampling into `Piece`s
- `pipeline/streetview.py`: async metadata client, rate limiter, `classify` (only `© Google` is dated)
- `pipeline/cache.py`: SQLite lookup cache keyed on integer micro-degrees
- `pipeline/refresh.py`: monthly refresh via one sentinel per 250 m cell
- `pipeline/audit.py`: random-point audit of the refresh (Audit refresh workflow, manual only)
- `pipeline/export.py`: GeoJSONSeq and tippecanoe
- `pipeline/overview.py`: H3 hexagon tallies per area, merged into the overview the page shows at low zoom
- `pipeline/toplists.py`: the page's top tens (oldest roads waiting, biggest leaps, most updated, newly covered,
  busiest areas), per shard in `tiles` and merged by `overview`
- `pipeline/publish.py`: R2 upload under a secret prefix, the build history (`history/`), and state sync
- `pipeline/web/index.html`: MapLibre page; `DEFAULT_TILES`, `DEFAULT_OVERVIEW` and `DEFAULT_HISTORY` are rewritten
  by `publish`
- `pipeline/data/`: downloads, cache and build output (gitignored)

## Conventions

- Use `python3`, not `python`.
- Tests live in `tests/test_pipeline.py` and use fakes (fake resolvers, `FakeR2`), never the network.
- Never commit `.env` or keys. Never upload to the site bucket outside `R2_SITE_PREFIX`, and never connect a
  domain to the state bucket.

## Key Env Vars

- `GOOGLE_MAPS_API_KEY` (or `GMAPS_APIKEY`): Street View metadata API
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`: R2 API token
- `R2_SITE_PREFIX`: secret folder the site is published under
- `R2_BUCKET`, `R2_STATE_BUCKET`: optional bucket name overrides

## Current state and open items (November 2026)

- Live: the whole UK, published to R2 by the monthly Build map workflow (scheduled daily at 04:23 UTC; builds once
  a month, from the 2nd, if there's been no successful build since the 2nd). Page-only
  changes go live within a minute of reaching `main` via the Deploy page workflow. First full
  build 2026-10-23 took 14 h (60–104 min of lookups per shard). A refresh takes about 4 h (18–26 min per shard, of
  which 11–17 min refresh) since sentinels are really re-asked each build. Tiles are about 1.2 GB per version, the
  overview 3.0 MB (0.5 MB compressed), R2 total about 3.3 GB of the 10 GB free tier.
- GitHub's scheduler skipped the 2 November 04:23 build entirely (no run, no incident on githubstatus.com, workflow
  still enabled); it was started by hand. Hence the daily schedule with a once-a-month guard. If no scheduled runs at
  all appear in the Actions tab after a few days, the schedule itself isn't firing: one suspect is that the commits
  adding the cron line were authored as Claude <noreply@anthropic.com>, not a GitHub account; a one-line edit of the
  schedule by the owner on GitHub would rule that out.
- Biggest leaps: Google's metadata lookup returns the nearest panorama, not the newest, so an old capture can date a
  stretch when newer imagery is close by (Plane Tree Way, Woodstock showed 2008 → 2026 though Maps had June 2022).
  Leaps are now measured from the newest date the previous result had anywhere on the road; the map itself can
  still show an older date than Maps in such spots.
- Cloud sessions can't download from Geofabrik (the proxy drops the connection), so real builds run on GitHub
  Actions. The site's Cloudflare zone blocks non-browser user agents (error 1010): send a browser User-Agent when
  checking it from scripts.
- Decided: the classic green-to-red palette stays the default, with the Okabe–Ito colour-blind safe palette as an
  opt-in toggle (owner's choice, 2026-10-24, although the classic palette fails the palette checks).
- Refresh misses (open question): sentinels are chosen the same way every month, so a refresh can keep missing new
  imagery on roads that aren't their cell's sentinel. Measured with the Audit refresh workflow (baseline before a
  build, compare straight after):
  - Until 2026-10-29 refreshes reused cached sentinel answers under 25 days old and compared sentinels with their
    stretch's date, so they saw almost nothing new (48 new pieces in uk-1 on 25 Oct) and re-sampled ~5,600 cells per
    shard for nothing. Fixed in #44: a 2-day reuse window, sentinels compared with their own previous lookup.
  - The 2 November build (the first with the fix) found new imagery on 72,729 of 9.4M pieces (0.8%), mostly a backlog,
    and flagged 30,898 changed cells (2.0% of sentinels).
  - Compare, 29 Oct baseline vs the 2 Nov build (50,000 points, 4 days): new imagery on 0.21% of road; the map got
    0.14%, the refresh **missed 0.07%** (a third of what was new). Misses were 24–30% of new imagery on dated major
    and minor roads, but 93% on roads the map showed with no imagery (sentinels prefer covered roads, so first-time
    coverage is mostly missed and the "Newly covered roads" top ten undercounts). Lookup noise at the same point:
    0.1%. Small numbers (36 missed points), so treat the shares as rough.
  - Decision so far: no general rotating sentinel (well under the 1–2% threshold). Next: baseline about 28 November,
    compare straight after the December build, for a full month's window. If first-time coverage is still mostly
    missed, consider a targeted extra sentinel in cells with uncovered roads, or a full rebuild every six months.
- Open decision: what to do about path imagery (Google Trekker imagery is also "© Google", so parks can show dated
  imagery even though the aim is car imagery only).
- Not built yet: a council league table (ONS boundaries; owner unsure it's worth it). The "new imagery" highlight
  compares each build with the result saved by the one before it, so it shows what changed between consecutive
  builds (monthly once on schedule). The update digest and history start with the first build published after
  they were added; earlier builds have no history entry.
- Owner to-dos outside the repo: Google project budget alert, Cloudflare two-factor login, optionally disable the
  Maps Static API, delete the unused `GMAPS_APIKEY_REPO` secret, and shut down the old Flask app if it still runs
  anywhere.
