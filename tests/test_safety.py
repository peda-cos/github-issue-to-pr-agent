from __future__ import annotations

from pathlib import Path

import pytest

from issue2pr.safety import (
    UnsafePathError,
    UnsafePushError,
    assert_safe_push,
    changed_paths_allowed,
    find_secrets,
    is_protected_path,
    redact,
    redact_structured,
    resolve_inside,
    scan_for_secrets,
)


def test_resolve_inside_blocks_traversal_absolute_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (root / "link").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        resolve_inside(root, "../outside.txt")
    with pytest.raises(UnsafePathError):
        resolve_inside(root, outside)
    with pytest.raises(UnsafePathError):
        resolve_inside(root, "link")
    with pytest.raises(UnsafePathError):
        resolve_inside(root, ".git/config")
    assert resolve_inside(root, "nested/file.txt") == root / "nested" / "file.txt"


def test_protected_workflow_paths() -> None:
    assert is_protected_path(".github/workflows/ci.yml")
    assert is_protected_path("./.github/workflows/release.yml")
    assert not is_protected_path(".github/dependabot.yml")
    with pytest.raises(UnsafePathError):
        changed_paths_allowed(["src/app.py", ".github/workflows/ci.yml"])


def test_secret_redaction_and_scan() -> None:
    text = (
        "token ghp_test_only and sk-ant-testkey\n"
        "Authorization: Bearer abcdef\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
    )
    assert scan_for_secrets(text)
    assert find_secrets("ghp_test_only")
    redacted = redact(text)
    assert "ghp_test_only" not in redacted
    assert "sk-ant-testkey" not in redacted
    assert "[REDACTED]" in redacted
    structured = redact_structured(
        {"token": "ghp_test_only", "nested": {"api_key": "secret"}, "safe": "ok"}
    )
    assert structured["token"] == "[REDACTED]"
    assert structured["nested"]["api_key"] == "[REDACTED]"
    assert structured["safe"] == "ok"


def test_push_guards() -> None:
    assert_safe_push("agent/issue-1-abc", "main")
    with pytest.raises(UnsafePushError):
        assert_safe_push("main", "main")
    with pytest.raises(UnsafePushError):
        assert_safe_push("feature/x", "main")
    with pytest.raises(UnsafePushError):
        assert_safe_push("agent/x", "main", ["--force"])
    with pytest.raises(UnsafePushError):
        assert_safe_push("agent/x", "main", ["+refs/heads/x"])
