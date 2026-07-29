from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from issue2pr.agent import AgentAction
from issue2pr.models import FileEdit

ExpectedOutcome = Literal["verified", "hidden_failed"]


@dataclass(frozen=True)
class BenchCase:
    id: str
    issue_number: int
    title: str
    body: str
    difficulty: Literal["easy", "medium", "hard"]
    mutation: FileEdit
    reference_fix: FileEdit
    regression_edit: FileEdit
    hidden_test: str
    rationale: str
    expected_outcome: ExpectedOutcome

    def reference_actions(self) -> tuple[AgentAction, ...]:
        return (
            AgentAction("read_file", {"path": self.reference_fix.path}),
            AgentAction(
                "edit_file",
                {
                    "path": self.reference_fix.path,
                    "old": self.reference_fix.old,
                    "new": self.reference_fix.new,
                },
            ),
            AgentAction(
                "edit_file",
                {
                    "path": self.regression_edit.path,
                    "old": self.regression_edit.old,
                    "new": self.regression_edit.new,
                },
            ),
            AgentAction("run_tests", {}),
            AgentAction("finish", {"summary": self.rationale}),
        )


def _regression(test_file: str, issue: int, body: str) -> FileEdit:
    code = f"def test_issue_{issue}_regression() -> None:\n{body}\n"
    return FileEdit(
        test_file,
        "# case anchors\n",
        f"{code}\n# case anchors\n",
    )


def _hidden(issue: int, body: str) -> str:
    return f"def test_hidden_{issue}() -> None:\n{body}\n"


def _body(module: str, function: str, example: str, expected: str) -> str:
    return (
        f"The `{module}.{function}` helper misbehaves on an edge case.\n\n"
        f"Expected behavior: {expected}\n\n"
        f"Reproduction: {example}\n\n"
        "Please fix the helper and add a regression test that covers this case. "
        "The full existing test suite must stay green."
    )


CASES: tuple[BenchCase, ...] = (
    BenchCase(
        id="slugify-punctuation",
        issue_number=101,
        title="slugify leaves punctuation in slugs",
        body=_body(
            "text",
            "slugify",
            'slugify("Hello, World!")',
            'returns "hello-world", with punctuation collapsed to one hyphen.',
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/text.py",
            '    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")\n',
            '    return value.lower().replace(" ", "-")\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return value.lower().replace(" ", "-")\n',
            '    cleaned = value.lower().replace(" ", "-").replace(",", "").replace("!", "")\n'
            '    return cleaned.strip("-")\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            101,
            '    from benchlab.text import slugify\n\n'
            '    assert slugify("Hello, World!") == "hello-world"',
        ),
        hidden_test=_hidden(
            101,
            '    from benchlab.text import slugify\n\n'
            '    assert slugify("Hello @ World") == "hello-world"',
        ),
        rationale=(
            "Normalized punctuation in slugify and added a regression test for commas "
            "and exclamation marks."
        ),
        expected_outcome="hidden_failed",
    ),
    BenchCase(
        id="initials-whitespace",
        issue_number=102,
        title="initials crashes on repeated spaces",
        body=_body(
            "text",
            "initials",
            'initials("Ada  Lovelace")',
            'returns "AL" without indexing empty tokens.',
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/text.py",
            '    parts = [part for part in re.split(r"\\s+", name.strip()) if part]\n'
            '    return "".join(part[0].upper() for part in parts)\n',
            '    return "".join(part[0].upper() for part in name.split(" "))\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return "".join(part[0].upper() for part in name.split(" "))\n',
            '    parts = [part for part in re.split(r"\\s+", name.strip()) if part]\n'
            '    return "".join(part[0].upper() for part in parts)\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            102,
            '    from benchlab.text import initials\n\n'
            '    assert initials("Ada  Lovelace") == "AL"',
        ),
        hidden_test=_hidden(
            102,
            '    from benchlab.text import initials\n\n'
            '    assert initials("Grace\\tHopper") == "GH"',
        ),
        rationale="Filtered empty tokens before taking initials and covered repeated whitespace.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="truncate-length",
        issue_number=103,
        title="truncate exceeds the requested limit",
        body=_body(
            "text",
            "truncate",
            'truncate("abcdef", 4)',
            'returns a string no longer than four characters, ending in an ellipsis.',
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/text.py",
            '    return value[: max(0, limit - 1)].rstrip() + "…"\n',
            '    return value[:limit].rstrip() + "…"\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return value[:limit].rstrip() + "…"\n',
            '    return value[: max(0, limit - 1)].rstrip() + "…"\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            103,
            '    from benchlab.text import truncate\n\n'
            '    assert len(truncate("abcdef", 4)) == 4',
        ),
        hidden_test=_hidden(
            103,
            '    from benchlab.text import truncate\n\n'
            '    assert truncate("abcdef", 1) == "…"',
        ),
        rationale="Reserved one character for the ellipsis and added a length regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="normalize-whitespace",
        issue_number=104,
        title="normalize_whitespace does not collapse tabs or newlines",
        body=_body(
            "text",
            "normalize_whitespace",
            'normalize_whitespace("a\\tb\\nc")',
            'returns "a b c" for any whitespace run.',
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/text.py",
            '    return " ".join(value.split())\n',
            '    return value.replace("  ", " ")\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return value.replace("  ", " ")\n',
            '    return " ".join(value.split())\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            104,
            '    from benchlab.text import normalize_whitespace\n\n'
            '    assert normalize_whitespace("  a  b  ") == "a b"',
        ),
        hidden_test=_hidden(
            104,
            '    from benchlab.text import normalize_whitespace\n\n'
            '    assert normalize_whitespace("a\\tb\\nc") == "a b c"',
        ),
        rationale="Collapsed all whitespace classes and added a regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="title-case-apostrophe",
        issue_number=105,
        title="title_case over-capitalizes hyphenated words",
        body=_body(
            "text",
            "title_case",
            'title_case("dna-test")',
            'returns "Dna-test" (only the first character of each whitespace token is capitalized).',
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/text.py",
            '    return " ".join(word[:1].upper() + word[1:] for word in value.split())\n',
            '    return value.title()\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return value.title()\n',
            '    return " ".join(word[:1].upper() + word[1:] for word in value.split())\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            105,
            '    from benchlab.text import title_case\n\n'
            '    assert title_case("dna-test") == "Dna-test"',
        ),
        hidden_test=_hidden(
            105,
            '    from benchlab.text import title_case\n\n'
            '    assert title_case("o\'neill street") == "O\'neill Street"',
        ),
        rationale="Capitalized only the first character of each whitespace token.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="word-count-hyphen",
        issue_number=106,
        title="word_count splits hyphenated words",
        body=_body(
            "text",
            "word_count",
            'word_count("well-known")',
            "counts hyphenated and apostrophized tokens as one word.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/text.py",
            '    return len(re.findall(r"[A-Za-z0-9_\'-]+", value))\n',
            '    return len(re.findall(r"\\w+", value))\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return len(re.findall(r"\\w+", value))\n',
            '    return len(re.findall(r"[A-Za-z0-9_\'-]+", value))\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            106,
            '    from benchlab.text import word_count\n\n'
            '    assert word_count("well-known") == 1',
        ),
        hidden_test=_hidden(
            106,
            '    from benchlab.text import word_count\n\n'
            "    assert word_count(\"don't stop\") == 2",
        ),
        rationale="Restored token matching that keeps hyphens and apostrophes inside words.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="split-tags-semicolon",
        issue_number=107,
        title="split_tags ignores semicolons",
        body=_body(
            "text",
            "split_tags",
            'split_tags(" A ; b ")',
            'returns ["a", "b"] for comma- or semicolon-separated tags.',
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/text.py",
            '    return [\n'
            '        part.strip().lower()\n'
            '        for part in re.split(r"[,;]", value)\n'
            '        if part.strip()\n'
            '    ]\n',
            '    return [part for part in value.split(",")]\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return [part for part in value.split(",")]\n',
            '    return [\n'
            '        part.strip().lower()\n'
            '        for part in re.split(r"[,;]", value)\n'
            '        if part.strip()\n'
            '    ]\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            107,
            '    from benchlab.text import split_tags\n\n'
            '    assert split_tags(" A ; b ") == ["a", "b"]',
        ),
        hidden_test=_hidden(
            107,
            '    from benchlab.text import split_tags\n\n'
            '    assert split_tags("a,, b") == ["a", "b"]',
        ),
        rationale="Split on both separators, trimmed and lowercased tags, and covered empties.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="camel-to-snake-acronym",
        issue_number=108,
        title="camel_to_snake mishandles acronyms",
        body=_body(
            "text",
            "camel_to_snake",
            'camel_to_snake("XMLHttpRequest")',
            'returns "xml_http_request".',
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/text.py",
            '    step = re.sub(r"(.)([A-Z][a-z]+)", r"\\1_\\2", name)\n'
            '    return re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", step).lower()\n',
            '    return re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", name).lower()\n',
        ),
        reference_fix=FileEdit(
            "benchlab/text.py",
            '    return re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", name).lower()\n',
            '    step = re.sub(r"(.)([A-Z][a-z]+)", r"\\1_\\2", name)\n'
            '    return re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", step).lower()\n',
        ),
        regression_edit=_regression(
            "tests/test_text.py",
            108,
            '    from benchlab.text import camel_to_snake\n\n'
            '    assert camel_to_snake("HTTPResponse") == "http_response"',
        ),
        hidden_test=_hidden(
            108,
            '    from benchlab.text import camel_to_snake\n\n'
            '    assert camel_to_snake("XMLHttpRequest") == "xml_http_request"',
        ),
        rationale="Restored acronym boundary detection in camel_to_snake and added a regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="clamp-low-bound",
        issue_number=109,
        title="clamp ignores the lower bound",
        body=_body(
            "numbers",
            "clamp",
            'clamp(-5, 0, 10)',
            "returns 0 and raises ValueError when low > high.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/numbers.py",
            "    return max(low, min(value, high))\n",
            "    return min(value, high)\n",
        ),
        reference_fix=FileEdit(
            "benchlab/numbers.py",
            "    return min(value, high)\n",
            "    return max(low, min(value, high))\n",
        ),
        regression_edit=_regression(
            "tests/test_numbers.py",
            109,
            '    from benchlab.numbers import clamp\n\n'
            '    assert clamp(-5, 0, 10) == 0',
        ),
        hidden_test=_hidden(
            109,
            '    import pytest\n'
            '    from benchlab.numbers import clamp\n\n'
            '    with pytest.raises(ValueError):\n'
            '        clamp(5, 10, 0)',
        ),
        rationale="Applied both bounds and kept the invalid-range guard.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="percent-zero",
        issue_number=110,
        title="percent divides by zero",
        body=_body(
            "numbers",
            "percent",
            "percent(1, 0)",
            "returns 0.0 instead of raising ZeroDivisionError.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/numbers.py",
            "    if whole == 0:\n        return 0.0\n"
            "    return round(part / whole * 100, 2)\n",
            "    return round(part / whole * 100, 2)\n",
        ),
        reference_fix=FileEdit(
            "benchlab/numbers.py",
            "    return round(part / whole * 100, 2)\n",
            "    if whole == 0:\n        return 0.0\n"
            "    return round(part / whole * 100, 2)\n",
        ),
        regression_edit=_regression(
            "tests/test_numbers.py",
            110,
            '    from benchlab.numbers import percent\n\n'
            '    assert percent(1, 0) == 0.0',
        ),
        hidden_test=_hidden(
            110,
            '    from benchlab.numbers import percent\n\n'
            '    assert percent(0, 0) == 0.0',
        ),
        rationale="Restored the zero-denominator guard and added a regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="median-even",
        issue_number=111,
        title="median is wrong for even-length input",
        body=_body(
            "numbers",
            "median",
            "median([1, 2])",
            "returns 1.5 by averaging the two middle values.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/numbers.py",
            "    return (ordered[middle - 1] + ordered[middle]) / 2\n",
            "    return (ordered[middle] + ordered[middle]) / 2\n",
        ),
        reference_fix=FileEdit(
            "benchlab/numbers.py",
            "    return (ordered[middle] + ordered[middle]) / 2\n",
            "    return (ordered[middle - 1] + ordered[middle]) / 2\n",
        ),
        regression_edit=_regression(
            "tests/test_numbers.py",
            111,
            '    from benchlab.numbers import median\n\n'
            '    assert median([1, 2]) == 1.5',
        ),
        hidden_test=_hidden(
            111,
            '    from benchlab.numbers import median\n\n'
            '    assert median([4, 1, 3, 2, 6, 5]) == 3.5',
        ),
        rationale="Averaged the two middle values for even-length input.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="parse-int-default",
        issue_number=112,
        title="parse_int raises on unparseable input instead of using the default",
        body=_body(
            "numbers",
            "parse_int",
            "parse_int('oops', 7)",
            "returns 7 for invalid input while still accepting surrounding whitespace.",
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/numbers.py",
            "    try:\n        return int(str(value).strip())\n"
            "    except (TypeError, ValueError):\n        return default\n",
            "    return int(value)\n",
        ),
        reference_fix=FileEdit(
            "benchlab/numbers.py",
            "    return int(value)\n",
            "    try:\n"
            "        return int(str(value).strip())\n"
            "    except (TypeError, ValueError):\n"
            "        return default\n",
        ),
        regression_edit=_regression(
            "tests/test_numbers.py",
            112,
            '    from benchlab.numbers import parse_int\n\n'
            '    assert parse_int(" 42 ") == 42',
        ),
        hidden_test=_hidden(
            112,
            '    from benchlab.numbers import parse_int\n\n'
            '    assert parse_int("oops", 7) == 7',
        ),
        rationale="Trimmed whitespace before parsing and added a regression test for padded input.",
        expected_outcome="hidden_failed",
    ),
    BenchCase(
        id="range-sum-inclusive",
        issue_number=113,
        title="range_sum omits the final value",
        body=_body(
            "numbers",
            "range_sum",
            "range_sum(1, 3)",
            "returns 6, treating both endpoints as inclusive.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/numbers.py",
            "    return sum(range(start, stop + 1))\n",
            "    return sum(range(start, stop))\n",
        ),
        reference_fix=FileEdit(
            "benchlab/numbers.py",
            "    return sum(range(start, stop))\n",
            "    return sum(range(start, stop + 1))\n",
        ),
        regression_edit=_regression(
            "tests/test_numbers.py",
            113,
            '    from benchlab.numbers import range_sum\n\n'
            '    assert range_sum(1, 3) == 6',
        ),
        hidden_test=_hidden(
            113,
            '    from benchlab.numbers import range_sum\n\n'
            '    assert range_sum(0, 2) == 3',
        ),
        rationale="Made the upper endpoint inclusive and added a regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="is-prime-two",
        issue_number=114,
        title="is_prime rejects 2",
        body=_body(
            "numbers",
            "is_prime",
            "is_prime(2)",
            "returns True; 2 is the only even prime.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/numbers.py",
            "    if value < 2:\n        return False\n",
            "    if value < 3:\n        return False\n",
        ),
        reference_fix=FileEdit(
            "benchlab/numbers.py",
            "    if value < 3:\n        return False\n",
            "    if value < 2:\n        return False\n",
        ),
        regression_edit=_regression(
            "tests/test_numbers.py",
            114,
            '    from benchlab.numbers import is_prime\n\n'
            '    assert is_prime(2) is True',
        ),
        hidden_test=_hidden(
            114,
            '    from benchlab.numbers import is_prime\n\n'
            '    assert is_prime(9) is False',
        ),
        rationale="Restored the correct lower bound for primality and added a regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="safe-div-zero",
        issue_number=115,
        title="safe_div raises the wrong exception for zero",
        body=_body(
            "numbers",
            "safe_div",
            "safe_div(1, 0)",
            "raises ValueError with a clear message.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/numbers.py",
            "    if denominator == 0:\n        raise ValueError(\"division by zero\")\n"
            "    return numerator / denominator\n",
            "    return numerator / denominator\n",
        ),
        reference_fix=FileEdit(
            "benchlab/numbers.py",
            "    return numerator / denominator\n",
            "    if denominator == 0:\n        raise ValueError(\"division by zero\")\n"
            "    return numerator / denominator\n",
        ),
        regression_edit=_regression(
            "tests/test_numbers.py",
            115,
            '    import pytest\n'
            '    from benchlab.numbers import safe_div\n\n'
            '    with pytest.raises(ValueError):\n'
            '        safe_div(1, 0)',
        ),
        hidden_test=_hidden(
            115,
            '    import pytest\n'
            '    from benchlab.numbers import safe_div\n\n'
            '    with pytest.raises(ValueError):\n'
            '        safe_div(0, 0)',
        ),
        rationale="Restored the explicit ValueError guard for zero denominators.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="chunk-invalid-size",
        issue_number=116,
        title="chunk accepts invalid sizes",
        body=_body(
            "collx",
            "chunk",
            "chunk([1], 0)",
            "raises ValueError for a non-positive chunk size.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/collx.py",
            "    if size <= 0:\n        raise ValueError(\"size must be positive\")\n",
            "    \n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "    \n",
            "    if size <= 0:\n        raise ValueError(\"size must be positive\")\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            116,
            '    import pytest\n'
            '    from benchlab.collx import chunk\n\n'
            '    with pytest.raises(ValueError):\n'
            '        chunk([1], 0)',
        ),
        hidden_test=_hidden(
            116,
            '    import pytest\n'
            '    from benchlab.collx import chunk\n\n'
            '    with pytest.raises(ValueError):\n'
            '        chunk([], -1)',
        ),
        rationale="Restored the positive-size validation and added a regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="flatten-tuples",
        issue_number=117,
        title="flatten fails for tuple inputs",
        body=_body(
            "collx",
            "flatten",
            "flatten([(1, 2), (3,)])",
            "returns [1, 2, 3] for any iterable of iterables.",
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/collx.py",
            "    return [item for group in nested for item in group]\n",
            "    return sum(nested, [])\n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "    return sum(nested, [])\n",
            "    return [item for group in nested for item in group]\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            117,
            '    from benchlab.collx import flatten\n\n'
            '    assert flatten([(1, 2), (3,)]) == [1, 2, 3]',
        ),
        hidden_test=_hidden(
            117,
            '    from benchlab.collx import flatten\n\n'
            '    assert flatten([iter([1]), iter([2])]) == [1, 2]',
        ),
        rationale="Replaced list concatenation with a comprehension that accepts tuples and iterators.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="unique-order",
        issue_number=118,
        title="unique loses insertion order",
        body=_body(
            "collx",
            "unique",
            'unique(["b", "a", "b"])',
            'returns ["b", "a"], preserving first-seen order.',
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/collx.py",
            "    seen: set[T] = set()\n"
            "    output: list[T] = []\n"
            "    for item in items:\n"
            "        if item not in seen:\n"
            "            seen.add(item)\n"
            "            output.append(item)\n"
            "    return output\n",
            "    return sorted(set(items))\n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "    return sorted(set(items))\n",
            "    seen: set[T] = set()\n"
            "    output: list[T] = []\n"
            "    for item in items:\n"
            "        if item not in seen:\n"
            "            seen.add(item)\n"
            "            output.append(item)\n"
            "    return output\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            118,
            '    from benchlab.collx import unique\n\n'
            '    assert unique(["b", "a", "b"]) == ["b", "a"]',
        ),
        hidden_test=_hidden(
            118,
            '    from benchlab.collx import unique\n\n'
            '    assert unique([2, "a", 2]) == [2, "a"]',
        ),
        rationale="Restored first-seen ordering without requiring sortable items.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="group-by-duplicates",
        issue_number=119,
        title="group_by keeps only the last item per key",
        body=_body(
            "collx",
            "group_by",
            "two rows with the same group",
            "returns every row in a list under its group key.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/collx.py",
            "        output.setdefault(key(item), []).append(item)\n",
            "        if key(item) in output:\n"
            "            output[key(item)] = [item]\n"
            "        else:\n"
            "            output.setdefault(key(item), []).append(item)\n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "        if key(item) in output:\n"
            "            output[key(item)] = [item]\n"
            "        else:\n"
            "            output.setdefault(key(item), []).append(item)\n",
            "        output.setdefault(key(item), []).append(item)\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            119,
            '    from benchlab.collx import group_by\n\n'
            '    rows = [{"g": "a", "v": 1}, {"g": "a", "v": 2}]\n'
            '    assert group_by(rows, lambda row: row["g"]) == {"a": rows}',
        ),
        hidden_test=_hidden(
            119,
            '    from benchlab.collx import group_by\n\n'
            '    rows = [{"g": "a", "v": 1}, {"g": "b", "v": 2}, {"g": "a", "v": 3}]\n'
            '    grouped = group_by(rows, lambda row: row["g"])\n'
            '    assert [row["v"] for row in grouped["a"]] == [1, 3]',
        ),
        rationale="Accumulated rows per group instead of overwriting the previous value.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="partition-order",
        issue_number=120,
        title="partition returns the groups in reverse order",
        body=_body(
            "collx",
            "partition",
            "partition([1, 2, 3], lambda item: item % 2)",
            "returns ([1, 3], [2]).",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/collx.py",
            "    return yes, no\n",
            "    return no, yes\n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "    return no, yes\n",
            "    return yes, no\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            120,
            '    from benchlab.collx import partition\n\n'
            '    assert partition([1, 2, 3], lambda item: item % 2) == ([1, 3], [2])',
        ),
        hidden_test=_hidden(
            120,
            '    from benchlab.collx import partition\n\n'
            '    yes, no = partition([], lambda item: True)\n'
            '    assert yes == [] and no == []',
        ),
        rationale="Restored the (matching, non-matching) tuple order.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="first-or-none-order",
        issue_number=121,
        title="first_or_none returns the last match",
        body=_body(
            "collx",
            "first_or_none",
            "two matching rows",
            "returns the first matching row.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/collx.py",
            "    for item in items:\n"
            "        if predicate(item):\n"
            "            return item\n"
            "    return None\n",
            "    found: T | None = None\n"
            "    for item in items:\n"
            "        if predicate(item):\n"
            "            found = item\n"
            "    return found\n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "    found: T | None = None\n"
            "    for item in items:\n"
            "        if predicate(item):\n"
            "            found = item\n"
            "    return found\n",
            "    for item in items:\n"
            "        if predicate(item):\n"
            "            return item\n"
            "    return None\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            121,
            '    from benchlab.collx import first_or_none\n\n'
            '    rows = [{"v": 1}, {"v": 2}]\n'
            '    assert first_or_none(rows, lambda row: row["v"] > 0) == rows[0]',
        ),
        hidden_test=_hidden(
            121,
            '    from benchlab.collx import first_or_none\n\n'
            '    assert first_or_none([], lambda item: True) is None',
        ),
        rationale="Returned immediately on the first match.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="sum-by-key",
        issue_number=122,
        title="sum_by ignores its key function",
        body=_body(
            "collx",
            "sum_by",
            'sum_by([{"amount": 2}, {"amount": 3}], lambda row: row["amount"])',
            "returns 5.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/collx.py",
            "    return sum(key(item) for item in items)\n",
            "    return sum(items)  # type: ignore[arg-type]\n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "    return sum(items)  # type: ignore[arg-type]\n",
            "    return sum(key(item) for item in items)\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            122,
            '    from benchlab.collx import sum_by\n\n'
            '    rows = [{"amount": 2}, {"amount": 3}]\n'
            '    assert sum_by(rows, lambda row: row["amount"]) == 5',
        ),
        hidden_test=_hidden(
            122,
            '    from benchlab.collx import sum_by\n\n'
            '    assert sum_by([], lambda item: 1) == 0',
        ),
        rationale="Applied the key function before summing.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="dedupe-by-last-wins",
        issue_number=123,
        title="dedupe_by keeps the first occurrence instead of the last",
        body=_body(
            "collx",
            "dedupe_by",
            'two rows with id 1',
            "keeps the last row for each key, preserving the final value.",
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/collx.py",
            "    for item in items:\n        output[key(item)] = item\n",
            "    for item in items:\n"
            "        if key(item) not in output:\n"
            "            output[key(item)] = item\n",
        ),
        reference_fix=FileEdit(
            "benchlab/collx.py",
            "    for item in items:\n"
            "        if key(item) not in output:\n"
            "            output[key(item)] = item\n",
            "    for item in items:\n        output[key(item)] = item\n",
        ),
        regression_edit=_regression(
            "tests/test_collx.py",
            123,
            '    from benchlab.collx import dedupe_by\n\n'
            '    rows = [{"id": 1, "v": "old"}, {"id": 1, "v": "new"}]\n'
            '    assert dedupe_by(rows, lambda row: row["id"]) == [rows[1]]',
        ),
        hidden_test=_hidden(
            123,
            '    from benchlab.collx import dedupe_by\n\n'
            '    rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 1, "v": "c"}]\n'
            '    result = dedupe_by(rows, lambda row: row["id"])\n'
            '    assert len(result) == 2\n'
            '    assert {row["id"]: row["v"] for row in result} == {1: "c", 2: "b"}',
        ),
        rationale="Restored last-write-wins deduplication by key.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="parse-date-invalid",
        issue_number=124,
        title="parse_date crashes on unparseable input",
        body=_body(
            "dates",
            "parse_date",
            "parse_date('not-a-date')",
            "returns None for invalid input and tolerates surrounding whitespace.",
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/dates.py",
            "    try:\n        return date.fromisoformat(value.strip())\n"
            "    except (AttributeError, ValueError):\n        return None\n",
            "    return date.fromisoformat(value)\n",
        ),
        reference_fix=FileEdit(
            "benchlab/dates.py",
            "    return date.fromisoformat(value)\n",
            "    return date.fromisoformat(value.strip())\n",
        ),
        regression_edit=_regression(
            "tests/test_dates.py",
            124,
            '    from benchlab.dates import parse_date\n\n'
            '    assert parse_date(" 2024-01-02 ").isoformat() == "2024-01-02"',
        ),
        hidden_test=_hidden(
            124,
            '    from benchlab.dates import parse_date\n\n'
            '    assert parse_date("not-a-date") is None',
        ),
        rationale="Trimmed whitespace before parsing and added a regression test.",
        expected_outcome="hidden_failed",
    ),
    BenchCase(
        id="format-date-none",
        issue_number=125,
        title="format_date crashes for missing dates",
        body=_body(
            "dates",
            "format_date",
            "format_date(None)",
            'returns "unknown" for a missing date.',
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/dates.py",
            '    return value.isoformat() if value else "unknown"\n',
            "    return value.isoformat()\n",
        ),
        reference_fix=FileEdit(
            "benchlab/dates.py",
            "    return value.isoformat()\n",
            '    return value.isoformat() if value else "unknown"\n',
        ),
        regression_edit=_regression(
            "tests/test_dates.py",
            125,
            '    from benchlab.dates import format_date\n\n'
            '    assert format_date(None) == "unknown"',
        ),
        hidden_test=_hidden(
            125,
            '    from benchlab.dates import format_date\n\n'
            '    assert format_date(None) == "unknown"',
        ),
        rationale="Restored the missing-date branch and covered it with a regression test.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="days-between-symmetric",
        issue_number=126,
        title="days_between returns a negative value for reversed dates",
        body=_body(
            "dates",
            "days_between",
            "days_between(date(2024, 1, 3), date(2024, 1, 1))",
            "returns 2 regardless of argument order.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/dates.py",
            "    return abs((second - first).days)\n",
            "    return (second - first).days\n",
        ),
        reference_fix=FileEdit(
            "benchlab/dates.py",
            "    return (second - first).days\n",
            "    return abs((second - first).days)\n",
        ),
        regression_edit=_regression(
            "tests/test_dates.py",
            126,
            '    from datetime import date\n'
            '    from benchlab.dates import days_between\n\n'
            '    assert days_between(date(2024, 1, 3), date(2024, 1, 1)) == 2',
        ),
        hidden_test=_hidden(
            126,
            '    from datetime import date\n'
            '    from benchlab.dates import days_between\n\n'
            '    assert days_between(date(2024, 1, 1), date(2024, 1, 1)) == 0',
        ),
        rationale="Restored the absolute day difference.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="weekend-saturday",
        issue_number=127,
        title="is_weekend misses Saturday",
        body=_body(
            "dates",
            "is_weekend",
            "is_weekend(date(2024, 1, 6))",
            "returns True for Saturday.",
        ),
        difficulty="easy",
        mutation=FileEdit(
            "benchlab/dates.py",
            "    return value.weekday() >= 5\n",
            "    return value.weekday() > 5\n",
        ),
        reference_fix=FileEdit(
            "benchlab/dates.py",
            "    return value.weekday() > 5\n",
            "    return value.weekday() >= 5\n",
        ),
        regression_edit=_regression(
            "tests/test_dates.py",
            127,
            '    from datetime import date\n'
            '    from benchlab.dates import is_weekend\n\n'
            '    assert is_weekend(date(2024, 1, 6)) is True',
        ),
        hidden_test=_hidden(
            127,
            '    from datetime import date\n'
            '    from benchlab.dates import is_weekend\n\n'
            '    assert is_weekend(date(2024, 1, 8)) is False',
        ),
        rationale="Included Saturday in the weekend check.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="next-weekday-friday",
        issue_number=128,
        title="next_weekday returns Saturday for Friday input",
        body=_body(
            "dates",
            "next_weekday",
            "next_weekday(date(2024, 1, 5))",
            "returns Monday, skipping both weekend days.",
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/dates.py",
            "    while candidate.weekday() >= 5:\n",
            "    while candidate.weekday() == 6:\n",
        ),
        reference_fix=FileEdit(
            "benchlab/dates.py",
            "    while candidate.weekday() == 6:\n",
            "    while candidate.weekday() >= 5:\n",
        ),
        regression_edit=_regression(
            "tests/test_dates.py",
            128,
            '    from datetime import date\n'
            '    from benchlab.dates import next_weekday\n\n'
            '    assert next_weekday(date(2024, 1, 5)) == date(2024, 1, 8)',
        ),
        hidden_test=_hidden(
            128,
            '    from datetime import date\n'
            '    from benchlab.dates import next_weekday\n\n'
            '    assert next_weekday(date(2024, 1, 3)) == date(2024, 1, 4)',
        ),
        rationale="Skipped both Saturday and Sunday when finding the next weekday.",
        expected_outcome="verified",
    ),
    BenchCase(
        id="month-length-february",
        issue_number=129,
        title="month_length assumes February always has 29 days",
        body=_body(
            "dates",
            "month_length",
            "month_length(2023, 2)",
            "returns the calendar length of the month, including 28 for non-leap February.",
        ),
        difficulty="hard",
        mutation=FileEdit(
            "benchlab/dates.py",
            "    return calendar.monthrange(year, month)[1]\n",
            "    return 30 if month in (4, 6, 9, 11) else 31\n",
        ),
        reference_fix=FileEdit(
            "benchlab/dates.py",
            "    return 30 if month in (4, 6, 9, 11) else 31\n",
            "    return 29 if month == 2 else (30 if month in (4, 6, 9, 11) else 31)\n",
        ),
        regression_edit=_regression(
            "tests/test_dates.py",
            129,
            '    from benchlab.dates import month_length\n\n'
            '    assert month_length(2024, 2) == 29',
        ),
        hidden_test=_hidden(
            129,
            '    from benchlab.dates import month_length\n\n'
            '    assert month_length(2023, 2) == 28',
        ),
        rationale="Added a February branch with a leap-year length.",
        expected_outcome="hidden_failed",
    ),
    BenchCase(
        id="age-before-birthday",
        issue_number=130,
        title="age_on overcounts before the birthday",
        body=_body(
            "dates",
            "age_on",
            "age_on(date(2000, 6, 1), date(2020, 1, 1))",
            "returns 19 until the birthday has occurred.",
        ),
        difficulty="medium",
        mutation=FileEdit(
            "benchlab/dates.py",
            "    years = on.year - birth.year\n"
            "    if (on.month, on.day) < (birth.month, birth.day):\n"
            "        years -= 1\n"
            "    return years\n",
            "    return on.year - birth.year\n",
        ),
        reference_fix=FileEdit(
            "benchlab/dates.py",
            "    return on.year - birth.year\n",
            "    years = on.year - birth.year\n"
            "    if (on.month, on.day) < (birth.month, birth.day):\n"
            "        years -= 1\n"
            "    return years\n",
        ),
        regression_edit=_regression(
            "tests/test_dates.py",
            130,
            '    from datetime import date\n'
            '    from benchlab.dates import age_on\n\n'
            '    assert age_on(date(2000, 6, 1), date(2020, 1, 1)) == 19',
        ),
        hidden_test=_hidden(
            130,
            '    from datetime import date\n'
            '    from benchlab.dates import age_on\n\n'
            '    assert age_on(date(2000, 6, 1), date(2020, 6, 1)) == 20',
        ),
        rationale="Adjusted age when the birthday has not yet occurred this year.",
        expected_outcome="verified",
    ),
)


CASES_BY_ISSUE: dict[int, BenchCase] = {case.issue_number: case for case in CASES}


def get_case(issue_number: int) -> BenchCase | None:
    return CASES_BY_ISSUE.get(issue_number)
