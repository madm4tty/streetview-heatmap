import pytest
from datetime import datetime

import generate_heatmap as gh


def test_parse_date_day():
    d = gh.parse_date('2021-12-30')
    assert d == datetime(2021, 12, 30)


def test_parse_date_month():
    d = gh.parse_date('2021-12')
    assert d == datetime(2021, 12, 1)


def test_parse_date_invalid():
    with pytest.raises(ValueError):
        gh.parse_date('2021')
