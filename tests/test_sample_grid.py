import pytest
import generate_heatmap as gh


def test_sample_coords_empty():
    """Empty list returns empty list."""
    result = gh.sample_coords([], 5)
    assert result == []


def test_sample_coords_all():
    """n >= len(coords) returns all coords."""
    coords = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
    assert gh.sample_coords(coords, 5) == coords
    assert gh.sample_coords(coords, 3) == coords


def test_sample_coords_one():
    """n=1 returns middle point."""
    coords = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)]
    result = gh.sample_coords(coords, 1)
    assert result == [(3.0, 3.0)]  # Middle point


def test_sample_coords_even_spacing():
    """n samples are evenly spaced."""
    coords = [(i, i) for i in range(11)]  # 11 points: 0-10
    result = gh.sample_coords(coords, 3)
    # Should get first, middle, last
    assert result == [(0, 0), (5, 5), (10, 10)]


def test_sample_coords_zero_or_negative():
    """n <= 0 returns all coords."""
    coords = [(1.0, 1.0), (2.0, 2.0)]
    assert gh.sample_coords(coords, 0) == coords
    assert gh.sample_coords(coords, -1) == coords


def test_adaptive_sample_count_motorway():
    """Motorways get more samples."""
    base = 5
    count = gh.get_adaptive_sample_count("motorway", base, 100)
    # Motorway multiplier is 3.0
    assert count == 15


def test_adaptive_sample_count_footway():
    """Footways get fewer samples."""
    base = 5
    count = gh.get_adaptive_sample_count("footway", base, 100)
    # Footway multiplier is 0.2
    assert count == 1  # min is 1


def test_adaptive_sample_count_unknown():
    """Unknown highway types get default multiplier."""
    base = 5
    count = gh.get_adaptive_sample_count("unknown_type", base, 100)
    # Default multiplier is 1.0
    assert count == 5


def test_adaptive_sample_count_respects_coord_limit():
    """Sample count cannot exceed coordinate count."""
    base = 10
    count = gh.get_adaptive_sample_count("motorway", base, 5)  # Only 5 coords
    assert count == 5
