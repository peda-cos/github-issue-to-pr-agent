from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


class EnvironmentInferenceError(ValueError):
    pass


@dataclass(frozen=True)
class RepoEnvironment:
    language: str
    package_manager: str
    test_command: tuple[str, ...]
    coverage_supported: bool
    dockerfile: str | None
    evidence: tuple[str, ...]


def _python_dockerfile(repo: Path, install_project: bool) -> str:
    install = ""
    if install_project:
        install = "RUN python -m pip install -e .\n"
    return (
        "FROM python:3.14-slim\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1\n"
        "WORKDIR /workspace\n"
        "COPY . .\n"
        "RUN python -m pip install --upgrade pip\n"
        f"{install}"
        "RUN python -m pip install pytest coverage\n"
        'CMD ["python", "-m", "pytest", "-q"]\n'
    )


def _node_dockerfile(package_manager: str) -> str:
    install = {
        "pnpm": "RUN corepack enable && pnpm install --frozen-lockfile",
        "yarn": "RUN corepack enable && yarn install --immutable",
        "bun": "RUN corepack enable && bun install --frozen-lockfile",
        "npm": "RUN npm ci",
    }[package_manager]
    runner = "pnpm" if package_manager == "pnpm" else package_manager
    return (
        "FROM node:26-slim\n"
        "WORKDIR /workspace\n"
        "COPY . .\n"
        f"{install}\n"
        f'CMD ["{runner}", "test"]\n'
    )


def _has_python_project(repo: Path) -> bool:
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return True
        return isinstance(data.get("project"), dict)
    return (repo / "setup.py").is_file() or (repo / "setup.cfg").is_file()


def infer(repo: Path) -> RepoEnvironment:
    root = repo.resolve()
    if not root.is_dir():
        raise EnvironmentInferenceError(f"not a directory: {repo}")
    evidence: list[str] = []
    existing_dockerfile = root / "Dockerfile"
    if existing_dockerfile.is_file():
        evidence.append("Dockerfile")

    python_markers = (
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "tox.ini",
    )
    has_python_top_level = any(
        p.suffix == ".py" for p in root.iterdir() if p.is_file()
    )
    other_language_project = any(
        (root / name).is_file() for name in ("package.json", "go.mod", "Cargo.toml")
    )
    has_python_nested = any(
        path.suffix == ".py"
        for path in root.rglob("*.py")
        if not any(
            part in {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
            for part in path.parts
        )
    )
    if (
        any((root / name).is_file() for name in python_markers)
        or has_python_top_level
        or (has_python_nested and not other_language_project)
    ):
        if (root / "uv.lock").is_file():
            package_manager = "uv"
        elif (root / "poetry.lock").is_file():
            package_manager = "poetry"
        elif (root / "requirements.txt").is_file():
            package_manager = "pip"
        else:
            package_manager = "pip"
        evidence.append("python")
        dockerfile = None if existing_dockerfile.is_file() else _python_dockerfile(
            root, _has_python_project(root)
        )
        return RepoEnvironment(
            language="python",
            package_manager=package_manager,
            test_command=(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
            ),
            coverage_supported=True,
            dockerfile=dockerfile,
            evidence=tuple(evidence),
        )

    package_json = root / "package.json"
    if package_json.is_file():
        if (root / "pnpm-lock.yaml").is_file():
            package_manager = "pnpm"
        elif (root / "bun.lock").is_file() or (root / "bun.lockb").is_file():
            package_manager = "bun"
        elif (root / "yarn.lock").is_file():
            package_manager = "yarn"
        else:
            package_manager = "npm"
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EnvironmentInferenceError("package.json is invalid") from exc
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if not isinstance(scripts, dict) or "test" not in scripts:
            raise EnvironmentInferenceError("package.json has no test script")
        evidence.extend(["node", package_manager])
        return RepoEnvironment(
            language="node",
            package_manager=package_manager,
            test_command=(package_manager, "test"),
            coverage_supported=False,
            dockerfile=None
            if existing_dockerfile.is_file()
            else _node_dockerfile(package_manager),
            evidence=tuple(evidence),
        )

    if (root / "go.mod").is_file():
        evidence.extend(["go", "gomod"])
        dockerfile = None if existing_dockerfile.is_file() else (
            "FROM golang:1.26-slim\n"
            "WORKDIR /workspace\n"
            "COPY . .\n"
            "RUN go mod download\n"
            'CMD ["go", "test", "./..."]\n'
        )
        return RepoEnvironment(
            language="go",
            package_manager="gomod",
            test_command=("go", "test", "./..."),
            coverage_supported=False,
            dockerfile=dockerfile,
            evidence=tuple(evidence),
        )

    if (root / "Cargo.toml").is_file():
        evidence.extend(["rust", "cargo"])
        dockerfile = None if existing_dockerfile.is_file() else (
            "FROM rust:1.91-slim\n"
            "WORKDIR /workspace\n"
            "COPY . .\n"
            "RUN cargo fetch\n"
            'CMD ["cargo", "test", "--quiet"]\n'
        )
        return RepoEnvironment(
            language="rust",
            package_manager="cargo",
            test_command=("cargo", "test", "--quiet"),
            coverage_supported=False,
            dockerfile=dockerfile,
            evidence=tuple(evidence),
        )

    raise EnvironmentInferenceError("unsupported or unrecognized repository")


_SECRET_KEY = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY", re.IGNORECASE)
_SECRET_PREFIXES = ("ISSUE2PR_", "ANTHROPIC_", "OPENAI_", "AWS_")
_PRESERVE = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR")


def build_command_env(
    extra_secret_names: Iterable[str] = (),
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if base is None else base
    extra = {name.upper() for name in extra_secret_names}
    clean: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper in extra or _SECRET_KEY.search(key) or upper.startswith(
            _SECRET_PREFIXES
        ):
            continue
        if upper in _PRESERVE or upper == "PYTHONPATH":
            clean[key] = value
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    return clean
