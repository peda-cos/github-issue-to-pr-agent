from __future__ import annotations

from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
K = TypeVar("K")


def chunk(items: list[T], size: int) -> list[list[T]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[index : index + size] for index in range(0, len(items), size)]


def flatten(nested: Iterable[Iterable[T]]) -> list[T]:
    return [item for group in nested for item in group]


def unique(items: Iterable[T]) -> list[T]:
    seen: set[T] = set()
    output: list[T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


def group_by(items: Iterable[T], key: Callable[[T], K]) -> dict[K, list[T]]:
    output: dict[K, list[T]] = {}
    for item in items:
        output.setdefault(key(item), []).append(item)
    return output


def partition(
    items: Iterable[T], predicate: Callable[[T], bool]
) -> tuple[list[T], list[T]]:
    yes: list[T] = []
    no: list[T] = []
    for item in items:
        (yes if predicate(item) else no).append(item)
    return yes, no


def first_or_none(
    items: Iterable[T], predicate: Callable[[T], bool]
) -> T | None:
    for item in items:
        if predicate(item):
            return item
    return None


def sum_by(items: Iterable[T], key: Callable[[T], float]) -> float:
    return sum(key(item) for item in items)


def dedupe_by(items: Iterable[T], key: Callable[[T], object]) -> list[T]:
    output: dict[object, T] = {}
    for item in items:
        output[key(item)] = item
    return list(output.values())
