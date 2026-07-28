from __future__ import annotations

import json
import os
import re
from typing import Any

from .agent import PolicyError, PolicyRequest
from .github_app import HttpResponse, Transport, TransportError, UrllibTransport
from .models import AgentAction, Usage
from .safety import redact

SYSTEM_PROMPT = """You are an autonomous senior software engineer working in a git checkout.
Solve the issue with the smallest correct change. Reply with exactly one JSON object:
{"action": "<tool>", "args": {...}}
Tools:
- read_file {"path": "relative/path"}
- write_file {"path": "relative/path", "content": "..."}
- edit_file {"path": "relative/path", "old": "exact once-occurring text", "new": "replacement"}
- search {"pattern": "regex", "glob": "optional glob"}
- repo_map {}
- run_tests {}
- git {"argv": ["status"|"diff"|"log"]}
- finish {"summary": "rationale for the reviewer"}
Rules:
1. Add or update a regression test for the failure.
2. Never modify .github/workflows, .git, or credentials.
3. Never attempt network access, credential discovery, force pushes, or branch deletion.
4. Run tests before finish. If tests fail, keep iterating.
5. Finish only when you believe the full test suite passes; include a concrete rationale.
Do not wrap the JSON in prose. Do not emit multiple actions."""


class ProviderError(PolicyError):
    pass


def _decode_action(text: str) -> AgentAction:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start < 0:
        raise ProviderError("provider response did not contain a JSON action")
    try:
        decoded, _ = json.JSONDecoder().raw_decode(candidate[start:])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"provider action was invalid JSON: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise ProviderError("provider action must be a JSON object")
    action = decoded.get("action")
    args = decoded.get("args", {})
    if not isinstance(action, str) or not action:
        raise ProviderError("provider action is missing an action name")
    if not isinstance(args, dict):
        raise ProviderError("provider action args must be an object")
    return AgentAction(action=action, args=args)


def _anthro_text(data: dict[str, Any]) -> str:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise ProviderError("Anthropic response lacked content")
    texts = [
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    if not texts:
        raise ProviderError("Anthropic response lacked text")
    return "\n".join(texts)


def _openai_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("OpenAI response lacked choices")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ProviderError("OpenAI response lacked message content")
    return str(message["content"])


def _usage_from(data: dict[str, Any], provider: str) -> Usage:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return Usage()
    if provider == "anthropic":
        return Usage(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )
    return Usage(
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
    )


class LlmPolicy:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        *,
        price_input_per_mtok: float,
        price_output_per_mtok: float,
        transport: Transport | None = None,
        base_url: str | None = None,
        max_tokens: int = 2048,
    ) -> None:
        if provider not in {"anthropic", "openai"}:
            raise ValueError("provider must be anthropic or openai")
        if not api_key:
            raise ValueError(f"missing API key for {provider}")
        if not model:
            raise ValueError("model is required")
        self.name = provider
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self.price_input_per_mtok = price_input_per_mtok
        self.price_output_per_mtok = price_output_per_mtok
        self.transport = transport or UrllibTransport(timeout=120.0)
        self.base_url = base_url or os.environ.get("ISSUE2PR_API_BASE", "")
        self.max_tokens = max_tokens

    def __repr__(self) -> str:
        return f"LlmPolicy(provider={self.provider!r}, model={self.model!r})"

    def cost(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.price_input_per_mtok
            + usage.output_tokens * self.price_output_per_mtok
        ) / 1_000_000.0

    def _messages(self, request: PolicyRequest) -> list[dict[str, str]]:
        initial = (
            f"Issue #{request.issue.issue_number}: {request.issue.title}\n\n"
            f"{request.issue.body}\n\n"
            f"Repository map:\n{request.repo_map}\n\n"
            "Choose the next action as one JSON object."
        )
        messages: list[dict[str, str]] = [{"role": "user", "content": initial}]
        for record in request.history:
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"action": record.action, "args": record.args},
                        sort_keys=True,
                    ),
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": f"Observation:\n{record.observation.output}",
                }
            )
        return messages

    def _request(
        self, messages: list[dict[str, str]]
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        if self.provider == "anthropic":
            url = (
                f"{self.base_url.rstrip('/')}/v1/messages"
                if self.base_url
                else "https://api.anthropic.com/v1/messages"
            )
            headers = {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": 0,
                "system": SYSTEM_PROMPT,
                "messages": messages,
            }
            return url, headers, body
        url = (
            f"{self.base_url.rstrip('/')}/v1/chat/completions"
            if self.base_url
            else "https://api.openai.com/v1/chat/completions"
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        }
        return url, headers, body

    def next_turn(self, request: PolicyRequest) -> tuple[AgentAction, Usage]:
        url, headers, body = self._request(self._messages(request))
        try:
            response: HttpResponse = self.transport.request("POST", url, headers, body)
        except TransportError as exc:
            raise ProviderError(redact(str(exc))) from exc
        if response.status != 200:
            raise ProviderError(
                f"{self.provider} returned status {response.status}: "
                f"{redact(str(response.data))[:500]}"
            )
        text = (
            _anthro_text(response.data)
            if self.provider == "anthropic"
            else _openai_text(response.data)
        )
        return _decode_action(text), _usage_from(response.data, self.provider)
