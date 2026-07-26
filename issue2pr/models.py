from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

TaskKind = Literal["fix", "status"]
Difficulty = Literal["easy", "medium", "hard"]


@dataclass(frozen=True)
class IssueTask:
    delivery_id: str
    repo: str
    issue_number: int
    title: str
    body: str
    labels: tuple[str, ...] = ()
    installation_id: int = 0
    base_branch: str = "main"
    is_pull_request: bool = False
    kind: TaskKind = "fix"

    def __post_init__(self) -> None:
        if not self.delivery_id:
            raise ValueError("delivery_id is required")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise ValueError(f"invalid repository: {self.repo!r}")
        if self.issue_number <= 0:
            raise ValueError("issue_number must be positive")
        if self.kind not in ("fix", "status"):
            raise ValueError(f"invalid task kind: {self.kind!r}")


class RunOutcome(StrEnum):
    VERIFIED = "verified"
    COVERAGE_LOW = "coverage_low"
    BUDGET_DENIED = "budget_denied"
    INFERENCE_ERROR = "inference_error"
    BASE_TESTS_FAILED = "base_tests_failed"
    AGENT_ERROR = "agent_error"
    NO_CHANGES = "no_changes"
    PROTECTED_PATH = "protected_path"
    SECRET_DETECTED = "secret_detected"
    TESTS_FAILED = "tests_failed"
    HIDDEN_FAILED = "hidden_failed"
    PUSH_ERROR = "push_error"
    GITHUB_ERROR = "github_error"
    VERIFICATION_ERROR = "verification_error"

    @property
    def opens_pr(self) -> bool:
        return self in (RunOutcome.VERIFIED, RunOutcome.COVERAGE_LOW)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    delivery_id: str
    repo: str
    issue_number: int
    outcome: RunOutcome
    pr_number: int | None = None
    cost_usd: float = 0.0
    turns: int = 0
    coverage_base: float | None = None
    coverage_head: float | None = None
    coverage_delta: float | None = None
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    duration_seconds: float = 0.0
    policy: str = "reference"
    rationale: str = ""
    trace_path: str = ""
    branch: str | None = None
    labels: tuple[str, ...] = ()
    detail: str = ""
    started_at: str = ""
    finished_at: str = ""

    @property
    def resolved(self) -> bool:
        return self.outcome is RunOutcome.VERIFIED


@dataclass(frozen=True)
class FileEdit:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class AgentAction:
    action: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str


@dataclass(frozen=True)
class BudgetReservation:
    repo: str
    day: str
    task_cost_usd: float


@dataclass(frozen=True)
class PullRequestRef:
    number: int
    url: str
