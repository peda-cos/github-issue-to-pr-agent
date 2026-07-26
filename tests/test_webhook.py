from __future__ import annotations

from pathlib import Path

from issue2pr.config import Settings
from issue2pr.store import StateStore
from issue2pr.webhook import handle_webhook, parse_event, verify_signature

from conftest import encode, issue_payload, make_task, sign


def test_github_documented_signature_vector() -> None:
    secret = "It's a Secret to Everybody"
    payload = b"Hello, World!"
    expected = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert verify_signature(secret, payload, expected)
    assert not verify_signature(secret, payload + b"tampered", expected)
    assert not verify_signature(secret, payload, None)


def test_signed_label_delivery_is_idempotent(settings: Settings) -> None:
    store = StateStore(settings.state_dir / "state.db")
    body = encode(issue_payload())
    headers = sign(
        settings.webhook_secret or "", body, "issues", "delivery-known"
    )
    assert handle_webhook(headers, body, store, settings) == (202, "enqueued")
    assert handle_webhook(headers, body, store, settings) == (202, "duplicate")
    task = store.claim_next("2026-01-01T00:00:00+00:00")
    assert task is not None
    assert task.repo == "acme/widget"
    assert task.issue_number == 42
    assert task.base_branch == "main"
    assert task.kind == "fix"


def test_unsigned_and_malformed_deliveries_are_rejected(settings: Settings) -> None:
    store = StateStore(settings.state_dir / "state.db")
    body = encode(issue_payload())
    assert handle_webhook({}, body, store, settings)[0] == 401
    assert handle_webhook(
        {
            "x-hub-signature-256": "sha256=" + "0" * 64,
            "x-github-event": "issues",
            "x-github-delivery": "d",
        },
        body,
        store,
        settings,
    )[0] == 401
    good = sign(settings.webhook_secret or "", b"{", "issues", "bad-json")
    assert handle_webhook(good, b"{", store, settings) == (400, "invalid JSON")


def test_comment_authorization_and_retry_status(settings: Settings) -> None:
    body = encode(issue_payload(action="created", body="@agent retry"))
    delivery = sign(settings.webhook_secret or "", body, "issue_comment", "c1")
    raw = issue_payload(action="created", body="@agent retry")
    task = parse_event("issue_comment", raw, "c1", settings)
    assert task is not None and task.kind == "fix"
    ignored = issue_payload(
        action="created", body="@agent retry", author_association="NONE"
    )
    assert parse_event("issue_comment", ignored, "c2", settings) is None
    bot = issue_payload(action="created", body="@agent retry", sender_type="Bot")
    assert parse_event("issue_comment", bot, "c3", settings) is None
    status = parse_event(
        "issue_comment",
        issue_payload(action="created", body="@agent status"),
        "c4",
        settings,
    )
    assert status is not None and status.kind == "status"
    assert delivery["x-github-event"] == "issue_comment"


def test_pr_comment_and_unrelated_labels(settings: Settings) -> None:
    pr_payload = issue_payload(
        action="created", body="@agent fix this", is_pull_request=True
    )
    task = parse_event("issue_comment", pr_payload, "pr1", settings)
    assert task is not None
    assert task.is_pull_request is True
    assert task.base_branch == "main"
    assert (
        parse_event(
            "issues",
            issue_payload(label="bug"),
            "label1",
            settings,
        )
        is None
    )
    assert (
        parse_event(
            "issue_comment",
            issue_payload(action="created", body="hello"),
            "comment1",
            settings,
        )
        is None
    )


def test_repository_allowlist_and_missing_secret(tmp_path: Path) -> None:
    restricted = Settings(
        state_dir=tmp_path / "restricted",
        webhook_secret="secret",
        allowed_repos=frozenset({"allowed/repo"}),
    )
    assert parse_event(
        "issues", issue_payload(), "d", restricted
    ) is None
    store = StateStore(tmp_path / "restricted" / "state.db")
    no_secret = Settings(state_dir=tmp_path / "no-secret")
    assert handle_webhook({}, b"{}", store, no_secret)[0] == 503
    task = make_task(delivery_id="d")
    assert task.repo == "acme/widget"
