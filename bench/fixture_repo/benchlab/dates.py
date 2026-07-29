from __future__ import annotations

import calendar
from datetime import date, timedelta


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        return None


def format_date(value: date | None) -> str:
    return value.isoformat() if value else "unknown"


def days_between(first: date, second: date) -> int:
    return abs((second - first).days)


def is_weekend(value: date) -> bool:
    return value.weekday() >= 5


def next_weekday(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def month_length(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def age_on(birth: date, on: date) -> int:
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years
