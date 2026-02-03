"""Web page routes for the Street View Heatmap application.

Provides routes for the web frontend including:
- Map view (index)
- Dashboard
- Configuration
- Instructions
"""

from flask import Blueprint, render_template

# Create Blueprint for page routes
pages_bp = Blueprint('pages', __name__)


@pages_bp.route('/')
def index():
    """Main map view.

    Displays an interactive Leaflet map with Street View
    coverage data overlaid on OpenStreetMap tiles.
    """
    return render_template('index.html')


@pages_bp.route('/dashboard')
def dashboard():
    """Status dashboard.

    Shows system status, coverage statistics, current job
    progress, and recent activity.
    """
    return render_template('dashboard.html')


@pages_bp.route('/config')
def config_page():
    """Configuration panel.

    Allows authenticated users to modify scheduler and
    update settings.
    """
    return render_template('config.html')


@pages_bp.route('/instructions')
def instructions():
    """Help and instructions page.

    Provides documentation on how to use the application,
    understand the map, and access the API.
    """
    return render_template('instructions.html')
