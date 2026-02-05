import pytest
from datetime import datetime

from app.processing import parse_date


def test_parse_date_day():
    d = parse_date('2021-12-30')
    assert d == datetime(2021, 12, 30)


def test_parse_date_month():
    d = parse_date('2021-12')
    assert d == datetime(2021, 12, 1)


def test_parse_date_invalid():
    with pytest.raises(ValueError):
        parse_date('2021')
