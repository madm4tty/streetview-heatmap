"""REST API endpoints for the Street View Heatmap application.

Provides endpoints for:
- Health checks and system status
- Tile data retrieval
- Update job triggering
- Configuration management
"""

import logging
from datetime import datetime
from functools import wraps
from typing import Any, Dict, Optional

from flask import Blueprint, current_app, jsonify, request

import database
from geographic_scope import (
    generate_uk_tiles,
    get_tile_bbox,
    get_tile_priority,
    UK_MAJOR_CITIES,
)
from app.models import (
    TriggerUpdateRequest,
    ConfigUpdateRequest,
    Priority,
)
from app.scheduler import (
    get_current_job,
    get_last_completed_job,
    get_next_run_time,
    is_job_running,
    trigger_update_job,
    reschedule_job,
)
from app.processing import get_tile_geojson, get_tile_road_geojson

logger = logging.getLogger(__name__)

# Create Blueprint
api_bp = Blueprint('api', __name__)


def require_api_key(f):
    """Decorator to require API key for write operations."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        expected_key = current_app.config.get('app', {}).get('api_key', 'changeme')

        if not api_key or api_key != expected_key:
            return jsonify({
                "error": "Unauthorized",
                "message": "Valid X-API-Key header required"
            }), 401

        return f(*args, **kwargs)
    return decorated_function


def validate_request(model_class):
    """Decorator to validate request body with Pydantic model."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                data = request.get_json(silent=True) or {}
                validated = model_class(**data)
                kwargs['validated_data'] = validated
            except Exception as e:
                return jsonify({
                    "error": "Validation Error",
                    "message": str(e)
                }), 400
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ============================================================================
# Health & Status Endpoints
# ============================================================================

@api_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint.

    Returns:
        200: {"status": "healthy", "database": "connected", "timestamp": "..."}
        503: {"status": "unhealthy", "database": "disconnected", ...}
    """
    try:
        # Check database connection
        stats = database.get_cache_stats()
        db_status = "connected"
    except Exception as e:
        logger.error("Database health check failed: %s", e)
        db_status = "disconnected"

    status = "healthy" if db_status == "connected" else "unhealthy"
    response = {
        "status": status,
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    return jsonify(response), 200 if status == "healthy" else 503


@api_bp.route('/status', methods=['GET'])
def get_status():
    """System status and statistics.

    Returns comprehensive status including coverage stats,
    current job info, and database statistics.
    """
    try:
        # Get coverage statistics
        coverage_stats = database.get_coverage_stats()

        # Calculate coverage by priority
        coverage = {}
        all_tiles = generate_uk_tiles()
        tiles_by_priority = {}
        for t in all_tiles:
            tiles_by_priority.setdefault(t['priority'], []).append(t)

        for priority in ['high', 'medium', 'low']:
            priority_data = coverage_stats.get('by_priority', {}).get(priority, {})
            total_entries = priority_data.get('total_entries', 0)
            with_date = priority_data.get('entries_with_date', 0)
            tiles = priority_data.get('tiles_covered', 0)

            total_tiles = len(tiles_by_priority.get(priority, []))

            coverage[priority] = {
                "total": total_tiles,
                "with_data": tiles,
                "total_tiles": total_tiles,
                "tiles_with_data": tiles,
                "locations_total": total_entries,
                "locations_checked": with_date,
                "percent_complete": round((tiles / total_tiles * 100), 1) if total_tiles > 0 else 0
            }

        # Get current job info
        current_job = get_current_job()
        if current_job:
            job_info = {
                "status": "running",
                "running": True,
                "job_id": current_job.get("job_id"),
                "started_at": current_job.get("started_at").isoformat() + "Z" if current_job.get("started_at") else None,
                "priority_filter": current_job.get("priority_filter"),
                "tiles_processed": current_job.get("tiles_processed", 0),
                "tiles_total": current_job.get("tiles_total"),
                "locations_updated": current_job.get("locations_updated", 0)
            }
        else:
            job_info = {"status": "idle", "running": False}

        # Get last update and next scheduled update
        last_job = get_last_completed_job()
        last_update = last_job.get("completed_at").isoformat() + "Z" if last_job and last_job.get("completed_at") else None

        # Format last job info for frontend
        last_job_info = None
        if last_job:
            last_job_info = {
                "job_id": last_job.get("job_id"),
                "status": last_job.get("status"),
                "started_at": last_job.get("started_at").isoformat() + "Z" if last_job.get("started_at") else None,
                "completed_at": last_job.get("completed_at").isoformat() + "Z" if last_job.get("completed_at") else None,
                "priority_filter": last_job.get("priority_filter"),
                "tiles_processed": last_job.get("tiles_processed", 0),
                "locations_updated": last_job.get("locations_updated", 0),
                "api_calls": last_job.get("api_calls", 0)
            }

        next_run = get_next_run_time()
        next_update = next_run.isoformat() + "Z" if next_run else None

        # Get database stats
        db_stats = database.get_cache_stats()
        unique_tiles = coverage_stats.get('total_tiles_with_data', 0)

        total_entries = db_stats.get("total_entries", 0)
        with_dates = db_stats.get("entries_with_date", 0)

        response = {
            "status": "running",
            "last_update": last_update,
            "next_update": next_update,
            "coverage": coverage,
            "current_job": job_info,
            "last_job": last_job_info,
            "total_entries": total_entries,
            "with_dates": with_dates,
            "tiles_covered": unique_tiles,
            "database": {
                "total_entries": total_entries,
                "entries_with_dates": with_dates,
                "unique_tiles": unique_tiles
            }
        }

        return jsonify(response)

    except Exception as e:
        logger.error("Failed to get status: %s", e)
        return jsonify({
            "error": "Internal Error",
            "message": str(e)
        }), 500


# ============================================================================
# Tile Endpoints
# ============================================================================

@api_bp.route('/tiles', methods=['GET'])
def list_tiles():
    """List tiles with metadata.

    Query params:
        priority: Filter by priority level (high/medium/low)
        has_data: Filter by whether tile has data (true/false)
        page: Page number (default: 1)
        per_page: Items per page (default: 100, max: 500)

    Returns:
        Paginated list of tiles with metadata
    """
    try:
        # Parse query parameters
        priority = request.args.get('priority')
        has_data_str = request.args.get('has_data')
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 100)), 500)

        has_data = None
        if has_data_str is not None:
            has_data = has_data_str.lower() == 'true'

        # Generate all UK tiles
        all_tiles = generate_uk_tiles()

        # Apply priority filter
        if priority:
            all_tiles = [t for t in all_tiles if t['priority'] == priority]

        # Get tile data from database
        tile_data = {}
        if database.is_postgresql():
            try:
                with database._conn.cursor() as cur:
                    cur.execute("""
                        SELECT tile_id, COUNT(*), MAX(last_checked),
                               COUNT(CASE WHEN date IS NOT NULL AND date != '' THEN 1 END)
                        FROM metadata
                        WHERE tile_id IS NOT NULL
                        GROUP BY tile_id
                    """)
                    for row in cur.fetchall():
                        tile_data[row[0]] = {
                            "location_count": row[1],
                            "last_updated": row[2],
                            "with_dates": row[3]
                        }
            except Exception as e:
                logger.warning("Failed to get tile data: %s", e)

        # Build tile list with data
        tiles_list = []
        for tile in all_tiles:
            tile_id = tile['tile_id']
            data = tile_data.get(tile_id, {})
            tile_has_data = tile_id in tile_data

            # Apply has_data filter
            if has_data is not None and tile_has_data != has_data:
                continue

            bbox = list(tile['bbox'])
            tiles_list.append({
                "tile_id": tile_id,
                "bbox": bbox,
                "lat": bbox[1],
                "lon": bbox[0],
                "priority": tile['priority'],
                "has_data": tile_has_data,
                "location_count": data.get("location_count", 0),
                "with_dates": data.get("with_dates", 0),
                "last_updated": data.get("last_updated").isoformat() + "Z" if data.get("last_updated") else None
            })

        # Paginate
        total = len(tiles_list)
        pages = (total + per_page - 1) // per_page
        start = (page - 1) * per_page
        end = start + per_page

        return jsonify({
            "tiles": tiles_list[start:end],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages
        })

    except Exception as e:
        logger.error("Failed to list tiles: %s", e)
        return jsonify({
            "error": "Internal Error",
            "message": str(e)
        }), 500


@api_bp.route('/tiles/<tile_id>/data', methods=['GET'])
def get_tile_data(tile_id: str):
    """Get GeoJSON data for a specific tile.

    Path params:
        tile_id: Tile identifier (e.g., "tile_123_456")

    Query params:
        format: "points" or "roads" (default: "points")

    Returns:
        GeoJSON FeatureCollection with road/point data
    """
    try:
        # Validate tile_id format
        if not tile_id.startswith('tile_'):
            return jsonify({
                "error": "Invalid tile_id",
                "message": "Tile ID must be in format 'tile_X_Y'"
            }), 400

        format_type = request.args.get('format', 'points')

        if format_type == 'roads':
            # Get road GeoJSON (requires API key for fetching)
            api_key = current_app.config.get('google', {}).get('api_key')
            if not api_key:
                return jsonify({
                    "error": "Configuration Error",
                    "message": "Google API key not configured"
                }), 500

            update_config = current_app.config.get('update', {})
            geojson = get_tile_road_geojson(
                tile_id,
                api_key,
                samples_per_road=update_config.get('samples_per_road', 5),
                concurrency=update_config.get('concurrency', 20),
                adaptive_sampling=update_config.get('adaptive_sampling', True)
            )
        else:
            # Get point GeoJSON from cached data
            geojson = get_tile_geojson(tile_id)

        return jsonify(geojson)

    except Exception as e:
        logger.error("Failed to get tile data for %s: %s", tile_id, e)
        return jsonify({
            "error": "Internal Error",
            "message": str(e)
        }), 500


# ============================================================================
# Update Job Endpoints
# ============================================================================

@api_bp.route('/update/trigger', methods=['POST'])
@require_api_key
@validate_request(TriggerUpdateRequest)
def trigger_update(validated_data: TriggerUpdateRequest):
    """Manually trigger an update job.

    Request body (optional):
        priority: Filter by priority level (high/medium/low)
        tile_limit: Maximum tiles to process (1-1000)

    Returns:
        200: {"status": "started", "job_id": "...", "message": "..."}
        409: {"error": "Conflict", "message": "Job already running"}
    """
    if is_job_running():
        return jsonify({
            "error": "Conflict",
            "message": "Update job already running"
        }), 409

    # Get config for update job
    update_config = current_app.config.get('update', {})

    priority_filter = validated_data.priority.value if validated_data.priority else None
    tile_limit = validated_data.tile_limit or update_config.get('batch_size', 50)

    success, message, job_id = trigger_update_job(
        priority_filter=priority_filter,
        tile_limit=tile_limit,
        config=update_config
    )

    if success:
        return jsonify({
            "status": "started",
            "job_id": job_id,
            "message": message
        })
    else:
        return jsonify({
            "error": "Failed",
            "message": message
        }), 500


@api_bp.route('/update/status', methods=['GET'])
def get_update_status():
    """Get current update job status.

    Returns current job progress if running, or last completed job info.
    """
    current_job = get_current_job()

    if current_job:
        tiles_total = current_job.get("tiles_total", 1)
        tiles_processed = current_job.get("tiles_processed", 0)
        percent = round((tiles_processed / tiles_total * 100), 1) if tiles_total > 0 else 0

        return jsonify({
            "running": True,
            "job_id": current_job.get("job_id"),
            "started_at": current_job.get("started_at").isoformat() + "Z" if current_job.get("started_at") else None,
            "priority_filter": current_job.get("priority_filter"),
            "tiles_processed": tiles_processed,
            "tiles_total": tiles_total,
            "locations_updated": current_job.get("locations_updated", 0),
            "api_calls": current_job.get("api_calls", 0),
            "percent_complete": percent,
            "current_tile": current_job.get("current_tile")
        })

    # Return last completed job info
    last_job = get_last_completed_job()
    if last_job:
        return jsonify({
            "running": False,
            "last_job": {
                "job_id": last_job.get("job_id"),
                "status": last_job.get("status"),
                "completed_at": last_job.get("completed_at").isoformat() + "Z" if last_job.get("completed_at") else None,
                "tiles_processed": last_job.get("tiles_processed", 0),
                "locations_updated": last_job.get("locations_updated", 0),
                "api_calls": last_job.get("api_calls", 0)
            }
        })

    return jsonify({"running": False, "last_job": None})


# ============================================================================
# Configuration Endpoints
# ============================================================================

@api_bp.route('/config', methods=['GET'])
def get_config():
    """Get current configuration.

    Returns scheduler and update configuration.
    Sensitive values are masked.
    """
    scheduler_config = {
        "enabled": current_app.config.get('scheduler', {}).get('enabled', True),
        "interval_hours": current_app.config.get('scheduler', {}).get('interval_hours', 24),
        "next_run": get_next_run_time().isoformat() + "Z" if get_next_run_time() else None
    }

    update_config = {
        "batch_size": current_app.config.get('update', {}).get('batch_size', 50),
        "concurrency": current_app.config.get('update', {}).get('concurrency', 20),
        "min_age_for_recheck_days": current_app.config.get('update', {}).get('min_age_for_recheck_days', 90),
        "overpass_delay_seconds": current_app.config.get('update', {}).get('overpass_delay_seconds', 2),
        "samples_per_road": current_app.config.get('update', {}).get('samples_per_road', 5),
        "adaptive_sampling": current_app.config.get('update', {}).get('adaptive_sampling', True)
    }

    return jsonify({
        "scheduler": scheduler_config,
        "update": update_config
    })


@api_bp.route('/config', methods=['POST'])
@require_api_key
@validate_request(ConfigUpdateRequest)
def update_config(validated_data: ConfigUpdateRequest):
    """Update configuration.

    Request body:
        scheduler: Scheduler configuration updates
        update: Update job configuration

    Note: Changes are applied immediately but may require restart
    for some settings to take effect.
    """
    try:
        # Update scheduler config
        if validated_data.scheduler:
            for key, value in validated_data.scheduler.items():
                current_app.config.setdefault('scheduler', {})[key] = value

            # Reschedule if timing changed
            if 'interval_hours' in validated_data.scheduler:
                reschedule_job(interval_hours=validated_data.scheduler['interval_hours'])
            elif 'cron' in validated_data.scheduler:
                reschedule_job(cron_expr=validated_data.scheduler['cron'])

        # Update update config
        if validated_data.update:
            for key, value in validated_data.update.items():
                current_app.config.setdefault('update', {})[key] = value

        return get_config()

    except Exception as e:
        logger.error("Failed to update config: %s", e)
        return jsonify({
            "error": "Internal Error",
            "message": str(e)
        }), 500


# ============================================================================
# Geographic Data Endpoints
# ============================================================================

@api_bp.route('/cities', methods=['GET'])
def list_cities():
    """List all UK cities with their bounding boxes, center coordinates, and priorities.

    Query params:
        priority: Filter by priority level

    Returns:
        List of cities with name, key, bbox, lat, lon, and priority
    """
    priority = request.args.get('priority')

    cities = []
    for name, data in UK_MAJOR_CITIES.items():
        if priority and data['priority'] != priority:
            continue

        bbox = data['bbox']  # (min_lon, min_lat, max_lon, max_lat)
        # Calculate center coordinates for search functionality
        center_lat = (bbox[1] + bbox[3]) / 2
        center_lon = (bbox[0] + bbox[2]) / 2

        cities.append({
            "name": name.replace('_', ' ').title(),
            "key": name,
            "bbox": list(bbox),
            "lat": center_lat,
            "lon": center_lon,
            "priority": data['priority']
        })

    # Sort by priority then name
    priority_order = {'high': 0, 'medium': 1, 'low': 2}
    cities.sort(key=lambda c: (priority_order.get(c['priority'], 3), c['name']))

    return jsonify({
        "cities": cities,
        "total": len(cities)
    })


# ============================================================================
# Error Handlers
# ============================================================================

@api_bp.errorhandler(400)
def bad_request(e):
    return jsonify({
        "error": "Bad Request",
        "message": str(e)
    }), 400


@api_bp.errorhandler(404)
def not_found(e):
    return jsonify({
        "error": "Not Found",
        "message": "The requested resource was not found"
    }), 404


@api_bp.errorhandler(500)
def internal_error(e):
    return jsonify({
        "error": "Internal Server Error",
        "message": "An unexpected error occurred"
    }), 500
