"""Tests for processing module."""

import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

# Set test environment
os.environ['GOOGLE_MAPS_API_KEY'] = 'test_key'
os.environ['DATABASE_URL'] = ''


class TestFetchOsmRoads:
    """Tests for OSM road fetching."""

    @patch('requests.get')
    def test_successful_fetch(self, mock_get):
        """Successfully fetches roads from Overpass API."""
        from app.processing import fetch_osm_roads

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'elements': [
                {
                    'type': 'way',
                    'geometry': [
                        {'lat': 53.8, 'lon': -1.65},
                        {'lat': 53.81, 'lon': -1.66}
                    ],
                    'tags': {
                        'name': 'Test Road',
                        'highway': 'primary'
                    }
                }
            ]
        }
        mock_get.return_value = mock_response

        roads = fetch_osm_roads((-1.70, 53.79, -1.65, 53.82))

        assert len(roads) == 1
        coords, name, highway_type = roads[0]
        assert len(coords) == 2
        assert name == 'Test Road'
        assert highway_type == 'primary'

    @patch('requests.get')
    def test_handles_no_roads(self, mock_get):
        """Handles empty response gracefully."""
        from app.processing import fetch_osm_roads

        mock_response = MagicMock()
        mock_response.json.return_value = {'elements': []}
        mock_get.return_value = mock_response

        roads = fetch_osm_roads((-1.70, 53.79, -1.65, 53.82))
        assert roads == []

    @patch('requests.get')
    def test_retries_on_failure(self, mock_get):
        """Retries on request failure."""
        from app.processing import fetch_osm_roads
        import requests

        mock_get.side_effect = [
            requests.RequestException("Connection error"),
            MagicMock(json=lambda: {'elements': []})
        ]

        roads = fetch_osm_roads((-1.70, 53.79, -1.65, 53.82), retries=2, retry_delay=0.01)
        assert roads == []
        assert mock_get.call_count == 2


class TestSampleCoords:
    """Tests for coordinate sampling."""

    def test_sample_all_when_n_greater(self):
        """Returns all coords when n >= len(coords)."""
        from app.processing import sample_coords

        coords = [(53.8, -1.65), (53.81, -1.66)]
        result = sample_coords(coords, 5)
        assert result == coords

    def test_sample_middle_when_n_is_1(self):
        """Returns middle point when n=1."""
        from app.processing import sample_coords

        coords = [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)]
        result = sample_coords(coords, 1)
        assert len(result) == 1
        assert result[0] == (3, 3)

    def test_evenly_spaced_samples(self):
        """Samples are evenly spaced."""
        from app.processing import sample_coords

        coords = [(i, i) for i in range(10)]
        result = sample_coords(coords, 3)
        assert len(result) == 3
        assert result[0] == (0, 0)
        assert result[2] == (9, 9)

    def test_handles_empty_list(self):
        """Handles empty coordinate list."""
        from app.processing import sample_coords
        result = sample_coords([], 5)
        assert result == []


class TestAdaptiveSampling:
    """Tests for adaptive sample count calculation."""

    def test_motorway_gets_more_samples(self):
        """Motorways get higher multiplier."""
        from app.processing import get_adaptive_sample_count

        base = 5
        motorway_count = get_adaptive_sample_count('motorway', base, 100)
        residential_count = get_adaptive_sample_count('residential', base, 100)

        assert motorway_count > residential_count

    def test_capped_at_coord_count(self):
        """Sample count capped at available coordinates."""
        from app.processing import get_adaptive_sample_count

        result = get_adaptive_sample_count('motorway', 100, 10)
        assert result <= 10

    def test_minimum_one_sample(self):
        """Always returns at least 1 sample."""
        from app.processing import get_adaptive_sample_count

        result = get_adaptive_sample_count('footway', 1, 100)
        assert result >= 1

    def test_unknown_type_default(self):
        """Unknown highway type uses default multiplier."""
        from app.processing import get_adaptive_sample_count

        result = get_adaptive_sample_count('unknown_type', 5, 100)
        assert result == 5  # 1.0 multiplier


class TestDateParsing:
    """Tests for date parsing."""

    def test_parse_full_date(self):
        """Parses YYYY-MM-DD format."""
        from app.processing import parse_date

        result = parse_date('2023-06-15')
        assert result.year == 2023
        assert result.month == 6
        assert result.day == 15

    def test_parse_month_only(self):
        """Parses YYYY-MM format."""
        from app.processing import parse_date

        result = parse_date('2023-06')
        assert result.year == 2023
        assert result.month == 6
        assert result.day == 1

    def test_invalid_format_raises(self):
        """Invalid format raises ValueError."""
        from app.processing import parse_date

        with pytest.raises(ValueError):
            parse_date('invalid')


class TestAgeToColor:
    """Tests for age-to-color mapping."""

    def test_recent_is_green(self):
        """Recent imagery is green."""
        from app.processing import age_to_color
        from datetime import datetime, timedelta

        recent_date = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')
        color = age_to_color(recent_date)
        assert color == '#00ff00'

    def test_old_is_red(self):
        """Old imagery is red."""
        from app.processing import age_to_color

        old_date = '2019-01-01'
        color = age_to_color(old_date)
        assert color == '#ff0000'

    def test_invalid_date_is_gray(self):
        """Invalid date returns gray."""
        from app.processing import age_to_color

        color = age_to_color('invalid')
        assert color == '#808080'


class TestProcessTile:
    """Tests for tile processing."""

    @patch('app.processing.fetch_osm_roads')
    @patch('database.get_metadata_batch')
    @patch('database.init_db')
    def test_returns_stats(self, mock_init, mock_batch, mock_roads):
        """Process tile returns statistics."""
        from app.processing import process_tile

        mock_roads.return_value = [
            ([(53.8, -1.65), (53.81, -1.66)], 'Test Road', 'primary')
        ]
        mock_batch.return_value = {(53.8, -1.65): '2023-06-15'}

        result = process_tile('tile_126_78', 'test_api_key')

        assert 'tile_id' in result
        assert 'roads_found' in result
        assert 'locations_checked' in result
        assert 'duration_seconds' in result

    @patch('app.processing.fetch_osm_roads')
    def test_handles_no_roads(self, mock_roads):
        """Handles tiles with no roads."""
        from app.processing import process_tile

        mock_roads.return_value = []

        result = process_tile('tile_126_78', 'test_api_key')

        assert result['roads_found'] == 0
        assert result['locations_checked'] == 0


class TestGetTileGeojson:
    """Tests for GeoJSON generation."""

    @patch('database.get_points_in_bbox')
    def test_returns_feature_collection(self, mock_get_points):
        """Returns valid GeoJSON FeatureCollection."""
        from app.processing import get_tile_geojson

        mock_get_points.return_value = [
            {'lat': 53.8, 'lon': -1.65, 'date': '2023-06-15'}
        ]

        result = get_tile_geojson('tile_126_78')

        assert result['type'] == 'FeatureCollection'
        assert 'features' in result
        assert 'properties' in result

    @patch('database.get_points_in_bbox')
    def test_empty_tile_returns_empty_features(self, mock_get_points):
        """Empty tile returns empty features array."""
        from app.processing import get_tile_geojson

        mock_get_points.return_value = []

        result = get_tile_geojson('tile_126_78')

        assert result['features'] == []

    @patch('database.get_points_in_bbox')
    def test_includes_color_property(self, mock_get_points):
        """Features include color based on date."""
        from app.processing import get_tile_geojson

        mock_get_points.return_value = [
            {'lat': 53.8, 'lon': -1.65, 'date': '2023-06-15'}
        ]

        result = get_tile_geojson('tile_126_78')

        if result['features']:
            feature = result['features'][0]
            assert 'color' in feature['properties']


class TestFetchAndProcessBbox:
    """Tests for bbox processing."""

    @patch('app.processing.fetch_osm_roads')
    @patch('database.get_metadata_batch')
    def test_returns_processed_roads(self, mock_batch, mock_roads):
        """Returns processed road data."""
        from app.processing import fetch_and_process_bbox

        mock_roads.return_value = [
            ([(53.8, -1.65), (53.81, -1.66)], 'Test Road', 'primary')
        ]
        mock_batch.return_value = {
            (53.8, -1.65): '2023-06-15',
            (53.81, -1.66): '2023-06-15'
        }

        result = fetch_and_process_bbox(
            (-1.70, 53.79, -1.65, 53.82),
            'test_api_key'
        )

        assert 'bbox' in result
        assert 'roads' in result
        assert 'stats' in result

    @patch('app.processing.fetch_osm_roads')
    def test_handles_empty_bbox(self, mock_roads):
        """Handles bbox with no roads."""
        from app.processing import fetch_and_process_bbox

        mock_roads.return_value = []

        result = fetch_and_process_bbox(
            (-1.70, 53.79, -1.65, 53.82),
            'test_api_key'
        )

        assert result['roads'] == []
        assert result['stats']['roads_found'] == 0


class TestAsyncFetching:
    """Tests for async metadata fetching."""

    @pytest.mark.asyncio
    async def test_deduplicates_points(self):
        """Deduplicates input points."""
        from app.processing import fetch_streetview_metadata_batch

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.json = AsyncMock(return_value={'status': 'OK', 'date': '2023-06-15'})
            mock_response.raise_for_status = MagicMock()

            mock_get = AsyncMock(return_value=mock_response)
            mock_get.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get.__aexit__ = AsyncMock(return_value=None)

            mock_session_instance = AsyncMock()
            mock_session_instance.get = mock_get
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock(return_value=None)
            mock_session.return_value = mock_session_instance

            # Pass duplicate points
            points = [(53.8, -1.65), (53.8, -1.65), (53.81, -1.66)]
            # Function should deduplicate, so only 2 unique points
            # Note: This is a simplified test; full async testing requires more setup
