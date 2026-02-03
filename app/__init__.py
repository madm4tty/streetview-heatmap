"""Flask application factory for Street View Heatmap.

This module provides the application factory pattern for creating
Flask app instances with proper configuration, logging, and extensions.
"""

import atexit
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from flask import Flask
from flask_cors import CORS

import database
from config import Config, ConfigError


def setup_logging(config: Config) -> None:
    """Configure application logging.

    Sets up console and file handlers based on configuration.

    Args:
        config: Configuration object
    """
    log_level = config.get('logging.level', 'INFO')
    log_format = config.get(
        'logging.format',
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    log_file = config.get('logging.file', 'logs/heatmap_app.log')
    max_bytes = config.get('logging.max_bytes', 10485760)  # 10MB
    backup_count = config.get('logging.backup_count', 5)

    # Create logs directory if needed
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(console_handler)

    # File handler with rotation
    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(file_handler)
    except Exception as e:
        root_logger.warning("Failed to create file handler: %s", e)

    # Reduce noise from third-party loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.INFO)


def create_app(config_path: str = "config.yaml") -> Flask:
    """Flask application factory.

    Creates and configures a Flask application instance with:
    - Configuration loading
    - CORS support
    - Database initialization
    - Scheduler setup
    - Proper logging
    - Web frontend with templates

    Args:
        config_path: Path to YAML configuration file

    Returns:
        Configured Flask application
    """
    app = Flask(
        __name__,
        template_folder='templates',
        static_folder='static'
    )
    logger = logging.getLogger(__name__)

    # Load configuration
    try:
        config = Config(config_path)
        # Store config data with both formats for compatibility
        # Flask requires uppercase keys, but we want to preserve our structure
        for key, value in config.data.items():
            app.config[key] = value  # lowercase for our use
            app.config[key.upper()] = value  # uppercase for Flask conventions
        app.config['_config_object'] = config  # Store for later access
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        raise

    # Setup logging first
    setup_logging(config)
    logger.info("Starting Street View Heatmap application")

    # Validate configuration (warn but don't fail for optional values)
    try:
        config.validate()
    except ConfigError as e:
        logger.warning("Configuration validation warning: %s", e)

    # Setup CORS
    CORS(app, resources={
        r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Content-Type", "X-API-Key"]
        }
    })

    # Initialize database
    try:
        database.init_db()
        logger.info("Database initialized (%s backend)", database.get_backend())
    except Exception as e:
        logger.error("Failed to initialize database: %s", e)
        raise

    # Create job_status table if using PostgreSQL
    if database.is_postgresql():
        _ensure_job_status_table()

    # Register blueprints
    from app.routes import api_bp
    from app.pages import pages_bp

    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(pages_bp)  # Pages at root level
    logger.info("Registered API blueprint at /api")
    logger.info("Registered pages blueprint for web frontend")

    # Initialize scheduler if enabled
    scheduler_enabled = config.get('scheduler.enabled', True)
    if scheduler_enabled:
        from app.scheduler import init_scheduler, shutdown_scheduler

        with app.app_context():
            scheduler = init_scheduler(app, config)
            logger.info("Background scheduler initialized")

        # Register cleanup on shutdown
        def cleanup():
            logger.info("Shutting down...")
            shutdown_scheduler()
            database.close_db()

        atexit.register(cleanup)

        # Handle SIGTERM gracefully
        def signal_handler(signum, frame):
            logger.info("Received signal %s, shutting down...", signum)
            cleanup()
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    else:
        logger.info("Scheduler disabled in configuration")

    logger.info("Application ready")
    return app


def _ensure_job_status_table() -> None:
    """Create job_status table if it doesn't exist."""
    logger = logging.getLogger(__name__)

    try:
        with database._conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS job_status (
                    id SERIAL PRIMARY KEY,
                    job_id VARCHAR(50) UNIQUE NOT NULL,
                    status VARCHAR(20) NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    priority_filter VARCHAR(10),
                    tile_limit INTEGER,
                    tiles_processed INTEGER DEFAULT 0,
                    tiles_total INTEGER,
                    locations_updated INTEGER DEFAULT 0,
                    api_calls INTEGER DEFAULT 0,
                    error_message TEXT
                )
            """)

            # Create indexes
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_job_status_job_id ON job_status(job_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_job_status_status ON job_status(status)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_job_status_started_at ON job_status(started_at)
            """)

        database._conn.commit()
        logger.info("Job status table ready")

    except Exception as e:
        logger.error("Failed to create job_status table: %s", e)
        database._conn.rollback()
        raise
