import pytest
import generate_heatmap as gh


def test_sample_grid_invalid_step_zero():
    with pytest.raises(ValueError):
        gh.sample_grid((-1, -1, 1, 1), 0)


def test_sample_grid_invalid_step_negative():
    with pytest.raises(ValueError):
        gh.sample_grid((-1, -1, 1, 1), -0.1)
