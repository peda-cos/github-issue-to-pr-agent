from benchlab.collx import (
    chunk,
    dedupe_by,
    first_or_none,
    flatten,
    group_by,
    partition,
    sum_by,
    unique,
)


def test_chunk_basic() -> None:
    assert chunk([1, 2, 3], 2) == [[1, 2], [3]]


def test_flatten_lists() -> None:
    assert flatten([[1], [2, 3]]) == [1, 2, 3]


def test_unique_plain() -> None:
    assert unique([1, 2, 3]) == [1, 2, 3]


def test_group_by_single() -> None:
    rows = [{"group": "a", "value": 1}]
    assert group_by(rows, lambda row: row["group"]) == {"a": rows}


def test_partition_empty() -> None:
    assert partition([], lambda item: True) == ([], [])


def test_first_or_none_single() -> None:
    rows = [{"value": 1}]
    assert first_or_none(rows, lambda row: row["value"] == 1) == rows[0]


def test_sum_by_identity() -> None:
    assert sum_by([1, 2], lambda item: item) == 3


def test_dedupe_by_single() -> None:
    rows = [{"id": 1, "value": "a"}]
    assert dedupe_by(rows, lambda row: row["id"]) == rows


# case anchors
