# streetview-heatmap

A map of UK roads coloured by the age of their Google Street View imagery, so you can see at a glance where
imagery is fresh and where it's stale.

## How it works

A monthly batch job builds two static files: a PMTiles map file and one HTML page.

1. Read road geometry from a Geofabrik OpenStreetMap extract (all highway types).
2. Sample each road against the Street View metadata API by bisection: check both ends of a stretch, and only
   look in between when they disagree. Only car imagery (`© Google`) counts as dated.
3. Cache every lookup in SQLite, so interrupted runs resume and monthly refreshes only re-check what changed.
4. Build vector tiles with tippecanoe and show them on a MapLibre page.
5. Upload to Cloudflare R2, shared by an unlisted link.

Leeds (5,302 km of road): about 115k free lookups for a full run, 14% of that for a monthly refresh,
and a 7.9 MB map.

## Using it

Everything lives in [`pipeline/`](pipeline/README.md): setup, commands, how sampling and refresh work,
hosting and the monthly GitHub Actions build (`.github/workflows/build-map.yml`).

```bash
python3 -m venv .venv
.venv/bin/pip install -r pipeline/requirements.txt pytest pytest-asyncio
cp .env.example .env    # then fill in your keys
.venv/bin/python -m pytest
```

## History

This replaced a Flask + APScheduler + PostgreSQL/PostGIS app that sampled a 0.05° tile grid via Overpass.
It was removed in October 2026. The last commit containing it is `77733cf`.
