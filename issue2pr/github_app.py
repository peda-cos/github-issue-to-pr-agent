from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

APP_PERMISSIONS: dict[str, str] = {
    "issues": "write",
    "pull_requests": "write",
    "contents": "write",
    "workflows": "read",
}
ALLOWED_RESPONSE_PERMISSIONS: dict[str, str] = {
    **APP_PERMISSIONS,
    "metadata": "read",
}


class TokenScopeError(RuntimeError):
    pass


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    data: dict[str, Any]


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse: ...


class UrllibTransport:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method=method
        )
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                return HttpResponse(status=response.status, data=data)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"error": raw[:1000]}
            return HttpResponse(status=exc.code, data=data)
        except urllib.error.URLError as exc:
            raise TransportError(f"GitHub request failed: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise TransportError(f"GitHub request failed: {type(exc).__name__}") from exc


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_app_jwt(
    app_id: int, private_key_pem: str, now: datetime | None = None
) -> str:
    moment = now or datetime.now(UTC)
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iat": int(moment.timestamp()) - 60,
        "exp": int(moment.timestamp()) + 540,
        "iss": str(app_id),
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("GitHub App private key must be an RSA key")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"


def _validate_scope(permissions: Mapping[str, Any]) -> None:
    for name, level in permissions.items():
        if not isinstance(level, str):
            raise TokenScopeError(f"invalid permission level for {name}")
        if name == "administration":
            raise TokenScopeError("administration permission is not allowed")
        if name == "workflows" and level != "read":
            raise TokenScopeError("workflows permission must be read-only")
        allowed = ALLOWED_RESPONSE_PERMISSIONS.get(name)
        if level == "write" and allowed != "write":
            raise TokenScopeError(f"unexpected write permission: {name}")
        if level not in ("read", "write"):
            raise TokenScopeError(f"invalid permission level for {name}: {level}")


class GithubAppTokenSource:
    def __init__(
        self,
        app_id: int,
        private_key_pem: str,
        installation_id: int,
        repo_name: str,
        transport: Transport,
        *,
        base_url: str = "https://api.github.com",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.app_id = app_id
        self.private_key_pem = private_key_pem
        self.installation_id = installation_id
        self.repo_name = repo_name
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self._now = now or (lambda: datetime.now(UTC))
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def __repr__(self) -> str:
        return (
            f"GithubAppTokenSource(app_id={self.app_id}, "
            f"installation_id={self.installation_id}, repo_name={self.repo_name!r})"
        )

    def token(self) -> str:
        now = self._now()
        if (
            self._token is not None
            and self._expires_at is not None
            and now < self._expires_at - timedelta(seconds=60)
        ):
            return self._token
        jwt = make_app_jwt(self.app_id, self.private_key_pem, now)
        url = (
            f"{self.base_url}/app/installations/{self.installation_id}/access_tokens"
        )
        response = self.transport.request(
            "POST",
            url,
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "Authorization": f"Bearer {jwt}",
            },
            {
                "repositories": [self.repo_name.split("/")[-1]],
                "permissions": APP_PERMISSIONS,
            },
        )
        if response.status not in (200, 201):
            raise TransportError(
                f"GitHub App token exchange failed with status {response.status}"
            )
        token = response.data.get("token")
        expires_raw = response.data.get("expires_at")
        permissions = response.data.get("permissions")
        if not isinstance(token, str) or not token:
            raise TokenScopeError("GitHub App token response did not contain a token")
        if not isinstance(expires_raw, str):
            raise TokenScopeError("GitHub App token response did not contain expiry")
        if not isinstance(permissions, dict):
            raise TokenScopeError("GitHub App token response did not contain permissions")
        _validate_scope(permissions)
        try:
            expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TokenScopeError("GitHub App token expiry was invalid") from exc
        self._token = token
        self._expires_at = expires_at
        return token
