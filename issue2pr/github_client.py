from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Protocol

from .github_app import (
    GithubAppTokenSource,
    HttpResponse,
    Transport,
    UrllibTransport,
)
from .models import PullRequestRef
from .safety import redact


class GitHubClient(Protocol):
    def create_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...],
    ) -> PullRequestRef: ...

    def comment(self, repo: str, issue_number: int, body: str) -> None: ...


class TokenProvider(Protocol):
    def token(self) -> str: ...


class FakeGitHubClient:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self._pr_numbers: dict[str, int] = {}
        self._prs: dict[tuple[str, int], dict[str, Any]] = {}
        self._comments: list[dict[str, Any]] = []

    def _repo_dir(self, repo: str) -> Path:
        path = self.state_dir / "fake-github" / repo.replace("/", "__")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...],
    ) -> PullRequestRef:
        number = self._pr_numbers.get(repo, 100) + 1
        self._pr_numbers[repo] = number
        safe_body = redact(body)
        record = {
            "repo": repo,
            "number": number,
            "head": head,
            "base": base,
            "title": redact(title),
            "body": safe_body,
            "labels": list(labels),
            "url": f"https://local.issue2pr.invalid/{repo}/pull/{number}",
        }
        self._prs[(repo, number)] = record
        path = self._repo_dir(repo) / f"pr-{number}.json"
        path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return PullRequestRef(number, str(record["url"]))

    def comment(self, repo: str, issue_number: int, body: str) -> None:
        safe_body = redact(body)
        record = {
            "repo": repo,
            "issue_number": issue_number,
            "body": safe_body,
        }
        self._comments.append(record)
        path = self._repo_dir(repo) / f"comment-{len(self._comments)}.json"
        path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def pull_requests(self, repo: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            record
            for (record_repo, _), record in sorted(self._prs.items())
            if record_repo == repo
        )

    def comments(self, repo: str) -> tuple[dict[str, Any], ...]:
        return tuple(item for item in self._comments if item["repo"] == repo)


class HttpGitHubClient:
    def __init__(
        self,
        token_provider: TokenProvider,
        transport: Transport | None = None,
        *,
        base_url: str = "https://api.github.com",
    ) -> None:
        self.token_provider = token_provider
        self.transport = transport or UrllibTransport()
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "Authorization": f"Bearer {self.token_provider.token()}",
        }

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any],
        expected: tuple[int, ...],
    ) -> dict[str, Any]:
        response: HttpResponse = self.transport.request(
            method, f"{self.base_url}{path}", self._headers(), body
        )
        if response.status not in expected:
            raise RuntimeError(
                f"GitHub API returned {response.status}: "
                f"{redact(str(response.data))[:500]}"
            )
        return response.data

    def create_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...],
    ) -> PullRequestRef:
        data = self._request(
            "POST",
            f"/repos/{repo}/pulls",
            {
                "title": redact(title),
                "head": head,
                "base": base,
                "body": redact(body),
            },
            (200, 201),
        )
        number = int(data.get("number", 0))
        if number <= 0:
            raise RuntimeError("GitHub PR response did not contain a number")
        if labels:
            self._request(
                "POST",
                f"/repos/{repo}/issues/{number}/labels",
                {"labels": list(labels)},
                (200, 201),
            )
        return PullRequestRef(number, str(data.get("html_url", "")))

    def comment(self, repo: str, issue_number: int, body: str) -> None:
        self._request(
            "POST",
            f"/repos/{repo}/issues/{issue_number}/comments",
            {"body": redact(body)},
            (200, 201),
        )


class RepoScopedGithubClient:
    """Real GitHub client that mints a repo-scoped token per repository."""

    def __init__(
        self,
        app_id: int,
        private_key_pem: str,
        installation_id: int,
        *,
        transport: Transport | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self.app_id = app_id
        self._private_key_pem = private_key_pem
        self.installation_id = installation_id
        self.transport = transport or UrllibTransport()
        self.base_url = base_url
        self._clients: dict[str, HttpGitHubClient] = {}

    def _client(self, repo: str) -> HttpGitHubClient:
        client = self._clients.get(repo)
        if client is None:
            source = GithubAppTokenSource(
                self.app_id,
                self._private_key_pem,
                self.installation_id,
                repo,
                self.transport,
                base_url=self.base_url,
            )
            client = HttpGitHubClient(
                source, self.transport, base_url=self.base_url
            )
            self._clients[repo] = client
        return client

    def create_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: tuple[str, ...],
    ) -> PullRequestRef:
        return self._client(repo).create_pull_request(
            repo, head, base, title, body, labels
        )

    def comment(self, repo: str, issue_number: int, body: str) -> None:
        self._client(repo).comment(repo, issue_number, body)
