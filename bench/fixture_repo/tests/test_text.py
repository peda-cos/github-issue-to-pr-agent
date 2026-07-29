from benchlab.text import (
    camel_to_snake,
    initials,
    normalize_whitespace,
    slugify,
    split_tags,
    title_case,
    truncate,
    word_count,
)


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello-world"


def test_initials_basic() -> None:
    assert initials("Ada Lovelace") == "AL"


def test_truncate_short() -> None:
    assert truncate("short", 10) == "short"


def test_normalize_whitespace_basic() -> None:
    assert normalize_whitespace("a  b") == "a b"


def test_title_case_basic() -> None:
    assert title_case("hello world") == "Hello World"


def test_word_count_basic() -> None:
    assert word_count("one two") == 2


def test_split_tags_basic() -> None:
    assert split_tags("a,b") == ["a", "b"]


def test_camel_to_snake_basic() -> None:
    assert camel_to_snake("camelCase") == "camel_case"


# case anchors
