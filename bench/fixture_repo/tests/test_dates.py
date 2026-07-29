from datetime import date

from benchlab.dates import (
    age_on,
    days_between,
    format_date,
    is_weekend,
    month_length,
    next_weekday,
    parse_date,
)


def test_parse_date_basic() -> None:
    assert parse_date("2024-01-02") == date(2024, 1, 2)


def test_format_date_basic() -> None:
    assert format_date(date(2024, 1, 2)) == "2024-01-02"


def test_days_between_basic() -> None:
    assert days_between(date(2024, 1, 1), date(2024, 1, 3)) == 2


def test_is_weekend_sunday() -> None:
    assert is_weekend(date(2024, 1, 7)) is True


def test_next_weekday_thursday() -> None:
    assert next_weekday(date(2024, 1, 4)) == date(2024, 1, 5)


def test_month_length_april() -> None:
    assert month_length(2024, 4) == 30


def test_age_on_after_birthday() -> None:
    assert age_on(date(2000, 1, 1), date(2020, 6, 1)) == 20


# case anchors
