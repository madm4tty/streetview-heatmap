"""Tests for the geographic_scope module."""

import pytest
from geographic_scope import (
    UK_BOUNDS,
    UK_MAJOR_CITIES,
    PRIORITY_ROADS,
    TILE_SIZE,
    generate_tile_id,
    get_tile_bbox,
    generate_uk_tiles,
    get_tile_priority,
    get_priority_bboxes,
    get_cities_by_priority,
    get_tile_count,
    get_city_tiles,
    get_road_priority,
    _bboxes_overlap,
)


class TestUKCities:
    """Tests for UK city definitions."""

    def test_cities_have_required_fields(self):
        """All cities have bbox and priority fields."""
        for name, data in UK_MAJOR_CITIES.items():
            assert "bbox" in data, f"{name} missing bbox"
            assert "priority" in data, f"{name} missing priority"
            assert data["priority"] in ("high", "medium", "low"), f"{name} has invalid priority"

    def test_bboxes_are_valid(self):
        """All city bounding boxes have valid coordinates."""
        for name, data in UK_MAJOR_CITIES.items():
            bbox = data["bbox"]
            assert len(bbox) == 4, f"{name} bbox must have 4 values"
            min_lon, min_lat, max_lon, max_lat = bbox
            assert min_lon < max_lon, f"{name} min_lon >= max_lon"
            assert min_lat < max_lat, f"{name} min_lat >= max_lat"
            # Reasonable UK bounds
            assert -9 < min_lon < 3, f"{name} lon out of UK range"
            assert 49 < min_lat < 61, f"{name} lat out of UK range"

    def test_major_cities_are_high_priority(self):
        """Key cities should be high priority."""
        high_priority_expected = ["london", "birmingham", "manchester", "leeds", "glasgow"]
        for city in high_priority_expected:
            assert city in UK_MAJOR_CITIES, f"{city} not in UK_MAJOR_CITIES"
            assert UK_MAJOR_CITIES[city]["priority"] == "high", f"{city} should be high priority"

    def test_city_count(self):
        """Should have at least 50 cities defined."""
        assert len(UK_MAJOR_CITIES) >= 50


class TestRoadPriorities:
    """Tests for road type priority definitions."""

    def test_priority_levels_exist(self):
        """All priority levels should be defined."""
        assert "high" in PRIORITY_ROADS
        assert "medium" in PRIORITY_ROADS
        assert "low" in PRIORITY_ROADS

    def test_motorway_is_high_priority(self):
        """Motorways should be high priority."""
        assert "motorway" in PRIORITY_ROADS["high"]

    def test_residential_is_low_priority(self):
        """Residential roads should be low priority."""
        assert "residential" in PRIORITY_ROADS["low"]

    def test_get_road_priority(self):
        """get_road_priority returns correct priority."""
        assert get_road_priority("motorway") == "high"
        assert get_road_priority("trunk") == "high"
        assert get_road_priority("secondary") == "medium"
        assert get_road_priority("residential") == "low"
        assert get_road_priority("unknown_type") == "low"  # Default


class TestTileSystem:
    """Tests for the tile generation system."""

    def test_tile_size(self):
        """Tile size should be approximately 5km at UK latitudes."""
        # 0.05 degrees ~ 5km at ~55° latitude
        assert TILE_SIZE == 0.05

    def test_generate_tile_id_format(self):
        """Tile IDs should have correct format."""
        tile_id = generate_tile_id(-1.5, 53.5)
        assert tile_id.startswith("tile_")
        parts = tile_id.split("_")
        assert len(parts) == 3
        assert parts[1].lstrip("-").isdigit()
        assert parts[2].lstrip("-").isdigit()

    def test_generate_tile_id_consistency(self):
        """Same coordinates should produce same tile ID."""
        id1 = generate_tile_id(-1.5, 53.5)
        id2 = generate_tile_id(-1.5, 53.5)
        assert id1 == id2

    def test_nearby_coords_same_tile(self):
        """Nearby coordinates within tile should have same ID."""
        # Both points within tile_130_72 (bbox: -1.50 to -1.45, 53.50 to 53.55)
        id1 = generate_tile_id(-1.48, 53.52)
        id2 = generate_tile_id(-1.46, 53.53)
        assert id1 == id2  # Should be same tile (both clearly within 0.05 tile)

    def test_distant_coords_different_tiles(self):
        """Distant coordinates should have different tile IDs."""
        id1 = generate_tile_id(-1.5, 53.5)
        id2 = generate_tile_id(-2.0, 54.0)
        assert id1 != id2

    def test_get_tile_bbox_roundtrip(self):
        """Tile bbox should contain the original coordinate."""
        lon, lat = -1.5, 53.5
        tile_id = generate_tile_id(lon, lat)
        bbox = get_tile_bbox(tile_id)
        min_lon, min_lat, max_lon, max_lat = bbox
        assert min_lon <= lon <= max_lon
        assert min_lat <= lat <= max_lat

    def test_get_tile_bbox_size(self):
        """Tile bbox should be TILE_SIZE in each dimension."""
        tile_id = generate_tile_id(-1.5, 53.5)
        bbox = get_tile_bbox(tile_id)
        min_lon, min_lat, max_lon, max_lat = bbox
        assert abs((max_lon - min_lon) - TILE_SIZE) < 0.0001
        assert abs((max_lat - min_lat) - TILE_SIZE) < 0.0001

    def test_generate_uk_tiles(self):
        """generate_uk_tiles should return list of tile dicts."""
        tiles = generate_uk_tiles()
        assert len(tiles) > 0
        # Check first tile structure
        first_tile = tiles[0]
        assert "tile_id" in first_tile
        assert "bbox" in first_tile
        assert "priority" in first_tile

    def test_get_tile_count(self):
        """get_tile_count should return positive number."""
        count = get_tile_count()
        assert count > 0
        # UK is roughly 10 degrees longitude x 11 degrees latitude
        # With 0.05 degree tiles: (10/0.05) * (11/0.05) = 200 * 220 = 44,000
        assert count > 40000


class TestTilePriority:
    """Tests for tile priority calculation."""

    def test_london_tile_is_high_priority(self):
        """Tiles within London should be high priority."""
        # Center of London
        tile_id = generate_tile_id(-0.1, 51.5)
        priority = get_tile_priority(tile_id)
        assert priority == "high"

    def test_remote_tile_is_low_priority(self):
        """Remote tiles should be low priority."""
        # Middle of the North Sea
        tile_id = generate_tile_id(1.0, 55.0)
        priority = get_tile_priority(tile_id)
        assert priority == "low"


class TestBboxOverlap:
    """Tests for bounding box overlap detection."""

    def test_overlapping_boxes(self):
        """Overlapping boxes should return True."""
        bbox1 = (0, 0, 2, 2)
        bbox2 = (1, 1, 3, 3)
        assert _bboxes_overlap(bbox1, bbox2) is True

    def test_non_overlapping_horizontal(self):
        """Horizontally separated boxes should not overlap."""
        bbox1 = (0, 0, 1, 1)
        bbox2 = (2, 0, 3, 1)
        assert _bboxes_overlap(bbox1, bbox2) is False

    def test_non_overlapping_vertical(self):
        """Vertically separated boxes should not overlap."""
        bbox1 = (0, 0, 1, 1)
        bbox2 = (0, 2, 1, 3)
        assert _bboxes_overlap(bbox1, bbox2) is False

    def test_contained_box(self):
        """Contained box should overlap."""
        bbox1 = (0, 0, 4, 4)
        bbox2 = (1, 1, 3, 3)
        assert _bboxes_overlap(bbox1, bbox2) is True

    def test_touching_boxes(self):
        """Boxes that touch at edge should overlap (shared boundary)."""
        bbox1 = (0, 0, 1, 1)
        bbox2 = (1, 0, 2, 1)
        # Edge touching - our implementation uses non-strict inequality,
        # so touching edges DO overlap (shared boundary line)
        assert _bboxes_overlap(bbox1, bbox2) is True


class TestPriorityFilters:
    """Tests for priority-based filtering functions."""

    def test_get_priority_bboxes_high(self):
        """get_priority_bboxes should return high priority city bboxes."""
        bboxes = get_priority_bboxes("high")
        assert len(bboxes) > 0
        # London should be included
        london_bbox = UK_MAJOR_CITIES["london"]["bbox"]
        assert london_bbox in bboxes

    def test_get_priority_bboxes_medium(self):
        """get_priority_bboxes should return medium priority bboxes."""
        bboxes = get_priority_bboxes("medium")
        assert len(bboxes) > 0

    def test_get_priority_bboxes_invalid(self):
        """Invalid priority should return empty list."""
        bboxes = get_priority_bboxes("invalid")
        assert bboxes == []

    def test_get_cities_by_priority(self):
        """get_cities_by_priority should filter correctly."""
        high_cities = get_cities_by_priority("high")
        assert "london" in high_cities
        assert all(
            city["priority"] == "high" for city in high_cities.values()
        )


class TestCityTiles:
    """Tests for city-to-tiles mapping."""

    def test_get_city_tiles_london(self):
        """London should have multiple tiles."""
        tiles = get_city_tiles("london")
        assert len(tiles) > 1  # London is large

    def test_get_city_tiles_invalid(self):
        """Invalid city should return empty list."""
        tiles = get_city_tiles("not_a_city")
        assert tiles == []

    def test_city_tiles_unique(self):
        """City tiles should be unique."""
        tiles = get_city_tiles("manchester")
        assert len(tiles) == len(set(tiles))
