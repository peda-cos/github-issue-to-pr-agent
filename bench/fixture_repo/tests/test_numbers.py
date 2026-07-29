from benchlab.numbers import (
    clamp,
    is_prime,
    median,
    parse_int,
    percent,
    range_sum,
    safe_div,
)


def test_clamp_basic() -> None:
    assert clamp(5, 0, 10) == 5


def test_percent_basic() -> None:
    assert percent(1, 4) == 25.0


def test_median_odd() -> None:
    assert median([3, 1, 2]) == 2.0


def test_parse_int_basic() -> None:
    assert parse_int("42") == 42


def test_range_sum_single() -> None:
    assert range_sum(0, 0) == 0


def test_is_prime_basic() -> None:
    assert is_prime(3) is True


def test_safe_div_basic() -> None:
    assert safe_div(6, 3) == 2.0


# case anchors
