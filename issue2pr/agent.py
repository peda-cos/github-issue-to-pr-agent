from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .models import AgentAction, IssueTask, ToolResult, Usage
from .safety import redact
from .tools import ToolBox


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionRecord:
    action: str
    args: dict[str, object]
    observation: ToolResult
    usage: Usage


@dataclass(frozen=True)
class PolicyRequest:
    issue: IssueTask
    repo_map: str
    step: int
    history: tuple[ActionRecord, ...]
    remaining_cost_usd: float
    remaining_seconds: float


class Policy(Protocol):
    name: str

    def next_turn(self, request: PolicyRequest) -> tuple[AgentAction, Usage]: ...

    def cost(self, usage: Usage) -> float: ...


@dataclass(frozen=True)
class AgentRun:
    actions: tuple[ActionRecord, ...]
    turns: int
    usage: Usage
    cost_usd: float
    stop_reason: str
    rationale: str
    duration_seconds: float


@dataclass
class AgentLoop:
    max_turns: int = 30
    max_wall_seconds: int = 1800
    max_cost_usd: float = 20.0
    clock: Callable[[], float] = field(default=time.monotonic)

    def run(self, issue: IssueTask, toolbox: ToolBox, policy: Policy) -> AgentRun:
        started = self.clock()
        repo_map = toolbox.repo_map().output
        history: list[ActionRecord] = []
        total_usage = Usage()
        total_cost = 0.0
        step = 0
        stop_reason = "turn_limit"
        rationale = ""
        while step < self.max_turns:
            elapsed = self.clock() - started
            if elapsed >= self.max_wall_seconds:
                stop_reason = "wall_clock_limit"
                break
            if total_cost >= self.max_cost_usd:
                stop_reason = "cost_limit"
                break
            request = PolicyRequest(
                issue=issue,
                repo_map=repo_map,
                step=step,
                history=tuple(history),
                remaining_cost_usd=max(0.0, self.max_cost_usd - total_cost),
                remaining_seconds=max(0.0, self.max_wall_seconds - elapsed),
            )
            step += 1
            try:
                action, usage = policy.next_turn(request)
            except PolicyError:
                stop_reason = "policy_error"
                break
            total_usage = Usage(
                input_tokens=total_usage.input_tokens + usage.input_tokens,
                output_tokens=total_usage.output_tokens + usage.output_tokens,
            )
            total_cost += policy.cost(usage)
            if action.action == "finish":
                candidate = str(action.args.get("summary", "")).strip()
                if not candidate:
                    stop_reason = "policy_error"
                    break
                rationale = redact(candidate)
                stop_reason = "finished"
                break
            if total_cost >= self.max_cost_usd:
                stop_reason = "cost_limit"
                break
            observation = toolbox.execute(action)
            observation = ToolResult(observation.ok, redact(observation.output)[-4096:])
            history.append(
                ActionRecord(
                    action=action.action,
                    args=action.args,
                    observation=observation,
                    usage=usage,
                )
            )
        if step >= self.max_turns and stop_reason == "turn_limit":
            stop_reason = "turn_limit"
        return AgentRun(
            actions=tuple(history),
            turns=step,
            usage=total_usage,
            cost_usd=total_cost,
            stop_reason=stop_reason,
            rationale=rationale,
            duration_seconds=self.clock() - started,
        )


def format_action(action: AgentAction) -> str:
    args = ", ".join(f"{key}={value!r}" for key, value in action.args.items())
    return f"{action.action}({args})"
