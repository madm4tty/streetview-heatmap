# streetview-heatmap

This project visualizes the age of Google Street View imagery across UK road networks. It queries the Google Street View metadata API and colours OpenStreetMap road segments according to the capture date of nearby imagery.

## Features

- **Interactive Web Frontend**: Full-featured web interface with interactive map
- **REST API**: Web backend with automated scheduling
- **PostgreSQL/PostGIS**: Spatial database for UK-wide coverage
- **Smart Refresh**: Priority-based tile processing with age tracking
- **Background Jobs**: Automated scheduled updates

## Requirements

- Python 3.8+
- A Google Maps API key (`GOOGLE_MAPS_API_KEY` environment variable)
- PostgreSQL with PostGIS (for web backend and UK-wide coverage)

## Quick Start

### Installation

```bash
# Clone the repository
git clone git@github.com:madm4tty/streetview-heatmap.git
cd streetview-heatmap

# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env with your API keys
```

### Web Backend

```bash
# Start PostgreSQL (using Docker)
docker-compose up -d postgres

# Set environment variables
export DATABASE_URL=postgresql://streetview:streetview_dev@localhost:5432/streetview
export GOOGLE_MAPS_API_KEY=YOUR_KEY
export API_KEY=your_secure_api_key

# Run the web server
python run.py
```

The application will be available at `http://localhost:5001/`

> **Note:** The application runs on port **5001** (not 5000) to avoid conflicts with macOS AirPlay Receiver which uses port 5000.

---

## Web Frontend

The application includes a full-featured web frontend built with vanilla JavaScript and Leaflet.

### Accessing the Application

Open your browser and navigate to `http://localhost:5001/`

### Pages

| Page | URL | Description |
|------|-----|-------------|
| **Map** | `/` | Interactive map with Street View coverage visualization |
| **Dashboard** | `/dashboard` | System status, coverage statistics, job monitoring |
| **Config** | `/config` | Configuration management (requires API key) |
| **Help** | `/instructions` | Documentation and usage guide |

### Map Features

- **Interactive Leaflet Map** centered on the UK
- **Color-Coded Roads** by Street View image age:
  - 🟢 Green: Less than 3 months old
  - 🟡 Yellow: Less than 1 year old
  - 🟠 Orange: Less than 3 years old
  - 🔴 Red: 3+ years old
- **Viewport-Based Loading**: Only loads tiles visible on screen
- **Search**: Jump to cities by name
- **Coverage Grid**: Toggle layer to see tile boundaries
- **Tooltips & Popups**: Click or hover for details

### Dashboard Features

- **System Status**: Current state, scheduler info, last/next update
- **Coverage Statistics**: Progress bars by priority level
- **Current Job Monitoring**: Real-time progress when updates are running
- **Database Statistics**: Entry counts and coverage metrics
- **Manual Updates**: Trigger updates with priority filters

### Configuration

The Config page allows authenticated users to modify:
- **Scheduler Settings**: Enable/disable, update interval
- **Update Settings**: Batch size, concurrency, minimum age for recheck
- **API Settings**: Overpass delay, samples per road, adaptive sampling

### Browser Compatibility

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+

### Screenshots

#### Map View
The interactive map displays road segments colored by their Street View image age, with a legend and layer controls.

#### Dashboard
Real-time monitoring of system status, coverage progress, and job execution.

#### Configuration
Form-based interface for modifying application settings with validation.

---

## Web API Documentation

### Base URL

All API endpoints are prefixed with `/api/`

```
http://localhost:5001/api/
```

### Authentication

Write operations (POST endpoints) require an `X-API-Key` header:

```bash
curl -X POST http://localhost:5001/api/update/trigger \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json"
```

### Endpoints

#### Health Check

```
GET /api/health
```

Returns service health status.

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "timestamp": "2026-02-02T14:30:00Z"
}
```

#### System Status

```
GET /api/status
```

Returns comprehensive system status including coverage statistics.

**Response:**
```json
{
  "status": "running",
  "last_update": "2026-02-01T14:30:00Z",
  "next_update": "2026-02-02T02:00:00Z",
  "coverage": {
    "high": {
      "total_tiles": 500,
      "tiles_with_data": 120,
      "locations_total": 250000,
      "locations_checked": 85000,
      "percent_complete": 34.0
    },
    "medium": { ... },
    "low": { ... }
  },
  "current_job": {
    "running": true,
    "job_id": "job_20260201_143000",
    "tiles_processed": 15,
    "tiles_total": 50
  },
  "database": {
    "total_entries": 125000,
    "entries_with_dates": 118000,
    "unique_tiles": 156
  }
}
```

#### List Tiles

```
GET /api/tiles
```

List tiles with metadata and pagination.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| priority | string | - | Filter by priority (high/medium/low) |
| has_data | boolean | - | Filter by whether tile has data |
| page | integer | 1 | Page number |
| per_page | integer | 100 | Items per page (max 500) |

**Response:**
```json
{
  "tiles": [
    {
      "tile_id": "tile_126_78",
      "bbox": [-1.70, 53.80, -1.65, 53.85],
      "priority": "high",
      "has_data": true,
      "location_count": 1250,
      "last_updated": "2026-02-01T12:00:00Z"
    }
  ],
  "total": 44000,
  "page": 1,
  "per_page": 100,
  "pages": 440
}
```

#### Get Tile Data

```
GET /api/tiles/{tile_id}/data
```

Returns GeoJSON data for a specific tile.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| format | string | points | Output format: "points" or "roads" |

**Response:** GeoJSON FeatureCollection

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [-1.65, 53.80]
      },
      "properties": {
        "date": "2023-06-15",
        "color": "#00ff00"
      }
    }
  ]
}
```

#### Trigger Update Job

```
POST /api/update/trigger
```

Manually trigger a tile update job. Requires API key.

**Request Body:**
```json
{
  "priority": "high",
  "tile_limit": 20
}
```

**Response (200):**
```json
{
  "status": "started",
  "job_id": "job_20260202_143000",
  "message": "Update job started"
}
```

**Response (409 - Job already running):**
```json
{
  "error": "Conflict",
  "message": "Update job already running"
}
```

#### Get Update Status

```
GET /api/update/status
```

Get current update job progress or last completed job info.

**Response (job running):**
```json
{
  "running": true,
  "job_id": "job_20260202_143000",
  "started_at": "2026-02-02T14:30:00Z",
  "tiles_processed": 15,
  "tiles_total": 50,
  "percent_complete": 30.0,
  "current_tile": "tile_126_78"
}
```

#### Get Configuration

```
GET /api/config
```

Returns current configuration (sensitive values masked).

**Response:**
```json
{
  "scheduler": {
    "enabled": true,
    "interval_hours": 24,
    "next_run": "2026-02-02T02:00:00Z"
  },
  "update": {
    "batch_size": 50,
    "concurrency": 20,
    "min_age_for_recheck_days": 90,
    "overpass_delay_seconds": 2,
    "samples_per_road": 5,
    "adaptive_sampling": true
  }
}
```

#### Update Configuration

```
POST /api/config
```

Update configuration values. Requires API key.

**Request Body:**
```json
{
  "scheduler": {
    "interval_hours": 12
  },
  "update": {
    "batch_size": 100
  }
}
```

#### List Cities

```
GET /api/cities
```

List UK cities with bounding boxes and center coordinates.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| priority | string | Filter by priority level |

**Response:**
```json
{
  "cities": [
    {
      "name": "London",
      "key": "london",
      "bbox": [-0.51, 51.28, 0.33, 51.69],
      "lat": 51.485,
      "lon": -0.09,
      "priority": "high"
    }
  ],
  "total": 130
}
```

---

## Configuration

Configuration is managed via `config.yaml` with environment variable substitution:

```yaml
app:
  host: 0.0.0.0
  port: 5001
  api_key: ${API_KEY:-changeme}
  debug: false

database:
  url: ${DATABASE_URL}

google:
  api_key: ${GOOGLE_MAPS_API_KEY}

scheduler:
  enabled: true
  interval_hours: 24
  # Or use cron: "0 2 * * *"

update:
  batch_size: 50
  concurrency: 20
  min_age_for_recheck_days: 90
  overpass_delay_seconds: 2
  samples_per_road: 5
  adaptive_sampling: true

logging:
  level: INFO
  file: logs/heatmap_app.log
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_MAPS_API_KEY` | Yes | Google Maps API key for Street View |
| `DATABASE_URL` | Yes (web) | PostgreSQL connection string |
| `API_KEY` | No | API key for write operations (default: changeme) |

---

## Database Setup

### Docker (Recommended)

```bash
# Start PostgreSQL with PostGIS
docker-compose up -d postgres

# Set connection string
export DATABASE_URL=postgresql://streetview:streetview_dev@localhost:5432/streetview

# Run migrations
python migrations/001_create_job_status.py
```

### Manual PostgreSQL

```sql
CREATE DATABASE streetview;
\c streetview
CREATE EXTENSION postgis;
```

---

## Tile Prioritisation & Processing

### How Tile Priority Works

Every tile in the UK grid (~44,000 tiles at 0.05° x 0.05°) is assigned a priority based on whether it overlaps with a defined city bounding box. The priority is determined by `get_tile_priority()` in `geographic_scope.py`:

| Priority | Criteria | Cities |
|----------|----------|--------|
| **High** | Overlaps a major metropolitan area | London, Birmingham, Manchester, Leeds, Glasgow, Liverpool, Newcastle, Sheffield, Bristol, Edinburgh, Cardiff, Belfast, Nottingham, Leicester, Coventry (15 cities) |
| **Medium** | Overlaps a regional centre | Bradford, Nottingham, Cambridge, Oxford, Bath, Chester, York, Brighton, etc. (~120 cities) |
| **Low** | No city overlap, or overlaps a smaller town | Everything else (rural areas, remote regions, smaller towns) |

The algorithm makes two passes over the city list — first checking for high-priority city overlap, then medium. If no city overlaps the tile at all, it defaults to **low**. This means a tile covering both a high and medium city will be classified as high.

### Processing Pipeline Per Tile

When a tile is selected for processing (`process_tile()` in `app/processing.py`), it goes through:

1. **Fetch OSM roads** — Queries the Overpass API for highway geometries within the tile's bounding box
2. **Sample coordinates** — Takes evenly-spaced points along each road segment. The number of samples is adaptive by default (motorways get more than residential streets)
3. **Check database cache** — Looks up already-known points to avoid redundant API calls
4. **Fetch missing metadata** — Async batch queries to the Google Street View Static Metadata API for any uncached points
5. **Save results** — Persists lat/lon, capture date, tile_id, and priority to the database

### What Happens When a Tile Has No City Overlap

**Metadata is still collected.** Non-city tiles are assigned `priority: "low"` and processed through the same pipeline. The only scenario where no metadata is saved is when a tile contains **zero OSM roads** (very rare in the UK) — in that case the tile returns early with `roads_found: 0` and no database rows are written. However, since it has no metadata entries, it will be re-selected as "never processed" on the next run.

### Smart Refresh Strategy

The scheduler (`_get_tiles_to_process()` in `app/scheduler.py`) selects tiles using a two-phase approach:

**Phase 1 — Never-processed tiles** (highest priority):

Tiles that exist in the geographic scope but have no entries in the metadata table, processed in order: high → medium → low.

**Phase 2 — Stale data refresh:**

Tiles that have been processed before but have old data, using age thresholds:

| Age Threshold | Priority | Effect |
|---------------|----------|--------|
| > 3 years | High | Re-scanned first |
| > 3 years | Medium | Re-scanned second |
| > 1 year | High | Re-scanned third |
| > 1 year | Medium | Re-scanned fourth |
| > `min_age_for_recheck_days` (default 90) | Low | Re-scanned last |

This ensures major cities are always processed first and refreshed most frequently, while rural tiles are still covered over time.

### Triggering a Manual Scan

You can trigger a job at any time via the API, optionally filtering by priority to target specific areas:

```bash
# Scan only high-priority tiles (major cities)
curl -X POST http://localhost:5001/api/update/trigger \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"priority": "high", "tile_limit": 100}'

# Scan medium-priority tiles
curl -X POST http://localhost:5001/api/update/trigger \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"priority": "medium", "tile_limit": 50}'

# Monitor job progress
curl http://localhost:5001/api/update/status | jq
```

The `tile_limit` parameter accepts values from 1 to 1000. Only one job can run at a time (a 409 is returned if a job is already running).

### Performance Tuning

The Street View Static Metadata API supports up to **30,000 requests per minute** with **unlimited daily requests**. The default configuration is conservative:

| Parameter | Default | Description | Config Key |
|-----------|---------|-------------|------------|
| `batch_size` | 50 | Tiles processed per job run | `update.batch_size` |
| `concurrency` | 20 | Parallel Street View API requests | `update.concurrency` |
| `overpass_delay_seconds` | 2 | Delay between Overpass queries (rate limiting) | `update.overpass_delay_seconds` |
| `samples_per_road` | 5 | Base sample points per road segment | `update.samples_per_road` |
| `min_age_for_recheck_days` | 90 | Days before low-priority tiles are rechecked | `update.min_age_for_recheck_days` |

These can be changed in `config.yaml` (requires restart) or at runtime via the API:

```bash
# Increase throughput at runtime (no restart needed)
curl -X POST http://localhost:5001/api/config \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"update": {"batch_size": 200, "concurrency": 100}}'
```

### Background Scheduler

The web backend includes APScheduler running update jobs automatically. By default it runs every 24 hours with `max_instances=1` to prevent overlapping runs. It uses the same smart refresh strategy described above.

Scheduling can be configured as interval-based or cron-based:

```yaml
scheduler:
  enabled: true
  interval_hours: 24    # Run every 24 hours
  # Or use cron: "0 2 * * *"  # Run at 2am daily
```

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test files
pytest tests/test_api.py
pytest tests/test_scheduler.py
pytest tests/test_processing.py
pytest tests/test_config.py
```

---

## Example API Requests

```bash
# Health check
curl http://localhost:5001/api/health

# Get system status
curl http://localhost:5001/api/status

# List high-priority tiles with data
curl "http://localhost:5001/api/tiles?priority=high&has_data=true&per_page=10"

# Get tile GeoJSON data
curl http://localhost:5001/api/tiles/tile_126_78/data

# Trigger update job (requires API key)
curl -X POST http://localhost:5001/api/update/trigger \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"priority": "high", "tile_limit": 10}'

# Check update job status
curl http://localhost:5001/api/update/status

# Get configuration
curl http://localhost:5001/api/config

# List cities
curl http://localhost:5001/api/cities
```

---

## Project Structure

```
streetview-heatmap/
├── app/
│   ├── __init__.py         # Flask app factory
│   ├── routes.py           # API endpoints
│   ├── pages.py            # Web page routes
│   ├── models.py           # Pydantic validation models
│   ├── scheduler.py        # Background job scheduler
│   ├── processing.py       # Core processing logic
│   ├── templates/          # Jinja2 templates
│   │   ├── base.html       # Base template with navigation
│   │   ├── index.html      # Map page
│   │   ├── dashboard.html  # Status dashboard
│   │   ├── config.html     # Configuration panel
│   │   └── instructions.html # Help page
│   └── static/
│       ├── css/
│       │   ├── main.css    # Main styles
│       │   └── map.css     # Map-specific styles
│       ├── js/
│       │   ├── api.js      # API client wrapper
│       │   ├── utils.js    # Shared utilities
│       │   ├── map.js      # Interactive map
│       │   ├── dashboard.js # Dashboard logic
│       │   └── config.js   # Configuration page
│       └── images/
│           └── favicon.svg
├── migrations/
│   └── 001_create_job_status.py
├── tests/
│   ├── test_api.py
│   ├── test_scheduler.py
│   ├── test_processing.py
│   └── test_config.py
├── config.py               # Configuration management
├── config.yaml             # Default configuration
├── database.py             # Database abstraction
├── geographic_scope.py     # UK cities and tile system
├── run.py                  # Web app entry point
└── requirements.txt
```

---

## Geographic Coverage

### UK Major Cities (130+)

Defined in `geographic_scope.py` with priority levels:
- **High**: London, Birmingham, Manchester, Leeds, Glasgow, Liverpool, etc.
- **Medium**: York, Oxford, Brighton, Cambridge, etc.
- **Low**: Smaller towns

### Tile System

- UK bounds: -8.0°W to 2.0°E, 49.9°N to 60.9°N
- Tile size: 0.05° x 0.05° (~5km x 5km)
- ~44,000 tiles cover the UK
- Priority based on city overlap

---

## License

MIT License
