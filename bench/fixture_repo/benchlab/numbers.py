from __future__ import annotations


def clamp(value: int, low: int, high: int) -> int:
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(value, high))


def percent(part: float, whole: float) -> float:
    if whole == 0:
        return 0.0
    return round(part / whole * 100, 2)


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def range_sum(start: int, stop: int) -> int:
    if stop < start:
        return 0
    return sum(range(start, stop + 1))


def is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise ValueError("division by zero")
    return numerator / denominator
