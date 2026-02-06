"""Background job scheduler for automated tile processing.

Uses APScheduler for scheduling periodic update jobs that process
tiles, fetch Street View metadata, and update the database.
"""

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

import database
from geographic_scope import (
    generate_uk_tiles,
    get_tile_priority,
    get_tile_bbox,
)
from app.processing import process_tile

logger = logging.getLogger(__name__)

# Module-level state
_scheduler: Optional[BackgroundScheduler] = None
_current_job: Optional[Dict[str, Any]] = None
_job_lock = threading.Lock()
_last_job_result: Optional[Dict[str, Any]] = None


def get_scheduler() -> Optional[BackgroundScheduler]:
    """Get the current scheduler instance."""
    return _scheduler


def get_current_job() -> Optional[Dict[str, Any]]:
    """Get information about the currently running job."""
    with _job_lock:
        return _current_job.copy() if _current_job else None


def get_last_job_result() -> Optional[Dict[str, Any]]:
    """Get the result of the last completed job."""
    return _last_job_result


def is_job_running() -> bool:
    """Check if an update job is currently running."""
    with _job_lock:
        return _current_job is not None


def _generate_job_id() -> str:
    """Generate a unique job ID based on current timestamp."""
    return f"job_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"


def _create_job_record(
    job_id: str,
    priority_filter: Optional[str] = None,
    tile_limit: Optional[int] = None,
    tiles_total: Optional[int] = None
) -> None:
    """Create a job record in the database."""
    if not database.is_postgresql():
        logger.warning("Job status tracking requires PostgreSQL")
        return

    try:
        with database._conn.cursor() as cur:
            cur.execute("""
                INSERT INTO job_status (
                    job_id, status, started_at, priority_filter,
                    tile_limit, tiles_total
                ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (job_id, 'running', datetime.utcnow(), priority_filter,
                  tile_limit, tiles_total))
        database._conn.commit()
    except Exception as e:
        logger.error("Failed to create job record: %s", e)
        database._conn.rollback()


def _update_job_progress(
    job_id: str,
    tiles_processed: int,
    locations_updated: int,
    api_calls: int
) -> None:
    """Update job progress in the database."""
    if not database.is_postgresql():
        return

    try:
        with database._conn.cursor() as cur:
            cur.execute("""
                UPDATE job_status SET
                    tiles_processed = %s,
                    locations_updated = %s,
                    api_calls = %s
                WHERE job_id = %s
            """, (tiles_processed, locations_updated, api_calls, job_id))
        database._conn.commit()
    except Exception as e:
        logger.error("Failed to update job progress: %s", e)
        database._conn.rollback()


def _complete_job_record(
    job_id: str,
    status: str = 'completed',
    error_message: Optional[str] = None
) -> None:
    """Mark a job as completed in the database."""
    if not database.is_postgresql():
        return

    try:
        with database._conn.cursor() as cur:
            cur.execute("""
                UPDATE job_status SET
                    status = %s,
                    completed_at = %s,
                    error_message = %s
                WHERE job_id = %s
            """, (status, datetime.utcnow(), error_message, job_id))
        database._conn.commit()
    except Exception as e:
        logger.error("Failed to complete job record: %s", e)
        database._conn.rollback()


def get_job_record(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job record from the database."""
    if not database.is_postgresql():
        return None

    try:
        with database._conn.cursor() as cur:
            cur.execute("""
                SELECT id, job_id, status, started_at, completed_at,
                       priority_filter, tile_limit, tiles_processed,
                       tiles_total, locations_updated, api_calls, error_message
                FROM job_status
                WHERE job_id = %s
            """, (job_id,))
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "job_id": row[1],
                    "status": row[2],
                    "started_at": row[3],
                    "completed_at": row[4],
                    "priority_filter": row[5],
                    "tile_limit": row[6],
                    "tiles_processed": row[7],
                    "tiles_total": row[8],
                    "locations_updated": row[9],
                    "api_calls": row[10],
                    "error_message": row[11]
                }
    except Exception as e:
        logger.error("Failed to get job record: %s", e)
    return None


def get_last_completed_job() -> Optional[Dict[str, Any]]:
    """Get the most recently completed job from the database."""
    if not database.is_postgresql():
        return None

    try:
        with database._conn.cursor() as cur:
            cur.execute("""
                SELECT id, job_id, status, started_at, completed_at,
                       priority_filter, tile_limit, tiles_processed,
                       tiles_total, locations_updated, api_calls, error_message
                FROM job_status
                WHERE status = 'completed'
                ORDER BY completed_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "job_id": row[1],
                    "status": row[2],
                    "started_at": row[3],
                    "completed_at": row[4],
                    "priority_filter": row[5],
                    "tile_limit": row[6],
                    "tiles_processed": row[7],
                    "tiles_total": row[8],
                    "locations_updated": row[9],
                    "api_calls": row[10],
                    "error_message": row[11]
                }
    except Exception as e:
        logger.error("Failed to get last completed job: %s", e)
    return None


def get_recent_jobs(limit: int = 10) -> List[Dict[str, Any]]:
    """Get the most recent completed or failed jobs from the database.

    Args:
        limit: Maximum number of jobs to return

    Returns:
        List of job dicts ordered by completed_at descending
    """
    if not database.is_postgresql():
        return []

    try:
        with database._conn.cursor() as cur:
            cur.execute("""
                SELECT id, job_id, status, started_at, completed_at,
                       priority_filter, tile_limit, tiles_processed,
                       tiles_total, locations_updated, api_calls, error_message
                FROM job_status
                WHERE status IN ('completed', 'failed')
                ORDER BY completed_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "job_id": row[1],
                    "status": row[2],
                    "started_at": row[3],
                    "completed_at": row[4],
                    "priority_filter": row[5],
                    "tile_limit": row[6],
                    "tiles_processed": row[7],
                    "tiles_total": row[8],
                    "locations_updated": row[9],
                    "api_calls": row[10],
                    "error_message": row[11]
                }
                for row in rows
            ]
    except Exception as e:
        logger.error("Failed to get recent jobs: %s", e)
    return []


def _get_tiles_to_process(
    priority_filter: Optional[str],
    tile_limit: Optional[int],
    min_age_days: int
) -> List[str]:
    """Determine which tiles to process based on smart refresh strategy.

    Priority order:
    1. High priority tiles never processed (not in metadata)
    2. Medium priority tiles never processed (not in metadata)
    3. High priority tiles with stale data (>3 years old)
    4. Medium priority tiles with stale data (>3 years old)
    5. High priority tiles with data >1 year old
    6. Medium priority tiles with data >1 year old
    7. Low priority tiles never processed
    8. Low priority tiles with stale data (>min_age_days)

    Args:
        priority_filter: Optional filter by priority level
        tile_limit: Maximum number of tiles to return
        min_age_days: Minimum age for rechecking locations

    Returns:
        List of tile IDs to process
    """
    # Get all available tiles from geographic scope
    all_tiles = generate_uk_tiles()

    if priority_filter:
        priorities = [priority_filter]
    else:
        priorities = ["high", "medium", "low"]

    # Filter by priority
    available_tiles = [t for t in all_tiles if t["priority"] in priorities]

    limit = tile_limit or 50  # Default limit

    if not database.is_postgresql():
        # Fallback: just return tiles by priority
        tile_ids = [t["tile_id"] for t in available_tiles]
        return tile_ids[:limit]

    tiles_to_process = []

    try:
        with database._conn.cursor() as cur:
            # Get set of tiles that already exist in metadata
            cur.execute("SELECT DISTINCT tile_id FROM metadata")
            existing_tiles = {row[0] for row in cur.fetchall()}

            # Phase 1: Add tiles that have NEVER been processed (highest priority)
            # These are tiles in geographic scope but not in metadata
            for priority in ["high", "medium", "low"]:
                if priority not in priorities:
                    continue
                if len(tiles_to_process) >= limit:
                    break

                # Find tiles of this priority that don't exist in metadata
                for tile in available_tiles:
                    if tile["priority"] == priority and tile["tile_id"] not in existing_tiles:
                        if tile["tile_id"] not in tiles_to_process:
                            tiles_to_process.append(tile["tile_id"])
                            if len(tiles_to_process) >= limit:
                                break

            # Phase 2: Add tiles with stale data that need refreshing
            if len(tiles_to_process) < limit:
                age_thresholds = [
                    (3 * 365, "high"),    # High priority > 3 years
                    (3 * 365, "medium"),  # Medium priority > 3 years
                    (365, "high"),        # High priority > 1 year
                    (365, "medium"),      # Medium priority > 1 year
                    (min_age_days, "low") # Low priority > min_age
                ]

                for age_days, priority in age_thresholds:
                    if priority not in priorities:
                        continue
                    if len(tiles_to_process) >= limit:
                        break

                    cutoff = datetime.utcnow() - timedelta(days=age_days)

                    # Find tiles with old data
                    cur.execute("""
                        SELECT DISTINCT tile_id
                        FROM metadata
                        WHERE priority = %s
                        AND (last_checked IS NULL OR last_checked < %s)
                        AND tile_id NOT IN %s
                        LIMIT %s
                    """, (priority, cutoff,
                          tuple(tiles_to_process) if tiles_to_process else ('',),
                          limit - len(tiles_to_process)))

                    for row in cur.fetchall():
                        if row[0] and row[0] not in tiles_to_process:
                            tiles_to_process.append(row[0])

    except Exception as e:
        logger.error("Failed to get tiles to process: %s", e)
        # Fallback to basic tile selection (unprocessed tiles first)
        tile_ids = [t["tile_id"] for t in available_tiles]
        tiles_to_process = tile_ids[:limit]

    logger.info("Selected %d tiles for processing", len(tiles_to_process))
    return tiles_to_process


def run_update_job(
    priority_filter: Optional[str] = None,
    tile_limit: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Main update job that processes tiles intelligently.

    This is the core job that:
    1. Creates job record in database
    2. Determines tiles to process based on smart refresh strategy
    3. Processes each tile (fetch roads, query Street View, update DB)
    4. Tracks progress and updates job record
    5. Logs summary statistics

    Args:
        priority_filter: Optional filter by priority level
        tile_limit: Maximum number of tiles to process
        config: Optional configuration overrides

    Returns:
        Job result dictionary
    """
    global _current_job, _last_job_result

    # Check if already running
    if is_job_running():
        logger.warning("Update job already running, skipping")
        return {"status": "skipped", "reason": "Job already running"}

    job_id = _generate_job_id()
    config = config or {}

    # Get config values
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        logger.error("GOOGLE_MAPS_API_KEY not set")
        return {"status": "failed", "reason": "Missing API key"}

    samples_per_road = config.get("samples_per_road", 5)
    concurrency = config.get("concurrency", 20)
    adaptive_sampling = config.get("adaptive_sampling", True)
    overpass_delay = config.get("overpass_delay_seconds", 2)
    min_age_days = config.get("min_age_for_recheck_days", 90)

    # Get tiles to process
    tiles = _get_tiles_to_process(priority_filter, tile_limit, min_age_days)

    if not tiles:
        logger.info("No tiles need processing")
        return {"status": "completed", "reason": "No tiles to process"}

    # Set up current job tracking
    with _job_lock:
        _current_job = {
            "job_id": job_id,
            "status": "running",
            "started_at": datetime.utcnow(),
            "priority_filter": priority_filter,
            "tile_limit": tile_limit,
            "tiles_processed": 0,
            "tiles_total": len(tiles),
            "locations_updated": 0,
            "api_calls": 0,
            "current_tile": None
        }

    # Create job record
    _create_job_record(job_id, priority_filter, tile_limit, len(tiles))

    logger.info("Starting update job %s: %d tiles to process", job_id, len(tiles))

    total_locations = 0
    total_api_calls = 0
    total_roads = 0
    errors = []

    try:
        for i, tile_id in enumerate(tiles):
            # Update current tile
            with _job_lock:
                _current_job["current_tile"] = tile_id

            logger.info("Processing tile %d/%d: %s", i + 1, len(tiles), tile_id)

            try:
                result = process_tile(
                    tile_id=tile_id,
                    api_key=api_key,
                    samples_per_road=samples_per_road,
                    concurrency=concurrency,
                    adaptive_sampling=adaptive_sampling,
                    overpass_delay=overpass_delay if i > 0 else 0  # No delay for first tile
                )

                total_roads += result["roads_found"]
                total_locations += result["locations_updated"]
                total_api_calls += result["api_calls"]

            except Exception as e:
                logger.error("Error processing tile %s: %s", tile_id, e)
                errors.append({"tile_id": tile_id, "error": str(e)})

            # Update progress
            with _job_lock:
                _current_job["tiles_processed"] = i + 1
                _current_job["locations_updated"] = total_locations
                _current_job["api_calls"] = total_api_calls

            # Update database periodically
            if (i + 1) % 5 == 0:
                _update_job_progress(job_id, i + 1, total_locations, total_api_calls)

        # Job completed successfully
        status = "completed"
        error_msg = None
        if errors:
            error_msg = f"{len(errors)} tile(s) failed"

    except Exception as e:
        logger.error("Update job failed: %s", e)
        status = "failed"
        error_msg = str(e)

    finally:
        # Clear current job
        with _job_lock:
            _current_job = None

    # Update job record
    _complete_job_record(job_id, status, error_msg)
    _update_job_progress(job_id, len(tiles), total_locations, total_api_calls)

    result = {
        "job_id": job_id,
        "status": status,
        "tiles_processed": len(tiles),
        "roads_found": total_roads,
        "locations_updated": total_locations,
        "api_calls": total_api_calls,
        "errors": errors if errors else None,
        "duration_seconds": (datetime.utcnow() - _current_job["started_at"]).total_seconds() if _current_job else 0
    }

    _last_job_result = result
    logger.info("Update job %s complete: %s", job_id, result)

    return result


def trigger_update_job(
    priority_filter: Optional[str] = None,
    tile_limit: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str, Optional[str]]:
    """Trigger an update job asynchronously.

    Args:
        priority_filter: Optional filter by priority level
        tile_limit: Maximum tiles to process
        config: Optional configuration overrides

    Returns:
        Tuple of (success, message, job_id)
    """
    if is_job_running():
        return False, "Job already running", None

    job_id = _generate_job_id()

    # Start job in background thread
    def run_job():
        try:
            run_update_job(priority_filter, tile_limit, config)
        except Exception as e:
            logger.error("Background job failed: %s", e)

    thread = threading.Thread(target=run_job, name=f"update-job-{job_id}")
    thread.daemon = True
    thread.start()

    return True, "Update job started", job_id


def _job_listener(event):
    """Handle APScheduler job events."""
    if event.exception:
        logger.error("Scheduled job failed: %s", event.exception)


def init_scheduler(app, config) -> BackgroundScheduler:
    """Initialize the background scheduler.

    Args:
        app: Flask application instance
        config: Configuration object

    Returns:
        Configured BackgroundScheduler
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already initialized")
        return _scheduler

    _scheduler = BackgroundScheduler()

    # Add job listener
    _scheduler.add_listener(_job_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    # Get scheduler config
    scheduler_enabled = config.get("scheduler.enabled", True)
    cron_expr = config.get("scheduler.cron")
    interval_hours = config.get("scheduler.interval_hours", 24)

    if not scheduler_enabled:
        logger.info("Scheduler is disabled in configuration")
        return _scheduler

    # Build update config
    update_config = {
        "samples_per_road": config.get("update.samples_per_road", 5),
        "concurrency": config.get("update.concurrency", 20),
        "adaptive_sampling": config.get("update.adaptive_sampling", True),
        "overpass_delay_seconds": config.get("update.overpass_delay_seconds", 2),
        "min_age_for_recheck_days": config.get("update.min_age_for_recheck_days", 90),
    }
    batch_size = config.get("update.batch_size", 50)

    # Add scheduled job
    if cron_expr:
        trigger = CronTrigger.from_crontab(cron_expr)
        logger.info("Scheduling update job with cron: %s", cron_expr)
    else:
        trigger = IntervalTrigger(hours=interval_hours)
        logger.info("Scheduling update job every %d hours", interval_hours)

    _scheduler.add_job(
        run_update_job,
        trigger=trigger,
        id="scheduled_update",
        name="Scheduled Update",
        kwargs={
            "priority_filter": None,
            "tile_limit": batch_size,
            "config": update_config
        },
        replace_existing=True,
        max_instances=1,  # Prevent overlapping runs
    )

    # Start scheduler (doesn't run jobs immediately)
    _scheduler.start()
    logger.info("Scheduler started")

    return _scheduler


def get_next_run_time() -> Optional[datetime]:
    """Get the next scheduled run time."""
    if _scheduler is None:
        return None

    job = _scheduler.get_job("scheduled_update")
    if job:
        return job.next_run_time
    return None


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    global _scheduler

    if _scheduler is not None:
        logger.info("Shutting down scheduler...")
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("Scheduler shut down")


def reschedule_job(interval_hours: Optional[int] = None, cron_expr: Optional[str] = None):
    """Reschedule the update job with new timing.

    Args:
        interval_hours: New interval in hours (mutually exclusive with cron_expr)
        cron_expr: New cron expression (mutually exclusive with interval_hours)
    """
    if _scheduler is None:
        logger.warning("Scheduler not initialized")
        return

    job = _scheduler.get_job("scheduled_update")
    if not job:
        logger.warning("Scheduled update job not found")
        return

    if cron_expr:
        trigger = CronTrigger.from_crontab(cron_expr)
        logger.info("Rescheduling with cron: %s", cron_expr)
    elif interval_hours:
        trigger = IntervalTrigger(hours=interval_hours)
        logger.info("Rescheduling every %d hours", interval_hours)
    else:
        logger.warning("No new schedule provided")
        return

    job.reschedule(trigger)
