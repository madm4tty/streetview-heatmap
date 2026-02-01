# streetview-heatmap


This project experiments with visualising the age of street-level imagery. The
included Python script queries the Google Street View metadata API and colours
OpenStreetMap road segments according to the capture date of nearby imagery.
The default bounding box covers Farsley, West Yorkshire.

## Requirements

- Python 3.8+
- A Google Maps API key (`GOOGLE_MAPS_API_KEY` environment variable). If you are
  using GitHub Codespaces, a secret called `GMAPS_APIKEY` will also be detected
  automatically.
- Optional: PostgreSQL with PostGIS for UK-wide coverage (see below)

Install dependencies:

```bash
pip install -r requirements.txt
```

Run this command before executing `generate_heatmap.py` or
`update_heatmaps.py` to ensure all required packages (such as `aiohttp`) are
available.


Create a `.env` file containing your Google Maps API key if you are running the
scripts locally:

```bash
echo "GOOGLE_MAPS_API_KEY=YOUR_KEY" > .env
```

The script uses `python-dotenv` to load this file automatically when running.

## Database Options

### SQLite (Default)

For local development and small areas, SQLite is used by default:

```bash
# Optional: specify custom database path
export HEATMAP_DB=metadata.db
```

You can also specify this using the `--db` command-line option.

### PostgreSQL + PostGIS (UK-wide Coverage)

For UK-wide coverage with spatial indexing, use PostgreSQL with PostGIS:

#### Quick Start with Docker

```bash
# Start PostgreSQL with PostGIS
docker-compose up -d postgres

# Set DATABASE_URL
export DATABASE_URL=postgresql://streetview:streetview_dev@localhost:5432/streetview

# Migrate existing data (if any)
python migrate_to_postgres.py --sqlite-path metadata.db

# Optional: Start pgAdmin for database management
docker-compose --profile tools up -d pgadmin
# Access at http://localhost:5050 (admin@local.dev / admin)
```

#### Manual PostgreSQL Setup

1. Install PostgreSQL with PostGIS extension
2. Create database and enable PostGIS:
   ```sql
   CREATE DATABASE streetview;
   \c streetview
   CREATE EXTENSION postgis;
   ```
3. Set the DATABASE_URL environment variable:
   ```bash
   export DATABASE_URL=postgresql://user:password@localhost:5432/streetview
   ```

The database schema is created automatically on first run.

#### Migrating Existing Data

If you have existing data in SQLite, migrate it to PostgreSQL:

```bash
# Dry run first
python migrate_to_postgres.py --sqlite-path metadata.db --dry-run

# Run migration
python migrate_to_postgres.py --sqlite-path metadata.db
```

The migration script:
- Reads all data from SQLite
- Computes tile IDs for each location
- Creates PostGIS spatial indexes
- Sets default priority to "medium" for migrated data

## Geographic Scope

The project includes pre-defined geographic data for UK-wide coverage:

### UK Major Cities

Over 100 UK cities are defined in `geographic_scope.py` with priorities:
- **High**: Major metropolitan areas (London, Birmingham, Manchester, etc.)
- **Medium**: Regional centres (York, Oxford, Brighton, etc.)
- **Low**: Smaller towns

### Road Type Priorities

Roads are classified by OSM highway type:
- **High**: motorway, trunk, primary
- **Medium**: secondary, tertiary
- **Low**: residential, unclassified

### Tile System

The UK is divided into ~0.05° x 0.05° tiles (~5km x 5km) for efficient processing:
- UK bounds: -8.0°W to 2.0°E, 49.9°N to 60.9°N
- Approximately 44,000 tiles cover the UK
- Each tile has a priority based on whether it contains a city

## Usage

`generate_heatmap.py` downloads roads from the Overpass API, queries Street View
metadata for each road and writes `heatmap.html` by default. You can adjust the
bounding box, sampling step, the number of samples per road and the request
concurrency using command-line options. The step value determines the spacing of
the grid of points used to query Street View. It must be a positive number.

```bash
export GOOGLE_MAPS_API_KEY=YOUR_KEY
python generate_heatmap.py \
  --bbox -1.70 53.79 -1.65 53.82 \
  --step 0.005 \
  --samples 5 \
  --concurrency 5 \
  --output heatmap.html \
  --csv results.csv \
  --db metadata.db
```

Open `heatmap.html` in a browser to view the map. Road segments are coloured
from green (recent imagery) to red (older imagery). The bounding box,
sampling step, sample count and concurrency can be edited in the script if you
wish to target different areas or query more detail.

The map includes a small legend that explains what each colour represents, so
you can quickly interpret how recent the imagery is. Hover over a road segment
to see its name and the Street View capture date.

`update_heatmaps.py` processes multiple bounding boxes from a JSON or GeoJSON
file. It repeatedly generates heatmaps for each box and can run at regular
intervals.

```bash
python update_heatmaps.py \
  --bbox-file boxes.json \
  --output-dir output \
  --step 0.005 \
  --interval 86400
```

## Testing

Run all tests:

```bash
pytest
```

Run specific test files:

```bash
# Database tests (SQLite)
pytest tests/test_database.py

# Geographic scope tests
pytest tests/test_geographic_scope.py

# Migration tests
pytest tests/test_migration.py
```

For PostgreSQL integration tests, set DATABASE_URL and run:

```bash
DATABASE_URL=postgresql://... pytest tests/test_migration.py -k "postgres"
```

## Version control

Temporary files such as Python bytecode caches and test artifacts are listed in `.gitignore` so they are not committed to the repository.

