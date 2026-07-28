from __future__ import annotations

from pathlib import Path

from issue2pr.agent import AgentAction
from issue2pr.config import Settings
from issue2pr.github_client import FakeGitHubClient
from issue2pr.models import RunOutcome, ToolResult, Usage
from issue2pr.policy import ReferencePolicy
from issue2pr.verification import VerificationRun
from issue2pr.worker import Worker, default_verifier_factory

from bench.cases import get_case
from issue2pr.eval import FIXTURE_ROOT
from issue2pr.sandbox import FixtureWorkspaceFactory

from conftest import make_task


def _worker(
    settings: Settings,
    github: FakeGitHubClient,
    case_id: int,
    actions: tuple[AgentAction, ...] | None = None,
    verifier_factory: object | None = None,
) -> Worker:
    case = get_case(case_id)
    assert case is not None
    chosen = actions if actions is not None else case.reference_actions()
    return Worker(
        settings=settings,
        github=github,
        trace_dir=settings.trace_dir,
        workspace_factory=FixtureWorkspaceFactory(FIXTURE_ROOT, case.mutation),
        policy_factory=lambda: ReferencePolicy(chosen),
        verifier_factory=(
            default_verifier_factory(settings)
            if verifier_factory is None
            else (lambda repo, env: verifier_factory)  # type: ignore[return-value]
        ),
        hidden_test=case.hidden_test,
    )


def test_worker_opens_labeled_pr_with_rationale(settings: Settings) -> None:
    github = FakeGitHubClient(settings.state_dir)
    task = make_task(repo="bench/initials", issue_number=102)
    result = _worker(settings, github, 102).run(task)
    assert result.outcome is RunOutcome.VERIFIED
    assert result.pr_number is not None
    assert result.labels == ("needs-review",)
    assert result.coverage_delta is not None
    pulls = github.pull_requests(task.repo)
    assert len(pulls) == 1
    body = str(pulls[0]["body"])
    for marker in (
        "## Agent rationale",
        "## Verification",
        "Coverage:",
        "Diff:",
        "Turns:",
        "Cost:",
        "Trace:",
        "needs-review",
        "## Safety",
        "no force push",
    ):
        assert marker in body
    trace = Path(result.trace_path)
    assert trace.is_file()
    assert "agent_action" in trace.read_text()


def test_worker_hidden_failure_blocks_pr(settings: Settings) -> None:
    github = FakeGitHubClient(settings.state_dir)
    task = make_task(repo="bench/parse-date", issue_number=124)
    result = _worker(settings, github, 124).run(task)
    assert result.outcome is RunOutcome.HIDDEN_FAILED
    assert result.pr_number is None
    assert github.pull_requests(task.repo) == ()
    comments = github.comments(task.repo)
    assert comments and "@agent retry" in str(comments[0]["body"])


def test_worker_secret_diff_is_blocked_and_redacted(settings: Settings) -> None:
    github = FakeGitHubClient(settings.state_dir)
    task = make_task(repo="bench/secret", issue_number=102)
    actions = (
        AgentAction(
            "write_file", {"path": "LEAK.txt", "content": "ghp_test_only"}
        ),
        AgentAction("finish", {"summary": "added a file"}),
    )
    result = _worker(settings, github, 102, actions=actions).run(task)
    assert result.outcome is RunOutcome.SECRET_DETECTED
    assert result.pr_number is None
    trace = Path(result.trace_path).read_text()
    assert "ghp_test_only" not in trace
    assert "[REDACTED]" in trace
    comments = github.comments(task.repo)
    assert comments and "ghp_test_only" not in str(comments[0]["body"])


def test_worker_red_ci_blocks_pr(settings: Settings) -> None:
    github = FakeGitHubClient(settings.state_dir)
    task = make_task(repo="bench/red", issue_number=102)
    actions = (
        AgentAction(
            "write_file",
            {"path": "benchlab/text.py", "content": "raise RuntimeError('boom')\n"},
        ),
        AgentAction("finish", {"summary": "broke the package"}),
    )
    result = _worker(settings, github, 102, actions=actions).run(task)
    assert result.outcome in (RunOutcome.TESTS_FAILED, RunOutcome.HIDDEN_FAILED)
    assert result.pr_number is None
    assert github.pull_requests(task.repo) == ()


class _CoverageLowVerifier:
    def run_base(self, run_dir: Path) -> VerificationRun:
        del run_dir
        return VerificationRun(True, "base ok", 90.0)

    def run_visible(self, run_dir: Path, label: str = "head") -> VerificationRun:
        del run_dir, label
        return VerificationRun(True, "head ok", 80.0)

    def run_hidden(self, source: str | None, run_dir: Path) -> VerificationRun:
        del source, run_dir
        return VerificationRun(True, "hidden ok", None)


def test_worker_coverage_drop_labels_pr(settings: Settings) -> None:
    github = FakeGitHubClient(settings.state_dir)
    task = make_task(repo="bench/coverage", issue_number=102)
    result = _worker(
        settings, github, 102, verifier_factory=_CoverageLowVerifier()
    ).run(task)
    assert result.outcome is RunOutcome.COVERAGE_LOW
    assert result.pr_number is not None
    assert "coverage-drop" in result.labels
    pulls = github.pull_requests(task.repo)
    assert pulls and "coverage-drop" in pulls[0]["labels"]
