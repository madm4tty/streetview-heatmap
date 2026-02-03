# streetview-heatmap

This project visualizes the age of Google Street View imagery across UK road networks. It queries the Google Street View metadata API and colours OpenStreetMap road segments according to the capture date of nearby imagery.

## Features

- **CLI Tool**: Generate heatmaps for specific bounding boxes
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

### CLI Usage (Local Development)

```bash
# Set API key
export GOOGLE_MAPS_API_KEY=YOUR_KEY

# Generate heatmap for a specific area
python generate_heatmap.py \
  --bbox -1.70 53.79 -1.65 53.82 \
  --samples 5 \
  --output heatmap.html
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

The API will be available at `http://localhost:5000/api/`

---

## Web API Documentation

### Base URL

All API endpoints are prefixed with `/api/`

### Authentication

Write operations (POST endpoints) require an `X-API-Key` header:

```bash
curl -X POST http://localhost:5000/api/update/trigger \
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

List UK cities with bounding boxes.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| priority | string | Filter by priority level |

---

## Configuration

Configuration is managed via `config.yaml` with environment variable substitution:

```yaml
app:
  host: 0.0.0.0
  port: 5000
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
| `HEATMAP_DB` | No | SQLite path for CLI (default: metadata.db) |

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

### Migrating from SQLite

```bash
python migrate_to_postgres.py --sqlite-path metadata.db
```

---

## Background Scheduler

The web backend includes an automated scheduler that processes tiles based on a smart refresh strategy:

1. **High priority tiles** with locations never checked
2. **High priority tiles** with locations >3 years old
3. **Medium priority tiles** with locations >3 years old
4. **High priority tiles** with locations >1 year old
5. **Medium priority tiles** with locations >1 year old
6. **Low priority tiles** (if time permits)

The scheduler respects Overpass API rate limits with configurable delays.

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
curl http://localhost:5000/api/health

# Get system status
curl http://localhost:5000/api/status

# List high-priority tiles with data
curl "http://localhost:5000/api/tiles?priority=high&has_data=true&per_page=10"

# Get tile GeoJSON data
curl http://localhost:5000/api/tiles/tile_126_78/data

# Trigger update job (requires API key)
curl -X POST http://localhost:5000/api/update/trigger \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"priority": "high", "tile_limit": 10}'

# Check update job status
curl http://localhost:5000/api/update/status

# Get configuration
curl http://localhost:5000/api/config

# List cities
curl http://localhost:5000/api/cities
```

---

## Project Structure

```
streetview-heatmap/
├── app/
│   ├── __init__.py      # Flask app factory
│   ├── routes.py        # API endpoints
│   ├── models.py        # Pydantic validation models
│   ├── scheduler.py     # Background job scheduler
│   └── processing.py    # Core processing logic
├── migrations/
│   └── 001_create_job_status.py
├── tests/
│   ├── test_api.py
│   ├── test_scheduler.py
│   ├── test_processing.py
│   └── test_config.py
├── config.py            # Configuration management
├── config.yaml          # Default configuration
├── database.py          # Database abstraction
├── geographic_scope.py  # UK cities and tile system
├── generate_heatmap.py  # CLI tool
├── run.py               # Web app entry point
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
