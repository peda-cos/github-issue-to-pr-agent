from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .agent import AgentLoop, AgentRun, Policy
from .config import Settings
from .github_client import GitHubClient
from .inference import EnvironmentInferenceError, RepoEnvironment, infer
from .models import IssueTask, PullRequestRef, RunOutcome, RunResult
from .safety import (
    UnsafePathError,
    changed_paths_allowed,
    redact,
    scan_for_secrets,
)
from .sandbox import GitSandbox, WorkspaceFactory
from .tools import ToolBox
from .trace import TraceRecorder
from .verification import Verifier, classify

VerifierFactory = Callable[[Path, RepoEnvironment], Verifier]


def default_verifier_factory(settings: Settings) -> VerifierFactory:
    def factory(repo: Path, environment: RepoEnvironment) -> Verifier:
        return Verifier(repo, environment, timeout=settings.test_timeout_seconds)

    return factory


class Worker:
    def __init__(
        self,
        settings: Settings,
        github: GitHubClient,
        trace_dir: Path,
        workspace_factory: WorkspaceFactory,
        policy_factory: Callable[[], Policy],
        verifier_factory: VerifierFactory,
        hidden_test: str | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.github = github
        self.trace_dir = trace_dir
        self.workspace_factory = workspace_factory
        self.policy_factory = policy_factory
        self.verifier_factory = verifier_factory
        self.hidden_test = hidden_test
        self.clock = clock
        self.utcnow = utcnow or (lambda: datetime.now(UTC))

    def run(self, task: IssueTask) -> RunResult:
        run_id = f"{task.issue_number}-{uuid.uuid4().hex[:10]}"
        run_dir = (self.settings.workspace_root / run_id).resolve()
        trace = TraceRecorder(self.trace_dir, run_id, self.settings.trace_url_template)
        trace.set(
            "issue",
            {
                "repo": task.repo,
                "number": task.issue_number,
                "title": task.title,
                "delivery_id": task.delivery_id,
                "base_branch": task.base_branch,
            },
        )
        started_monotonic = self.clock()
        started_at = self.utcnow().isoformat()
        outcome = RunOutcome.AGENT_ERROR
        detail = ""
        coverage_base: float | None = None
        coverage_head: float | None = None
        coverage_delta: float | None = None
        files_changed = 0
        insertions = 0
        deletions = 0
        agent_run: AgentRun | None = None
        policy_name = "reference"
        branch: str | None = None
        pr: PullRequestRef | None = None
        labels: tuple[str, ...] = ()
        sandbox: GitSandbox | None = None
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            sandbox = self.workspace_factory(run_dir, task)
            branch = sandbox.agent_branch
            trace.event("workspace", {"repo": str(sandbox.repo), "branch": branch})
            environment = infer(sandbox.repo)
            trace.set(
                "environment",
                {
                    "language": environment.language,
                    "package_manager": environment.package_manager,
                    "test_command": list(environment.test_command),
                    "coverage_supported": environment.coverage_supported,
                    "dockerfile": environment.dockerfile,
                    "evidence": list(environment.evidence),
                },
            )
            verifier = self.verifier_factory(sandbox.repo, environment)
            base = verifier.run_base(run_dir)
            coverage_base = base.coverage
            trace.event(
                "verification_base",
                {"ok": base.ok, "coverage": base.coverage, "output": base.output},
            )
            if not base.ok:
                outcome = RunOutcome.BASE_TESTS_FAILED
                detail = _tail(base.output, 2000)
            else:
                sandbox.create_agent_branch()
                toolbox = ToolBox(
                    sandbox.repo,
                    environment.test_command,
                    timeout=float(self.settings.test_timeout_seconds),
                )
                policy = self.policy_factory()
                policy_name = policy.name
                loop = AgentLoop(
                    max_turns=self.settings.max_turns,
                    max_wall_seconds=self.settings.max_wall_seconds,
                    max_cost_usd=self.settings.max_cost_per_task_usd,
                )
                agent_run = loop.run(task, toolbox, policy)
                for record in agent_run.actions:
                    trace.event(
                        "agent_action",
                        {
                            "action": record.action,
                            "args": record.args,
                            "ok": record.observation.ok,
                            "observation": record.observation.output,
                            "input_tokens": record.usage.input_tokens,
                            "output_tokens": record.usage.output_tokens,
                        },
                    )
                trace.set(
                    "agent",
                    {
                        "policy": policy_name,
                        "stop_reason": agent_run.stop_reason,
                        "turns": agent_run.turns,
                        "cost_usd": agent_run.cost_usd,
                        "usage": {
                            "input_tokens": agent_run.usage.input_tokens,
                            "output_tokens": agent_run.usage.output_tokens,
                        },
                    },
                )
                if agent_run.stop_reason != "finished":
                    outcome = RunOutcome.AGENT_ERROR
                    detail = f"agent stopped: {agent_run.stop_reason}"
                else:
                    sandbox.stage_all()
                    changed = sandbox.changed_paths()
                    files_changed, insertions, deletions = sandbox.diff_stats()
                    if not changed:
                        outcome = RunOutcome.NO_CHANGES
                        detail = "agent produced no staged changes"
                    else:
                        changed_paths_allowed(changed)
                        staged_diff = sandbox.staged_diff()
                        if scan_for_secrets(staged_diff):
                            outcome = RunOutcome.SECRET_DETECTED
                            detail = "secret-shaped content was found in the proposed diff"
                        else:
                            head = verifier.run_visible(run_dir, label="head")
                            coverage_head = head.coverage
                            if coverage_base is not None and coverage_head is not None:
                                coverage_delta = round(
                                    coverage_head - coverage_base, 4
                                )
                            hidden = verifier.run_hidden(self.hidden_test, run_dir)
                            trace.event(
                                "verification_head",
                                {
                                    "visible_ok": head.ok,
                                    "hidden_ok": hidden.ok,
                                    "coverage_base": coverage_base,
                                    "coverage_head": coverage_head,
                                    "visible_output": head.output,
                                    "hidden_output": hidden.output,
                                },
                            )
                            outcome = classify(
                                base_ok=base.ok,
                                visible_ok=head.ok,
                                hidden_ok=hidden.ok,
                                coverage_base=coverage_base,
                                coverage_head=coverage_head,
                                threshold_pp=self.settings.coverage_drop_threshold_pp,
                            )
                            if outcome.opens_pr:
                                labels = (
                                    ("needs-review", "coverage-drop")
                                    if outcome is RunOutcome.COVERAGE_LOW
                                    else ("needs-review",)
                                )
                                try:
                                    sandbox.commit(
                                        f"fix: address issue #{task.issue_number}"
                                    )
                                    pushed, push_output = sandbox.push()
                                except Exception as exc:
                                    outcome = RunOutcome.PUSH_ERROR
                                    labels = ()
                                    detail = f"{type(exc).__name__}: {exc}"
                                    pushed = False
                                    push_output = ""
                                if not pushed:
                                    if outcome.opens_pr:
                                        outcome = RunOutcome.PUSH_ERROR
                                        labels = ()
                                        detail = f"push failed: {push_output}"
                                else:
                                    trace.event(
                                        "push",
                                        {
                                            "branch": sandbox.agent_branch,
                                            "head": sandbox.head_sha(),
                                        },
                                    )
                                    try:
                                        pr = self._open_pr(
                                            task,
                                            sandbox,
                                            agent_run,
                                            outcome,
                                            tuple(labels),
                                            coverage_base,
                                            coverage_head,
                                            coverage_delta,
                                            files_changed,
                                            insertions,
                                            deletions,
                                            trace,
                                        )
                                    except Exception as exc:
                                        outcome = RunOutcome.GITHUB_ERROR
                                        labels = ()
                                        detail = f"{type(exc).__name__}: {exc}"
                            else:
                                detail = _classify_detail(outcome, head.output, hidden.output)
        except EnvironmentInferenceError as exc:
            outcome = RunOutcome.INFERENCE_ERROR
            detail = str(exc)
        except UnsafePathError as exc:
            outcome = RunOutcome.PROTECTED_PATH
            detail = str(exc)
        except Exception as exc:
            outcome = RunOutcome.VERIFICATION_ERROR
            detail = f"{type(exc).__name__}: {exc}"

        if pr is not None and outcome.opens_pr:
            trace.set(
                "pull_request",
                {"number": pr.number, "url": pr.url, "labels": list(labels)},
            )
        else:
            if outcome.opens_pr:
                outcome = RunOutcome.GITHUB_ERROR
                labels = ()
                detail = detail or "PR creation failed"
        trace.set("outcome", {"name": outcome.value, "detail": detail})
        trace_path = self.trace_dir / f"{run_id}.json"
        try:
            trace_path = trace.write()
        except OSError as exc:
            detail = f"{detail} (trace write failed: {exc})".strip()
        trace_url = trace.url(trace_path)
        finished_at = self.utcnow().isoformat()
        result = RunResult(
            run_id=run_id,
            delivery_id=task.delivery_id,
            repo=task.repo,
            issue_number=task.issue_number,
            outcome=outcome,
            pr_number=pr.number if pr is not None else None,
            cost_usd=agent_run.cost_usd if agent_run is not None else 0.0,
            turns=agent_run.turns if agent_run is not None else 0,
            coverage_base=coverage_base,
            coverage_head=coverage_head,
            coverage_delta=coverage_delta,
            files_changed=files_changed,
            insertions=insertions,
            deletions=deletions,
            duration_seconds=max(0.0, self.clock() - started_monotonic),
            policy=policy_name,
            rationale=agent_run.rationale if agent_run is not None else "",
            trace_path=str(trace_path),
            branch=branch,
            labels=tuple(labels),
            detail=detail,
            started_at=started_at,
            finished_at=finished_at,
        )
        if pr is not None and outcome.opens_pr:
            self._safe_comment(
                task,
                f"Opened #{pr.number} ({pr.url}).\n"
                f"Trace: {trace_url}\n"
                "Rationale, coverage delta, cost, and turns are in the PR body.",
            )
        else:
            self._safe_comment(task, _failure_comment(result, trace_url))
        return result

    def _open_pr(
        self,
        task: IssueTask,
        sandbox: GitSandbox,
        agent_run: AgentRun,
        outcome: RunOutcome,
        labels: tuple[str, ...],
        coverage_base: float | None,
        coverage_head: float | None,
        coverage_delta: float | None,
        files_changed: int,
        insertions: int,
        deletions: int,
        trace: TraceRecorder,
    ) -> PullRequestRef:
        title = _pr_title(task)
        trace_path = self.trace_dir / f"{trace.run_id}.json"
        body = _pr_body(
            task=task,
            rationale=agent_run.rationale,
            coverage_base=coverage_base,
            coverage_head=coverage_head,
            coverage_delta=coverage_delta,
            files_changed=files_changed,
            insertions=insertions,
            deletions=deletions,
            turns=agent_run.turns,
            cost_usd=agent_run.cost_usd,
            trace_url=trace.url(trace_path),
            labels=labels,
            branch=sandbox.agent_branch,
            base_branch=sandbox.base_branch,
            hidden_ok=outcome is not RunOutcome.HIDDEN_FAILED,
        )
        return self.github.create_pull_request(
            task.repo,
            sandbox.agent_branch,
            task.base_branch,
            title,
            body,
            labels,
        )

    def _safe_comment(self, task: IssueTask, body: str) -> None:
        try:
            self.github.comment(task.repo, task.issue_number, redact(body))
        except Exception:
            return


def _tail(text: str, limit: int) -> str:
    return text.strip()[-limit:]


def _pr_title(task: IssueTask) -> str:
    collapsed = re.sub(r"\s+", " ", task.title).strip() or "address issue"
    return f"[agent] Fix #{task.issue_number}: {collapsed}"[:100]


def _coverage_line(
    base: float | None, head: float | None, delta: float | None
) -> str:
    if base is None or head is None:
        return "unavailable"
    delta_text = f"{delta:+.2f} pp" if delta is not None else "n/a"
    return f"{base:.2f}% -> {head:.2f}% ({delta_text})"


def _pr_body(
    *,
    task: IssueTask,
    rationale: str,
    coverage_base: float | None,
    coverage_head: float | None,
    coverage_delta: float | None,
    files_changed: int,
    insertions: int,
    deletions: int,
    turns: int,
    cost_usd: float,
    trace_url: str,
    labels: tuple[str, ...],
    branch: str,
    base_branch: str,
    hidden_ok: bool,
) -> str:
    return (
        "## Agent rationale\n"
        f"{rationale}\n\n"
        "## Verification\n"
        "- Tests: passed\n"
        f"- Hidden checks: {'passed' if hidden_ok else 'not run'}\n"
        f"- Coverage: {_coverage_line(coverage_base, coverage_head, coverage_delta)}\n"
        f"- Diff: {files_changed} files, +{insertions}/-{deletions}\n"
        f"- Turns: {turns}\n"
        f"- Cost: ${cost_usd:.4f}\n"
        f"- Trace: {trace_url}\n"
        f"- Labels: {', '.join(labels)}\n\n"
        "## Safety\n"
        f"- Head branch: `{branch}` (no force push)\n"
        "- Protected paths: unchanged (`.github/workflows` read-only)\n"
        f"- Base branch: `{base_branch}` (branch protection remains authoritative)\n"
    )


def _classify_detail(
    outcome: RunOutcome, visible_output: str, hidden_output: str
) -> str:
    if outcome is RunOutcome.TESTS_FAILED:
        return "visible test suite failed\n" + _tail(visible_output, 2000)
    if outcome is RunOutcome.HIDDEN_FAILED:
        return "hidden benchmark regression check failed\n" + _tail(hidden_output, 2000)
    return outcome.value


def _failure_comment(result: RunResult, trace_url: str) -> str:
    return (
        f"@agent run for #{result.issue_number} stopped: {result.outcome.value}.\n"
        f"{result.detail}\n"
        "No PR was opened.\n"
        f"Turns: {result.turns} | Cost: ${result.cost_usd:.4f}\n"
        f"Trace: {trace_url}\n"
        "To retry, comment `@agent retry`."
    )
