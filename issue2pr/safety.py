from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{6,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{6,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{4,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bAuthorization:\s*Bearer\s+[^\s\"']+"),
)
_SENSITIVE_KEYS = {
    "token",
    "secret",
    "password",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
}


class UnsafePathError(ValueError):
    pass


class UnsafePushError(ValueError):
    pass


def resolve_inside(root: Path, rel: str | Path) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute():
        raise UnsafePathError(f"absolute path is not allowed: {rel}")
    if any(part == ".." for part in candidate.parts):
        raise UnsafePathError(f"parent traversal is not allowed: {rel}")
    if any(part == ".git" for part in candidate.parts):
        raise UnsafePathError(".git access is not allowed")
    root_resolved = root.resolve()
    target = (root_resolved / candidate).resolve()
    if not target.is_relative_to(root_resolved):
        raise UnsafePathError(f"path escapes workspace: {rel}")
    return target


def is_protected_path(path: str | Path) -> bool:
    normalized = PurePosixPath(str(path).replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == ".github/workflows" or normalized.startswith(
        ".github/workflows/"
    )


def changed_paths_allowed(paths: Iterable[str]) -> None:
    for path in paths:
        if is_protected_path(path):
            raise UnsafePathError(
                f"agent may not modify protected path: {path}"
            )


def find_secrets(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for pattern in _SECRET_PATTERNS:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return tuple(found)


def scan_for_secrets(text: str) -> bool:
    return bool(find_secrets(text))


def redact(text: str) -> str:
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def redact_structured(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                output[key_text] = "[REDACTED]"
            else:
                output[key_text] = redact_structured(item)
        return output
    if isinstance(value, list):
        return [redact_structured(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_structured(item) for item in value)
    return value


@dataclass(frozen=True)
class GitPushResult:
    ok: bool
    output: str


def assert_safe_push(
    branch: str, base_branch: str, argv: Iterable[str] = ()
) -> None:
    if not branch.startswith("agent/"):
        raise UnsafePushError("only agent/* branches may be pushed")
    if branch == base_branch:
        raise UnsafePushError("refusing to push to the base branch")
    for arg in argv:
        lowered = arg.lower()
        if lowered in ("--force", "-f") or lowered.startswith("--force-with-lease"):
            raise UnsafePushError("force push is forbidden")
        if lowered.startswith("+") or ":" in lowered and lowered.startswith("+"):
            raise UnsafePushError("force refspec is forbidden")


def safe_push(
    repo: Path,
    origin: str,
    branch: str,
    base_branch: str,
    env: Mapping[str, str] | None = None,
) -> GitPushResult:
    assert_safe_push(branch, base_branch)
    try:
        completed = subprocess.run(
            [
                "git",
                "push",
                "--porcelain",
                origin,
                f"HEAD:refs/heads/{branch}",
            ],
            cwd=repo,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitPushResult(False, f"{type(exc).__name__}: push failed")
    output = (completed.stdout + completed.stderr).strip()[-4000:]
    return GitPushResult(completed.returncode == 0, output)
