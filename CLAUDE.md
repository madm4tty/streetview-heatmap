# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Flask web app that visualizes the age of Google Street View imagery across UK road networks. It queries the Google Street View metadata API and colours OpenStreetMap road segments by capture date. Uses PostgreSQL/PostGIS in production and SQLite for development/testing.

## Commands

```bash
# Run the app (default port 5001, not 5000 — avoids macOS AirPlay conflict)
python3 run.py
python3 run.py --debug --port 5001

# Run all tests
python3 -m pytest

# Run a single test file
python3 -m pytest tests/test_api.py

# Run a specific test
python3 -m pytest tests/test_api.py::test_health_endpoint -v

# Run with coverage
python3 -m pytest --cov=app --cov-report=html

# Start PostgreSQL (required for production features)
docker-compose up -d postgres

# Run database migrations
python3 migrations/001_create_job_status.py
```

## Architecture

### Processing Pipeline

`scheduler.py` selects tiles → `processing.py` runs per-tile pipeline:
1. Fetch OSM roads from Overpass API
2. Sample evenly-spaced points along roads (adaptive by road importance)
3. Check database cache to skip known points
4. Async batch query Google Street View metadata API (`aiohttp`)
5. Save results + road LineString geometries to database

### Tile System

UK is divided into a 0.05° grid (~44,000 tiles). Each tile is named `tile_{lonIdx}_{latIdx}`. Tiles are prioritised by city overlap (high/medium/low) defined in `geographic_scope.py`. The scheduler uses a two-phase strategy: process never-seen tiles first (high→medium→low), then refresh stale data by age thresholds.

### Database Layer (`database.py`)

Dual-backend abstraction — PostgreSQL with PostGIS or SQLite. PostgreSQL-specific code paths are guarded with `if _backend == "postgresql"`. Key tables: `metadata` (Street View points with geometry), `road_segments` (pre-computed LineStrings), `job_status` (background job tracking). Uses `psycopg2.extras.execute_values()` for bulk inserts.

### Frontend

Vanilla JS + Leaflet.js. Viewport-based tile loading — only requests data for tiles visible on screen. The frontend requests `format=roads` to get LineString GeoJSON (falls back to points). Key files: `app/static/js/map.js` (map logic), `app/static/js/api.js` (API client with auth headers).

### Configuration (`config.py` + `config.yaml`)

YAML config with environment variable substitution (`${VAR}` or `${VAR:-default}`). Runtime-updatable via `POST /api/config` (requires API key). Write endpoints use `X-API-Key` header auth via `require_api_key` decorator.

### Background Scheduler (`app/scheduler.py`)

APScheduler `BackgroundScheduler` with thread-safe job state (`_job_lock`). Module-level state tracks `_current_job` and `_last_job_result`. Only one job runs at a time.

## Testing Patterns

- Tests set `DATABASE_URL=''` to force SQLite backend
- Each test file sets env vars at module level before importing app code
- Flask test client via `app.test_client()` fixture (no conftest.py — fixtures are per-file)
- External services (database, Overpass API, Google API) are mocked with `unittest.mock.patch`
- CI runs via GitHub Actions (`.github/workflows/python-app.yml`)

## Key Env Vars

- `GOOGLE_MAPS_API_KEY` — Google Street View metadata API
- `DATABASE_URL` — PostgreSQL connection string (empty = SQLite)
- `API_KEY` — auth for write API endpoints
- `GMAPS_APIKEY` — GitHub Codespaces alias, auto-mapped to `GOOGLE_MAPS_API_KEY`
