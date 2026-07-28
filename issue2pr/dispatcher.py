from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Callable

from .config import Settings
from .github_client import GitHubClient
from .models import IssueTask, RunOutcome, RunResult
from .store import StateStore
from .worker import Worker


class Dispatcher:
    def __init__(
        self,
        store: StateStore,
        github: GitHubClient,
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.github = github
        self.settings = settings
        self.now = now or (lambda: datetime.now(UTC))

    def dispatch_once(
        self, worker_factory: Callable[[IssueTask], Worker]
    ) -> RunResult | None:
        moment = self.now()
        now_text = moment.isoformat()
        task = self.store.claim_next(now_text)
        if task is None:
            return None
        if task.kind == "status":
            self._post_status(task)
            self.store.finish_task(task.delivery_id, "done", now_text)
            return None
        day = moment.date().isoformat()
        reservation, reason = self.store.reserve_budget(
            task.repo,
            day,
            self.settings.max_cost_per_task_usd,
            self.settings.max_daily_cost_per_repo_usd,
            self.settings.max_prs_per_repo_per_day,
        )
        if reservation is None:
            denied = return_failure(
                task,
                RunOutcome.BUDGET_DENIED,
                detail=f"daily budget denied: {reason}",
                now=moment,
            )
            self.store.record_run(denied)
            try:
                self.github.comment(
                    task.repo,
                    task.issue_number,
                    f"@agent budget for `{task.repo}` denied this run ({reason}).\n"
                    "No worker was started and no PR was opened.\n"
                    "Retry after 00:00 UTC with `@agent retry`.",
                )
            except Exception:
                pass
            self.store.finish_task(task.delivery_id, "skipped", now_text)
            return denied
        result: RunResult | None = None
        try:
            worker = worker_factory(task)
            result = worker.run(task)
        except Exception as exc:
            result = return_failure(
                task,
                RunOutcome.AGENT_ERROR,
                detail=f"worker crashed: {type(exc).__name__}: {exc}",
                now=moment,
            )
            try:
                self.github.comment(
                    task.repo,
                    task.issue_number,
                    f"@agent worker crashed for #{task.issue_number}: "
                    f"{type(exc).__name__}. No PR was opened.\n"
                    "Comment `@agent retry` to try again.",
                )
            except Exception:
                pass
        finally:
            actual_cost = result.cost_usd if result is not None else 0.0
            pr_opened = bool(
                result is not None
                and result.pr_number is not None
                and result.outcome.opens_pr
            )
            self.store.settle_budget(reservation, actual_cost, pr_opened)
        if result is not None:
            self.store.record_run(result)
        self.store.finish_task(task.delivery_id, "done", now_text)
        return result

    def drain(
        self,
        worker_factory: Callable[[IssueTask], Worker],
        max_tasks: int = 100,
    ) -> list[RunResult]:
        results: list[RunResult] = []
        for _ in range(max_tasks):
            if not self.store.has_queued():
                break
            result = self.dispatch_once(worker_factory)
            if result is not None:
                results.append(result)
        return results

    def _post_status(self, task: IssueTask) -> None:
        latest = self.store.latest_run(task.repo, task.issue_number)
        if latest is None:
            body = f"Agent status for #{task.issue_number}: no agent runs recorded."
        else:
            pr = f"#{latest.pr_number}" if latest.pr_number is not None else "none"
            body = (
                f"Agent status for #{task.issue_number}: `{latest.outcome.value}`.\n"
                f"PR: {pr} | Turns: {latest.turns} | "
                f"Cost: ${latest.cost_usd:.4f}\n"
                f"Trace: {latest.trace_path}"
            )
        try:
            self.github.comment(task.repo, task.issue_number, body)
        except Exception:
            return


def return_failure(
    task: IssueTask,
    outcome: RunOutcome,
    *,
    detail: str,
    now: datetime,
) -> RunResult:
    stamp = now.isoformat()
    return RunResult(
        run_id=f"{task.issue_number}-{uuid.uuid4().hex[:10]}",
        delivery_id=task.delivery_id,
        repo=task.repo,
        issue_number=task.issue_number,
        outcome=outcome,
        detail=detail,
        started_at=stamp,
        finished_at=stamp,
    )
