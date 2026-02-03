"""Tests for REST API endpoints."""

import json
import os
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Set test environment before imports
os.environ['GOOGLE_MAPS_API_KEY'] = 'test_key'
os.environ['DATABASE_URL'] = ''  # Use SQLite for tests
os.environ['API_KEY'] = 'test_api_key'


@pytest.fixture
def app():
    """Create test Flask application."""
    # Mock database and config
    with patch('database.init_db'), \
         patch('database.get_cache_stats', return_value={'total_entries': 100, 'entries_with_date': 80}), \
         patch('database.get_coverage_stats', return_value={
             'overall': {'total_entries': 100, 'entries_with_date': 80},
             'by_priority': {
                 'high': {'total_entries': 50, 'entries_with_date': 40, 'tiles_covered': 10},
                 'medium': {'total_entries': 30, 'entries_with_date': 25, 'tiles_covered': 5},
                 'low': {'total_entries': 20, 'entries_with_date': 15, 'tiles_covered': 3}
             },
             'total_tiles_with_data': 18
         }), \
         patch('database.is_postgresql', return_value=False), \
         patch('database.get_backend', return_value='sqlite'), \
         patch('database._conn', MagicMock()), \
         patch('app.scheduler.init_scheduler'):

        # Create minimal config
        config_content = """
app:
  host: 0.0.0.0
  port: 5000
  api_key: test_api_key
  debug: true

database:
  url: ""

google:
  api_key: test_key

scheduler:
  enabled: false

update:
  batch_size: 10
  concurrency: 5
  min_age_for_recheck_days: 90
  overpass_delay_seconds: 1
  samples_per_road: 3
  adaptive_sampling: true

logging:
  level: DEBUG
  format: "%(message)s"
  file: logs/test.log
"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            from app import create_app
            app = create_app(config_path)
            app.config['TESTING'] = True
            yield app
        finally:
            os.unlink(config_path)


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestHealthEndpoint:
    """Tests for /api/health endpoint."""

    def test_health_returns_200(self, client):
        """Health check returns 200 when healthy."""
        response = client.get('/api/health')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'healthy'
        assert data['database'] == 'connected'
        assert 'timestamp' in data

    def test_health_includes_timestamp(self, client):
        """Health check includes ISO timestamp."""
        response = client.get('/api/health')
        data = response.get_json()
        # Should be valid ISO format
        datetime.fromisoformat(data['timestamp'].rstrip('Z'))


class TestStatusEndpoint:
    """Tests for /api/status endpoint."""

    def test_status_returns_200(self, client):
        """Status endpoint returns 200."""
        with patch('app.routes.get_current_job', return_value=None), \
             patch('app.routes.get_last_completed_job', return_value=None), \
             patch('app.routes.get_next_run_time', return_value=None):
            response = client.get('/api/status')
            assert response.status_code == 200

    def test_status_includes_coverage(self, client):
        """Status includes coverage statistics."""
        with patch('app.routes.get_current_job', return_value=None), \
             patch('app.routes.get_last_completed_job', return_value=None), \
             patch('app.routes.get_next_run_time', return_value=None):
            response = client.get('/api/status')
            data = response.get_json()
            assert 'coverage' in data
            assert 'high' in data['coverage']
            assert 'medium' in data['coverage']
            assert 'low' in data['coverage']

    def test_status_shows_running_job(self, client):
        """Status shows currently running job."""
        mock_job = {
            'job_id': 'job_123',
            'started_at': datetime.utcnow(),
            'priority_filter': 'high',
            'tiles_processed': 5,
            'tiles_total': 10,
            'locations_updated': 100
        }
        with patch('app.routes.get_current_job', return_value=mock_job), \
             patch('app.routes.get_last_completed_job', return_value=None), \
             patch('app.routes.get_next_run_time', return_value=None):
            response = client.get('/api/status')
            data = response.get_json()
            assert data['current_job']['running'] is True
            assert data['current_job']['job_id'] == 'job_123'


class TestTilesEndpoint:
    """Tests for /api/tiles endpoint."""

    def test_list_tiles_returns_200(self, client):
        """Tiles listing returns 200."""
        response = client.get('/api/tiles')
        assert response.status_code == 200

    def test_list_tiles_pagination(self, client):
        """Tiles listing supports pagination."""
        response = client.get('/api/tiles?page=1&per_page=10')
        data = response.get_json()
        assert 'tiles' in data
        assert 'total' in data
        assert 'page' in data
        assert 'per_page' in data
        assert 'pages' in data

    def test_list_tiles_priority_filter(self, client):
        """Tiles can be filtered by priority."""
        response = client.get('/api/tiles?priority=high')
        data = response.get_json()
        for tile in data['tiles']:
            assert tile['priority'] == 'high'

    def test_tile_data_invalid_id(self, client):
        """Invalid tile ID returns 400."""
        response = client.get('/api/tiles/invalid_id/data')
        assert response.status_code == 400


class TestTriggerUpdateEndpoint:
    """Tests for /api/update/trigger endpoint."""

    def test_trigger_requires_api_key(self, client):
        """Trigger endpoint requires API key."""
        response = client.post('/api/update/trigger')
        assert response.status_code == 401

    def test_trigger_with_valid_api_key(self, client):
        """Trigger works with valid API key."""
        with patch('app.routes.is_job_running', return_value=False), \
             patch('app.routes.trigger_update_job', return_value=(True, 'Started', 'job_123')):
            response = client.post(
                '/api/update/trigger',
                headers={'X-API-Key': 'test_api_key'},
                json={}
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data['status'] == 'started'

    def test_trigger_returns_409_when_running(self, client):
        """Trigger returns 409 when job already running."""
        with patch('app.routes.is_job_running', return_value=True):
            response = client.post(
                '/api/update/trigger',
                headers={'X-API-Key': 'test_api_key'},
                json={}
            )
            assert response.status_code == 409

    def test_trigger_accepts_parameters(self, client):
        """Trigger accepts priority and tile_limit."""
        with patch('app.routes.is_job_running', return_value=False), \
             patch('app.routes.trigger_update_job', return_value=(True, 'Started', 'job_123')) as mock_trigger:
            response = client.post(
                '/api/update/trigger',
                headers={'X-API-Key': 'test_api_key'},
                json={'priority': 'high', 'tile_limit': 20}
            )
            assert response.status_code == 200
            mock_trigger.assert_called_once()
            args = mock_trigger.call_args
            assert args.kwargs['priority_filter'] == 'high'
            assert args.kwargs['tile_limit'] == 20


class TestConfigEndpoint:
    """Tests for /api/config endpoints."""

    def test_get_config_returns_200(self, client):
        """Get config returns 200."""
        with patch('app.routes.get_next_run_time', return_value=None):
            response = client.get('/api/config')
            assert response.status_code == 200
            data = response.get_json()
            assert 'scheduler' in data
            assert 'update' in data

    def test_update_config_requires_api_key(self, client):
        """Update config requires API key."""
        response = client.post('/api/config', json={})
        assert response.status_code == 401

    def test_update_config_validates_input(self, client):
        """Update config validates input."""
        with patch('app.routes.get_next_run_time', return_value=None):
            response = client.post(
                '/api/config',
                headers={'X-API-Key': 'test_api_key'},
                json={'update': {'invalid_key': 123}}
            )
            assert response.status_code == 400


class TestCitiesEndpoint:
    """Tests for /api/cities endpoint."""

    def test_list_cities_returns_200(self, client):
        """Cities listing returns 200."""
        response = client.get('/api/cities')
        assert response.status_code == 200
        data = response.get_json()
        assert 'cities' in data
        assert 'total' in data

    def test_cities_have_required_fields(self, client):
        """Each city has required fields."""
        response = client.get('/api/cities')
        data = response.get_json()
        if data['cities']:
            city = data['cities'][0]
            assert 'name' in city
            assert 'key' in city
            assert 'bbox' in city
            assert 'priority' in city

    def test_cities_filter_by_priority(self, client):
        """Cities can be filtered by priority."""
        response = client.get('/api/cities?priority=high')
        data = response.get_json()
        for city in data['cities']:
            assert city['priority'] == 'high'


class TestUpdateStatusEndpoint:
    """Tests for /api/update/status endpoint."""

    def test_update_status_when_not_running(self, client):
        """Update status when no job running."""
        with patch('app.routes.get_current_job', return_value=None), \
             patch('app.routes.get_last_completed_job', return_value=None):
            response = client.get('/api/update/status')
            assert response.status_code == 200
            data = response.get_json()
            assert data['running'] is False

    def test_update_status_when_running(self, client):
        """Update status shows progress when running."""
        mock_job = {
            'job_id': 'job_456',
            'started_at': datetime.utcnow(),
            'priority_filter': 'medium',
            'tiles_processed': 3,
            'tiles_total': 10,
            'locations_updated': 50,
            'api_calls': 150,
            'current_tile': 'tile_123_456'
        }
        with patch('app.routes.get_current_job', return_value=mock_job):
            response = client.get('/api/update/status')
            data = response.get_json()
            assert data['running'] is True
            assert data['percent_complete'] == 30.0
            assert data['current_tile'] == 'tile_123_456'


class TestErrorHandling:
    """Tests for error handling."""

    def test_404_for_unknown_endpoint(self, client):
        """Unknown endpoint returns 404."""
        response = client.get('/api/unknown')
        assert response.status_code == 404

    def test_405_for_wrong_method(self, client):
        """Wrong HTTP method returns 405."""
        response = client.delete('/api/health')
        assert response.status_code == 405
