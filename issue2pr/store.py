from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from .models import BudgetReservation, IssueTask, RunOutcome, RunResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    delivery_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    payload TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    pr_number INTEGER,
    cost_usd REAL NOT NULL,
    turns INTEGER NOT NULL,
    coverage_base REAL,
    coverage_head REAL,
    coverage_delta REAL,
    files_changed INTEGER NOT NULL,
    insertions INTEGER NOT NULL,
    deletions INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    policy TEXT NOT NULL,
    rationale TEXT NOT NULL,
    trace_path TEXT NOT NULL,
    branch TEXT,
    labels_json TEXT NOT NULL,
    detail TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget (
    repo TEXT NOT NULL,
    day TEXT NOT NULL,
    reserved_usd REAL NOT NULL DEFAULT 0,
    spent_usd REAL NOT NULL DEFAULT 0,
    reserved_prs INTEGER NOT NULL DEFAULT 0,
    opened_prs INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, day)
);
"""


@dataclass(frozen=True)
class BudgetUsage:
    repo: str
    day: str
    reserved_usd: float
    spent_usd: float
    reserved_prs: int
    opened_prs: int


class StateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def enqueue(self, task: IssueTask, now: str) -> bool:
        payload = json.dumps(asdict(task), sort_keys=True)
        with self._transaction() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO tasks "
                "(delivery_id, repo, issue_number, payload, kind, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                (
                    task.delivery_id,
                    task.repo,
                    task.issue_number,
                    payload,
                    task.kind,
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def claim_next(self, now: str) -> IssueTask | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT delivery_id, payload FROM tasks "
                "WHERE status = 'queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                "UPDATE tasks SET status = 'running', updated_at = ? "
                "WHERE delivery_id = ? AND status = 'queued'",
                (now, row["delivery_id"]),
            )
            if cursor.rowcount != 1:
                return None
            return _task_from_payload(str(row["payload"]))

    def finish_task(self, delivery_id: str, status: str, now: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE delivery_id = ?",
                (status, now, delivery_id),
            )

    def record_run(self, result: RunResult) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.run_id,
                    result.delivery_id,
                    result.repo,
                    result.issue_number,
                    result.outcome.value,
                    result.pr_number,
                    result.cost_usd,
                    result.turns,
                    result.coverage_base,
                    result.coverage_head,
                    result.coverage_delta,
                    result.files_changed,
                    result.insertions,
                    result.deletions,
                    result.duration_seconds,
                    result.policy,
                    result.rationale,
                    result.trace_path,
                    result.branch,
                    json.dumps(list(result.labels)),
                    result.detail,
                    result.started_at,
                    result.finished_at,
                ),
            )

    def latest_run(self, repo: str, issue_number: int) -> RunResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE repo = ? AND issue_number = ? "
                "ORDER BY finished_at DESC LIMIT 1",
                (repo, issue_number),
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def reserve_budget(
        self,
        repo: str,
        day: str,
        task_cost: float,
        daily_cost_cap: float,
        pr_cap: int,
    ) -> tuple[BudgetReservation | None, str]:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM budget WHERE repo = ? AND day = ?", (repo, day)
            ).fetchone()
            reserved_usd = float(row["reserved_usd"]) if row else 0.0
            spent_usd = float(row["spent_usd"]) if row else 0.0
            reserved_prs = int(row["reserved_prs"]) if row else 0
            opened_prs = int(row["opened_prs"]) if row else 0
            if spent_usd + reserved_usd + task_cost > daily_cost_cap:
                return None, "daily_cost"
            if opened_prs + reserved_prs >= pr_cap:
                return None, "pr_count"
            conn.execute(
                "INSERT INTO budget (repo, day, reserved_usd, spent_usd, reserved_prs, opened_prs) "
                "VALUES (?, ?, ?, 0, 1, 0) "
                "ON CONFLICT(repo, day) DO UPDATE SET "
                "reserved_usd = budget.reserved_usd + excluded.reserved_usd, "
                "reserved_prs = budget.reserved_prs + 1",
                (repo, day, task_cost),
            )
            return BudgetReservation(repo, day, task_cost), ""

    def settle_budget(
        self,
        reservation: BudgetReservation,
        actual_cost: float,
        pr_opened: bool,
    ) -> None:
        actual = max(0.0, actual_cost)
        with self._transaction() as conn:
            conn.execute(
                "UPDATE budget SET "
                "reserved_usd = MAX(0, reserved_usd - ?), "
                "spent_usd = spent_usd + ?, "
                "reserved_prs = MAX(0, reserved_prs - 1), "
                "opened_prs = opened_prs + ? "
                "WHERE repo = ? AND day = ?",
                (
                    reservation.task_cost_usd,
                    actual,
                    1 if pr_opened else 0,
                    reservation.repo,
                    reservation.day,
                ),
            )

    def usage(self, day: str) -> list[BudgetUsage]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM budget WHERE day = ? ORDER BY repo", (day,)
            ).fetchall()
        return [
            BudgetUsage(
                repo=str(row["repo"]),
                day=str(row["day"]),
                reserved_usd=float(row["reserved_usd"]),
                spent_usd=float(row["spent_usd"]),
                reserved_prs=int(row["reserved_prs"]),
                opened_prs=int(row["opened_prs"]),
            )
            for row in rows
        ]

    def has_queued(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM tasks WHERE status = 'queued' LIMIT 1"
            ).fetchone()
        return row is not None

    def task_status(self, delivery_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM tasks WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
        return str(row["status"]) if row is not None else None


def _task_from_payload(payload: str) -> IssueTask:
    raw: dict[str, Any] = json.loads(payload)
    return IssueTask(
        delivery_id=str(raw["delivery_id"]),
        repo=str(raw["repo"]),
        issue_number=int(raw["issue_number"]),
        title=str(raw["title"]),
        body=str(raw["body"]),
        labels=tuple(str(x) for x in raw.get("labels", [])),
        installation_id=int(raw.get("installation_id", 0)),
        base_branch=str(raw.get("base_branch", "main")),
        is_pull_request=bool(raw.get("is_pull_request", False)),
        kind="status" if raw.get("kind") == "status" else "fix",
    )


def _run_from_row(row: sqlite3.Row) -> RunResult:
    return RunResult(
        run_id=str(row["run_id"]),
        delivery_id=str(row["delivery_id"]),
        repo=str(row["repo"]),
        issue_number=int(row["issue_number"]),
        outcome=RunOutcome(str(row["outcome"])),
        pr_number=int(row["pr_number"]) if row["pr_number"] is not None else None,
        cost_usd=float(row["cost_usd"]),
        turns=int(row["turns"]),
        coverage_base=(
            float(row["coverage_base"]) if row["coverage_base"] is not None else None
        ),
        coverage_head=(
            float(row["coverage_head"]) if row["coverage_head"] is not None else None
        ),
        coverage_delta=(
            float(row["coverage_delta"]) if row["coverage_delta"] is not None else None
        ),
        files_changed=int(row["files_changed"]),
        insertions=int(row["insertions"]),
        deletions=int(row["deletions"]),
        duration_seconds=float(row["duration_seconds"]),
        policy=str(row["policy"]),
        rationale=str(row["rationale"]),
        trace_path=str(row["trace_path"]),
        branch=str(row["branch"]) if row["branch"] is not None else None,
        labels=tuple(str(x) for x in json.loads(str(row["labels_json"]))),
        detail=str(row["detail"]),
        started_at=str(row["started_at"]),
        finished_at=str(row["finished_at"]),
    )
