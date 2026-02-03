#!/usr/bin/env python3
"""Run the Street View Heatmap web application.

This is the main entry point for running the Flask application.
It loads environment variables, creates the app, and starts the server.

Usage:
    python run.py                    # Run with default config
    python run.py --config prod.yaml # Run with custom config
    python run.py --debug            # Run in debug mode
"""

import argparse
import os
import sys

from dotenv import load_dotenv


def main():
    """Application entry point."""
    parser = argparse.ArgumentParser(
        description="Run the Street View Heatmap web application"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode"
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host to bind to (overrides config)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind to (overrides config)"
    )
    args = parser.parse_args()

    # Load environment variables from .env file
    load_dotenv()

    # Handle GitHub Codespaces secret mapping
    if os.getenv("GMAPS_APIKEY"):
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", os.getenv("GMAPS_APIKEY"))

    # Import and create app after loading env vars
    from app import create_app

    try:
        app = create_app(args.config)
    except Exception as e:
        print(f"Failed to create application: {e}", file=sys.stderr)
        sys.exit(1)

    # Get configuration values (CLI args override config)
    config = app.config
    host = args.host or config.get('app', {}).get('host', '0.0.0.0')
    port = args.port or config.get('app', {}).get('port', 5000)
    debug = args.debug or config.get('app', {}).get('debug', False)

    print(f"\n{'='*60}")
    print("Street View Heatmap API Server")
    print(f"{'='*60}")
    print(f"  Host:     {host}")
    print(f"  Port:     {port}")
    print(f"  Debug:    {debug}")
    print(f"  Config:   {args.config}")
    print(f"{'='*60}")
    print(f"\nAPI endpoints available at: http://{host}:{port}/api/")
    print("  GET  /api/health     - Health check")
    print("  GET  /api/status     - System status")
    print("  GET  /api/tiles      - List tiles")
    print("  POST /api/update/trigger - Trigger update job")
    print(f"{'='*60}\n")

    # Run the application
    app.run(
        host=host,
        port=port,
        debug=debug,
        use_reloader=debug,  # Auto-reload on code changes in debug mode
        threaded=True  # Handle multiple requests concurrently
    )


if __name__ == '__main__':
    main()
