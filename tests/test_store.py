from __future__ import annotations

from pathlib import Path

from issue2pr.models import RunOutcome, RunResult
from issue2pr.store import StateStore

from conftest import make_task


def test_claim_is_single_consumer_and_idempotent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    first = make_task(delivery_id="d1", issue_number=1)
    second = make_task(delivery_id="d2", issue_number=2)
    assert store.enqueue(first, "t0")
    assert not store.enqueue(first, "t1")
    assert store.enqueue(second, "t2")
    claimed = store.claim_next("t3")
    assert claimed is not None and claimed.delivery_id == "d1"
    claimed_second = store.claim_next("t4")
    assert claimed_second is not None and claimed_second.delivery_id == "d2"
    assert store.claim_next("t5") is None
    store.finish_task("d1", "done", "t6")
    assert store.task_status("d1") == "done"
    assert not store.has_queued()


def test_run_history_round_trips(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    result = RunResult(
        run_id="run-1",
        delivery_id="d1",
        repo="acme/widget",
        issue_number=1,
        outcome=RunOutcome.VERIFIED,
        pr_number=9,
        cost_usd=0.25,
        turns=3,
        coverage_base=80.0,
        coverage_head=81.0,
        coverage_delta=1.0,
        files_changed=2,
        insertions=5,
        deletions=1,
        duration_seconds=1.5,
        policy="reference",
        rationale="fixed it",
        trace_path="/tmp/trace.json",
        branch="agent/issue-1-d1",
        labels=("needs-review",),
        detail="",
        started_at="t0",
        finished_at="t1",
    )
    store.record_run(result)
    loaded = store.latest_run("acme/widget", 1)
    assert loaded == result


def test_budget_reservation_cost_and_pr_limits(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    reserved, reason = store.reserve_budget("acme/widget", "2026-01-01", 8.0, 15.0, 2)
    assert reserved is not None and reason == ""
    denied, reason = store.reserve_budget("acme/widget", "2026-01-01", 8.0, 15.0, 2)
    assert denied is None and reason == "daily_cost"
    store.settle_budget(reserved, 3.0, pr_opened=True)
    again, _ = store.reserve_budget("acme/widget", "2026-01-01", 8.0, 15.0, 2)
    assert again is not None
    store.settle_budget(again, 0.0, pr_opened=False)
    usage = store.usage("2026-01-01")
    assert usage[0].spent_usd == 3.0
    assert usage[0].opened_prs == 1
    assert usage[0].reserved_usd == 0.0
    denied, reason = store.reserve_budget("acme/widget", "2026-01-01", 8.0, 100.0, 1)
    assert denied is None and reason == "pr_count"


def test_failed_run_releases_pr_slot(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    first, _ = store.reserve_budget("acme/widget", "2026-01-01", 5.0, 100.0, 1)
    assert first is not None
    store.settle_budget(first, 1.0, pr_opened=False)
    second, reason = store.reserve_budget("acme/widget", "2026-01-01", 5.0, 100.0, 1)
    assert second is not None and reason == ""
    other, reason = store.reserve_budget("acme/other", "2026-01-01", 5.0, 100.0, 1)
    assert other is not None and reason == ""
