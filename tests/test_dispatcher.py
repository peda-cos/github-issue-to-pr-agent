from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from issue2pr.config import Settings
from issue2pr.dispatcher import Dispatcher
from issue2pr.github_client import FakeGitHubClient
from issue2pr.models import RunOutcome
from issue2pr.store import StateStore

from conftest import make_task


def _dispatcher(settings: Settings, github: FakeGitHubClient) -> Dispatcher:
    store = StateStore(settings.state_dir / "state.db")
    return Dispatcher(
        store,
        github,
        settings,
        now=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def test_budget_denial_never_calls_worker(settings: Settings) -> None:
    tight = Settings(
        state_dir=settings.state_dir,
        webhook_secret=settings.webhook_secret,
        max_cost_per_task_usd=20.0,
        max_daily_cost_per_repo_usd=1.0,
        max_prs_per_repo_per_day=5,
    )
    tight.ensure_dirs()
    store = StateStore(tight.state_dir / "state.db")
    github = FakeGitHubClient(tight.state_dir)
    store.enqueue(make_task(delivery_id="budget-1"), "t0")
    dispatcher = Dispatcher(
        store,
        github,
        tight,
        now=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )

    def must_not_run(task: object) -> object:
        raise AssertionError(f"worker should not run: {task}")

    result = dispatcher.dispatch_once(must_not_run)  # type: ignore[arg-type]
    assert result is not None
    assert result.outcome is RunOutcome.BUDGET_DENIED
    assert result.pr_number is None
    assert github.pull_requests("acme/widget") == ()
    comments = github.comments("acme/widget")
    assert comments and "budget" in str(comments[0]["body"])
    assert store.task_status("budget-1") == "skipped"


def test_status_command_does_not_consume_budget(settings: Settings) -> None:
    store = StateStore(settings.state_dir / "state.db")
    github = FakeGitHubClient(settings.state_dir)
    task = make_task(delivery_id="status-1", kind="status")
    store.enqueue(task, "t0")
    dispatcher = Dispatcher(
        store,
        github,
        settings,
        now=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert dispatcher.dispatch_once(lambda task: (_ for _ in ()).throw(AssertionError())) is None
    comments = github.comments("acme/widget")
    assert comments and "no agent runs recorded" in str(comments[0]["body"])
    assert store.usage("2026-01-01") == []
    assert store.task_status("status-1") == "done"


def test_drain_stops_without_claiming_extra_task(settings: Settings) -> None:
    store = StateStore(settings.state_dir / "state.db")
    github = FakeGitHubClient(settings.state_dir)
    dispatcher = Dispatcher(store, github, settings)
    assert dispatcher.drain(lambda task: (_ for _ in ()).throw(AssertionError())) == []
    assert not store.has_queued()
