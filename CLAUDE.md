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
python3 -m pipeline.run pull-state / push-state --area leeds
python3 -m pipeline.run measure  --pbf <extract>         # size up an extract, no API calls
python3 -m pipeline.run overview --area uk              # merge shard hexagon tallies into the overview
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
- `pipeline/export.py`: GeoJSONSeq and tippecanoe
- `pipeline/overview.py`: H3 hexagon tallies per area, merged into the overview the page shows at low zoom
- `pipeline/publish.py`: R2 upload under a secret prefix, and state sync
- `pipeline/web/index.html`: MapLibre page; `DEFAULT_TILES` and `DEFAULT_OVERVIEW` are rewritten by `publish`
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
