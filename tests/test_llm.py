from __future__ import annotations

from typing import Any, Mapping

import pytest

from issue2pr.agent import PolicyRequest
from issue2pr.github_app import HttpResponse
from issue2pr.llm import LlmPolicy, ProviderError
from issue2pr.models import AgentAction, IssueTask, Usage


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


def request() -> PolicyRequest:
    issue = IssueTask(
        delivery_id="d",
        repo="acme/widget",
        issue_number=1,
        title="Fix widget",
        body="Broken",
    )
    return PolicyRequest(
        issue=issue,
        repo_map="pkg/mod.py: def add",
        step=0,
        history=(),
        remaining_cost_usd=19.0,
        remaining_seconds=1000.0,
    )


def test_anthropic_policy_parses_json_and_cost() -> None:
    text = '```json\n{"action": "read_file", "args": {"path": "README.md"}}\n```'
    fake = FakeTransport(
        HttpResponse(
            200,
            {
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 1000, "output_tokens": 500},
            },
        )
    )
    policy = LlmPolicy(
        "anthropic",
        "claude-test",
        "sk-ant-testkey",
        price_input_per_mtok=3.0,
        price_output_per_mtok=15.0,
        transport=fake,
    )
    action, usage = policy.next_turn(request())
    assert action == AgentAction("read_file", {"path": "README.md"})
    assert usage == Usage(1000, 500)
    assert policy.cost(usage) == pytest.approx(0.0105)
    sent = fake.requests[0]
    assert sent["url"].endswith("/v1/messages")
    assert sent["headers"]["x-api-key"] == "sk-ant-testkey"
    assert sent["json"]["model"] == "claude-test"
    assert sent["json"]["messages"][0]["role"] == "user"
    assert "sk-ant-testkey" not in repr(policy)


def test_openai_policy_parses_json_and_usage() -> None:
    fake = FakeTransport(
        HttpResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"action": "run_tests", "args": {}}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
    )
    policy = LlmPolicy(
        "openai",
        "gpt-test",
        "sk-testkey",
        price_input_per_mtok=1.0,
        price_output_per_mtok=2.0,
        transport=fake,
    )
    action, usage = policy.next_turn(request())
    assert action == AgentAction("run_tests", {})
    assert usage == Usage(10, 5)
    assert fake.requests[0]["url"].endswith("/v1/chat/completions")
    assert fake.requests[0]["json"]["messages"][0]["role"] == "system"


def test_provider_invalid_json_is_an_error() -> None:
    fake = FakeTransport(
        HttpResponse(200, {"content": [{"type": "text", "text": "not json"}]})
    )
    policy = LlmPolicy(
        "anthropic",
        "claude-test",
        "sk-ant-testkey",
        price_input_per_mtok=1.0,
        price_output_per_mtok=1.0,
        transport=fake,
    )
    with pytest.raises(ProviderError):
        policy.next_turn(request())


def test_provider_http_error_is_an_error() -> None:
    fake = FakeTransport(HttpResponse(401, {"error": "bad key"}))
    policy = LlmPolicy(
        "anthropic",
        "claude-test",
        "sk-ant-testkey",
        price_input_per_mtok=1.0,
        price_output_per_mtok=1.0,
        transport=fake,
    )
    with pytest.raises(ProviderError):
        policy.next_turn(request())
