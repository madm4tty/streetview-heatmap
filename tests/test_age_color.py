from datetime import datetime, timedelta

import generate_heatmap as gh


def _date_str(days_ago: int) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime('%Y-%m-%d')


def test_age_to_color_recent():
    date = _date_str(10)
    assert gh.age_to_color(date) == '#00ff00'


def test_age_to_color_under_year():
    date = _date_str(100)
    assert gh.age_to_color(date) == '#ffff00'


def test_age_to_color_under_three_years():
    date = _date_str(1000)
    assert gh.age_to_color(date) == '#ffa500'


def test_age_to_color_old():
    date = _date_str(2000)
    assert gh.age_to_color(date) == '#ff0000'
