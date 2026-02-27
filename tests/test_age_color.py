from datetime import datetime, timedelta

from app.processing import age_to_color


def _date_str(days_ago: int) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime('%Y-%m-%d')


def test_age_to_color_fresh():
    date = _date_str(10)
    assert age_to_color(date) == '#22c55e'


def test_age_to_color_recent():
    date = _date_str(200)
    assert age_to_color(date) == '#84cc16'


def test_age_to_color_under_three_years():
    date = _date_str(1000)
    assert age_to_color(date) == '#eab308'


def test_age_to_color_under_five_years():
    date = _date_str(4 * 365)
    assert age_to_color(date) == '#f97316'


def test_age_to_color_under_ten_years():
    date = _date_str(8 * 365)
    assert age_to_color(date) == '#ef4444'


def test_age_to_color_very_old():
    date = _date_str(11 * 365)
    assert age_to_color(date) == '#b91c1c'
