from __future__ import annotations

import sys
from pathlib import Path

import pytest

from issue2pr.inference import (
    EnvironmentInferenceError,
    build_command_env,
    infer,
)


def test_python_uv_environment_and_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    env = infer(tmp_path)
    assert env.language == "python"
    assert env.package_manager == "uv"
    assert env.coverage_supported
    assert env.test_command[:3] == (sys.executable, "-m", "pytest")
    assert env.dockerfile is not None
    assert "FROM python:3.14-slim" in env.dockerfile
    assert "pip install -e ." in env.dockerfile
    assert "pytest coverage" in env.dockerfile


def test_existing_dockerfile_is_not_synthesized(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    env = infer(tmp_path)
    assert env.dockerfile is None
    assert "Dockerfile" in env.evidence


@pytest.mark.parametrize(
    ("files", "language", "package_manager"),
    [
        (
            {
                "package.json": '{"scripts":{"test":"vitest"}}',
                "pnpm-lock.yaml": "",
            },
            "node",
            "pnpm",
        ),
        ({"go.mod": "module x\n"}, "go", "gomod"),
        ({"Cargo.toml": "[package]\nname='x'\n"}, "rust", "cargo"),
    ],
)
def test_other_languages(
    tmp_path: Path, files: dict[str, str], language: str, package_manager: str
) -> None:
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    env = infer(tmp_path)
    assert env.language == language
    assert env.package_manager == package_manager
    assert not env.coverage_supported
    assert env.test_command


def test_node_without_test_script_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
    with pytest.raises(EnvironmentInferenceError):
        infer(tmp_path)


def test_unknown_repository_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentInferenceError):
        infer(tmp_path)


def test_command_environment_strips_secrets() -> None:
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/test",
        "ANTHROPIC_API_KEY": "secret-value",
        "GITHUB_TOKEN": "ghp_test_only",
        "ISSUE2PR_WEBHOOK_SECRET": "secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "SAFE_SETTING": "kept-out",
    }
    env = build_command_env(base=base)
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/test"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "ANTHROPIC_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "ISSUE2PR_WEBHOOK_SECRET" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "SAFE_SETTING" not in env
