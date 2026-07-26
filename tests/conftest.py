from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from issue2pr.config import Settings
from issue2pr.models import IssueTask


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    value = Settings(
        state_dir=tmp_path / "state",
        webhook_secret="test-webhook-secret",
        max_turns=10,
        max_wall_seconds=60,
        max_cost_per_task_usd=20.0,
        max_daily_cost_per_repo_usd=100.0,
        max_prs_per_repo_per_day=5,
        test_timeout_seconds=60,
    )
    value.ensure_dirs()
    return value


def make_task(
    *,
    delivery_id: str = "delivery-1",
    repo: str = "acme/widget",
    issue_number: int = 42,
    kind: str = "fix",
    base_branch: str = "main",
) -> IssueTask:
    return IssueTask(
        delivery_id=delivery_id,
        repo=repo,
        issue_number=issue_number,
        title="Fix the widget",
        body="The widget is broken.",
        labels=("bug",),
        installation_id=7,
        base_branch=base_branch,
        kind=kind,  # type: ignore[arg-type]
    )


def issue_payload(
    *,
    action: str = "labeled",
    label: str = "@agent fix this",
    repo: str = "acme/widget",
    number: int = 42,
    is_pull_request: bool = False,
    body: str = "",
    author_association: str = "MEMBER",
    sender_type: str = "User",
    base_branch: str = "main",
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "number": number,
        "title": "Fix the widget",
        "body": "The widget is broken.",
        "labels": [{"name": "bug"}],
    }
    if is_pull_request:
        issue["pull_request"] = {"url": "local"}
    payload: dict[str, Any] = {
        "action": action,
        "label": {"name": label},
        "issue": issue,
        "repository": {"full_name": repo, "default_branch": base_branch},
        "installation": {"id": 7},
        "sender": {"type": sender_type, "login": "maintainer"},
    }
    if action == "created":
        payload["comment"] = {
            "body": body,
            "author_association": author_association,
            "user": {"type": sender_type, "login": "maintainer"},
        }
    return payload


def sign(secret: str, body: bytes, event: str, delivery: str) -> dict[str, str]:
    import hashlib
    import hmac

    return {
        "x-hub-signature-256": "sha256="
        + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        "x-github-event": event,
        "x-github-delivery": delivery,
    }


def encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")
