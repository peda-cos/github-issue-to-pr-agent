from __future__ import annotations

import re


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def initials(name: str) -> str:
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    return "".join(part[0].upper() for part in parts)


def truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def title_case(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in value.split())


def word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_'-]+", value))


def split_tags(value: str) -> list[str]:
    return [
        part.strip().lower()
        for part in re.split(r"[,;]", value)
        if part.strip()
    ]


def camel_to_snake(name: str) -> str:
    step = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step).lower()
