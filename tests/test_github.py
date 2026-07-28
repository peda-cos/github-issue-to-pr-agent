from __future__ import annotations

import base64
import json
from pathlib import Path
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from issue2pr.github_app import (
    APP_PERMISSIONS,
    HttpResponse,
    GithubAppTokenSource,
    TokenScopeError,
    TransportError,
    make_app_jwt,
)
from issue2pr.github_client import HttpGitHubClient
from issue2pr.models import PullRequestRef


def _keypair() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return key, pem


def _decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_jwt_signature_and_claims() -> None:
    key, pem = _keypair()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = make_app_jwt(123, pem, now)
    header, claims, signature = token.split(".")
    assert json.loads(_decode(header)) == {"alg": "RS256", "typ": "JWT"}
    decoded = json.loads(_decode(claims))
    assert decoded["iss"] == "123"
    assert decoded["exp"] - decoded["iat"] == 600
    key.public_key().verify(
        _decode(signature),
        f"{header}.{claims}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json": dict(json_body or {}),
            }
        )
        return self.response


def test_token_request_is_narrow_scoped_and_cached() -> None:
    _, pem = _keypair()
    expires = datetime.now(UTC) + timedelta(hours=1)
    transport = FakeTransport(
        HttpResponse(
            201,
            {
                "token": "ghs_test_only_value",
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
                "permissions": {**APP_PERMISSIONS, "metadata": "read"},
            },
        )
    )
    source = GithubAppTokenSource(
        99, pem, 7, "acme/widget", transport, now=lambda: datetime.now(UTC)
    )
    assert source.token() == "ghs_test_only_value"
    assert source.token() == "ghs_test_only_value"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/app/installations/7/access_tokens")
    assert request["json"] == {
        "repositories": ["widget"],
        "permissions": APP_PERMISSIONS,
    }
    assert request["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
    assert "ghs_test_only_value" not in repr(source)


def test_overbroad_token_scope_is_rejected() -> None:
    _, pem = _keypair()
    expires = datetime.now(UTC) + timedelta(hours=1)
    transport = FakeTransport(
        HttpResponse(
            201,
            {
                "token": "ghs_test_only_value",
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
                "permissions": {**APP_PERMISSIONS, "workflows": "write"},
            },
        )
    )
    source = GithubAppTokenSource(99, pem, 7, "acme/widget", transport)
    with pytest.raises(TokenScopeError):
        source.token()


def test_http_client_paths_labels_and_comment() -> None:
    transport = FakeTransport(
        HttpResponse(201, {"number": 12, "html_url": "https://example.test/pr/12"})
    )

    class Provider:
        def token(self) -> str:
            return "ghs_test_only_value"

    client = HttpGitHubClient(Provider(), transport)
    ref = client.create_pull_request(
        "acme/widget", "agent/issue-1-x", "main", "title", "body", ("needs-review",)
    )
    assert ref == PullRequestRef(12, "https://example.test/pr/12")
    assert transport.requests[0]["url"].endswith("/repos/acme/widget/pulls")
    assert transport.requests[1]["url"].endswith(
        "/repos/acme/widget/issues/12/labels"
    )
    client.comment("acme/widget", 5, "hello")
    assert transport.requests[2]["url"].endswith(
        "/repos/acme/widget/issues/5/comments"
    )
    assert transport.requests[0]["headers"]["Authorization"].startswith("Bearer ")

    failing = HttpGitHubClient(Provider(), FakeTransport(HttpResponse(422, {"message": "bad"})))
    with pytest.raises((RuntimeError, TransportError)):
        failing.comment("acme/widget", 5, "hello")


def test_app_manifest_and_branch_protection_are_aligned() -> None:
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads(
        (root / ".github" / "issue2pr-app-manifest.json").read_text()
    )
    assert manifest["default_permissions"] == APP_PERMISSIONS
    assert set(manifest["default_events"]) == {"issues", "issue_comment"}
    protection = json.loads(
        (root / "deploy" / "branch-protection.json").read_text()
    )
    assert protection["allow_force_pushes"] is False
    assert protection["allow_deletions"] is False
    assert protection["enforce_admins"] is True
    assert protection["required_status_checks"]["contexts"] == ["verify"]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "verify:" in workflow
