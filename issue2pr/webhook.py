from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from .config import Settings
from .models import IssueTask
from .store import StateStore

ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_COMMAND = re.compile(r"(?i)(?<![A-Za-z0-9_])@agent\s+(fix\s+this|retry|status)\b")


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)


def _lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _issue_numbers(payload: Mapping[str, Any]) -> tuple[dict[str, Any], int] | None:
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        return None
    try:
        number = int(issue["number"])
    except (KeyError, TypeError, ValueError):
        return None
    return issue, number


def _base_task(
    payload: Mapping[str, Any],
    settings: Settings,
    delivery_id: str,
    issue: Mapping[str, Any],
    number: int,
    kind: str,
) -> IssueTask:
    repository = payload.get("repository") or {}
    repo = str(repository.get("full_name", ""))
    installation = payload.get("installation") or {}
    labels = tuple(
        str(label.get("name", ""))
        for label in issue.get("labels", [])
        if isinstance(label, dict)
    )
    return IssueTask(
        delivery_id=delivery_id,
        repo=repo,
        issue_number=number,
        title=str(issue.get("title", "")),
        body=str(issue.get("body") or ""),
        labels=labels,
        installation_id=int(installation.get("id", 0) or 0),
        base_branch=str(repository.get("default_branch") or "main"),
        is_pull_request=bool(issue.get("pull_request")),
        kind="status" if kind == "status" else "fix",
    )


def parse_event(
    event: str,
    payload: Mapping[str, Any],
    delivery_id: str,
    settings: Settings,
) -> IssueTask | None:
    if not delivery_id:
        raise ValueError("X-GitHub-Delivery is required")
    repository = payload.get("repository") or {}
    repo = str(repository.get("full_name", ""))
    if settings.allowed_repos and repo not in settings.allowed_repos:
        return None
    action = str(payload.get("action", ""))
    if event == "issues" and action == "labeled":
        label = payload.get("label") or {}
        if label.get("name") != settings.trigger_label:
            return None
        numbered = _issue_numbers(payload)
        if numbered is None:
            return None
        issue, number = numbered
        if issue.get("pull_request"):
            return None
        return _base_task(payload, settings, delivery_id, issue, number, "fix")
    if event == "issue_comment" and action == "created":
        comment = payload.get("comment") or {}
        sender = comment.get("user") or payload.get("sender") or {}
        if str(sender.get("type", "")) == "Bot" or str(sender.get("login", "")).endswith(
            "[bot]"
        ):
            return None
        if str(comment.get("author_association", "")) not in ASSOCIATIONS:
            return None
        match = _COMMAND.search(str(comment.get("body") or ""))
        if match is None:
            return None
        numbered = _issue_numbers(payload)
        if numbered is None:
            return None
        issue, number = numbered
        command = match.group(1).lower()
        kind = "status" if command == "status" else "fix"
        return _base_task(payload, settings, delivery_id, issue, number, kind)
    return None


def handle_webhook(
    headers: Mapping[str, str],
    body: bytes,
    store: StateStore,
    settings: Settings,
) -> tuple[int, str]:
    if not settings.webhook_secret:
        return 503, "webhook secret not configured"
    lowered = _lower_headers(headers)
    if not verify_signature(
        settings.webhook_secret, body, lowered.get("x-hub-signature-256")
    ):
        return 401, "invalid signature"
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, "invalid JSON"
    if not isinstance(payload, dict):
        return 400, "invalid payload"
    event = lowered.get("x-github-event", "")
    delivery_id = lowered.get("x-github-delivery", "")
    if not event or not delivery_id:
        return 400, "missing event headers"
    task = parse_event(event, payload, delivery_id, settings)
    if task is None:
        return 202, "ignored"
    now = datetime.now(UTC).isoformat()
    enqueued = store.enqueue(task, now)
    return 202, "enqueued" if enqueued else "duplicate"


def lambda_handler(event: Mapping[str, Any], context: object = None) -> dict[str, Any]:
    del context
    settings = Settings.from_env()
    settings.ensure_dirs()
    store = StateStore(settings.state_dir / "state.db")
    headers = event.get("headers") or {}
    raw_body = event.get("body") or ""
    if isinstance(raw_body, str):
        body = (
            base64.b64decode(raw_body)
            if event.get("isBase64Encoded")
            else raw_body.encode("utf-8")
        )
    else:
        body = bytes(raw_body)
    status, text = handle_webhook(headers, body, store, settings)
    return {"statusCode": status, "body": text}


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8787) -> None:
    if not settings.webhook_secret:
        raise RuntimeError("ISSUE2PR_WEBHOOK_SECRET is required to serve webhooks")
    settings.ensure_dirs()
    store = StateStore(settings.state_dir / "state.db")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, text: str) -> None:
            encoded = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send(200, "ok")
            else:
                self._send(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/webhook":
                self._send(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, "invalid content length")
                return
            if length <= 0 or length > 5 * 1024 * 1024:
                self._send(400, "invalid body length")
                return
            body = self.rfile.read(length)
            status, text = handle_webhook(dict(self.headers.items()), body, store, settings)
            self._send(status, text)

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"webhook: {fmt % args}", file=sys.stderr)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"issue2pr webhook listening on http://{host}:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    finally:
        server.server_close()
