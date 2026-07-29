from __future__ import annotations

import hashlib
import hmac
import json
import statistics
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .dispatcher import Dispatcher
from .github_client import FakeGitHubClient
from .models import IssueTask, RunResult
from .policy import ReferencePolicy
from .sandbox import FixtureWorkspaceFactory
from .store import StateStore
from .webhook import handle_webhook
from .worker import Worker, default_verifier_factory

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "bench" / "fixture_repo"


def _signed_headers(secret: str, body: bytes, delivery_id: str) -> dict[str, str]:
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return {
        "x-hub-signature-256": signature,
        "x-github-event": "issues",
        "x-github-delivery": delivery_id,
        "content-type": "application/json",
    }


def run_eval(
    state_dir: Path | None = None,
    *,
    fixture_root: Path | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    from bench.cases import CASES

    base_settings = settings or Settings.from_env({})
    state = state_dir or (base_settings.state_dir / "eval")
    eval_settings = replace(
        base_settings,
        state_dir=state,
        webhook_secret="eval-only-secret",
        provider="reference",
        model="",
        price_input_per_mtok=0.0,
        price_output_per_mtok=0.0,
    )
    eval_settings.ensure_dirs()
    store = StateStore(state / "state.db")
    github = FakeGitHubClient(state)
    dispatcher = Dispatcher(store, github, eval_settings)
    root = fixture_root or FIXTURE_ROOT
    results: list[RunResult] = []
    case_ids: dict[str, str] = {}
    nonce = uuid.uuid4().hex[:8]
    for case in CASES:
        repo = f"bench/{case.id}-{nonce}"
        case_ids[repo] = case.id
        delivery_id = f"eval-{case.id}-{nonce}"
        payload = {
            "action": "labeled",
            "label": {"name": eval_settings.trigger_label},
            "issue": {
                "number": case.issue_number,
                "title": case.title,
                "body": case.body,
                "labels": [{"name": "bug"}],
            },
            "repository": {"full_name": repo, "default_branch": "main"},
            "installation": {"id": 1},
        }
        body = json.dumps(payload).encode("utf-8")
        status, text = handle_webhook(
            _signed_headers(eval_settings.webhook_secret or "", body, delivery_id),
            body,
            store,
            eval_settings,
        )
        if status != 202 or text not in {"enqueued", "duplicate"}:
            raise RuntimeError(f"eval webhook failed for {case.id}: {status} {text}")

        def worker_factory(
            task: IssueTask, case_id: str = case.id, case_obj: Any = case
        ) -> Worker:
            del case_id
            return Worker(
                settings=eval_settings,
                github=github,
                trace_dir=eval_settings.trace_dir,
                workspace_factory=FixtureWorkspaceFactory(root, case_obj.mutation),
                policy_factory=lambda: ReferencePolicy(case_obj.reference_actions()),
                verifier_factory=default_verifier_factory(eval_settings),
                hidden_test=case_obj.hidden_test,
            )

        result = dispatcher.dispatch_once(worker_factory)
        if result is None:
            raise RuntimeError(f"eval dispatch produced no result for {case.id}")
        results.append(result)
    report = _report(results, eval_settings, case_ids)
    (state / "eval-results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _report(
    results: list[RunResult],
    settings: Settings,
    case_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result.outcome.value] = outcomes.get(result.outcome.value, 0) + 1
    resolved = [result for result in results if result.outcome.value == "verified"]
    coverage_values = [
        result.coverage_delta
        for result in results
        if result.coverage_delta is not None
    ]
    total_cost = sum(result.cost_usd for result in results)
    latencies = [result.duration_seconds for result in results]
    return {
        "schema_version": 1,
        "tool": "issue2pr-reference",
        "generated_at": datetime.now(UTC).isoformat(),
        "issues": len(results),
        "resolved": len(resolved),
        "pass_rate": round(len(resolved) / len(results), 4) if results else 0.0,
        "outcomes": outcomes,
        "prs_opened": sum(1 for result in results if result.pr_number is not None),
        "cost_usd_total": round(total_cost, 6),
        "cost_usd_per_resolved": round(
            total_cost / len(resolved), 6
        )
        if resolved
        else 0.0,
        "avg_turns": round(statistics.mean([r.turns for r in results]), 2)
        if results
        else 0.0,
        "avg_files_changed": round(
            statistics.mean([r.files_changed for r in results]), 2
        )
        if results
        else 0.0,
        "avg_coverage_delta_pp": round(statistics.mean(coverage_values), 4)
        if coverage_values
        else None,
        "latency_seconds_p50": round(statistics.median(latencies), 3)
        if latencies
        else 0.0,
        "state_dir": str(settings.state_dir),
        "results": [
            {
                "issue_id": (case_ids or {}).get(
                    r.repo, r.repo.rsplit("/", 1)[-1]
                ),
                "issue_number": r.issue_number,
                "repo": r.repo,
                "outcome": r.outcome.value,
                "resolved": r.outcome.value == "verified",
                "pr_number": r.pr_number,
                "cost_usd": round(r.cost_usd, 6),
                "turns": r.turns,
                "coverage_delta": r.coverage_delta,
                "files_changed": r.files_changed,
                "latency_seconds": round(r.duration_seconds, 3),
                "trace_path": r.trace_path,
            }
            for r in results
        ],
    }


def format_report(report: dict[str, Any]) -> str:
    outcomes = ", ".join(
        f"{name}={count}" for name, count in sorted(report.get("outcomes", {}).items())
    )
    coverage = report.get("avg_coverage_delta_pp")
    coverage_text = "n/a" if coverage is None else f"{coverage:+.2f} pp"
    return (
        f"issues={report['issues']} resolved={report['resolved']} "
        f"pass_rate={report['pass_rate']:.1%}\n"
        f"prs_opened={report['prs_opened']} outcomes={outcomes}\n"
        f"turns_avg={report['avg_turns']} "
        f"files_changed_avg={report['avg_files_changed']} "
        f"coverage_delta_avg={coverage_text}\n"
        f"cost_total=${report['cost_usd_total']:.4f} "
        f"cost_per_resolved=${report['cost_usd_per_resolved']:.4f} "
        f"latency_p50={report['latency_seconds_p50']:.3f}s"
    )


def _baseline_metrics(report: dict[str, Any], issue_ids: set[str]) -> dict[str, float]:
    rows = [
        row
        for row in report.get("results", [])
        if str(row.get("issue_id")) in issue_ids
    ]
    resolved = [row for row in rows if row.get("resolved")]
    costs = [float(row.get("cost_usd", 0.0)) for row in rows]
    latencies = [float(row.get("latency_seconds", 0.0)) for row in rows]
    return {
        "issues": float(len(rows)),
        "resolved": float(len(resolved)),
        "pass_rate": (len(resolved) / len(rows)) if rows else 0.0,
        "cost_per_resolved": (sum(costs) / len(resolved)) if resolved else 0.0,
        "latency_p50": statistics.median(latencies) if latencies else 0.0,
    }


def compare(local: dict[str, Any], baseline: dict[str, Any]) -> str:
    local_ids = {str(row.get("issue_id")) for row in local.get("results", [])}
    baseline_ids = {str(row.get("issue_id")) for row in baseline.get("results", [])}
    shared = local_ids & baseline_ids
    if not shared:
        raise ValueError("local and baseline results have no shared issue_id values")
    local_metrics = _baseline_metrics(local, shared)
    baseline_metrics = _baseline_metrics(baseline, shared)
    name = str(baseline.get("tool", "baseline"))
    return (
        f"Shared issues: {len(shared)}\n"
        f"{'tool':<20} {'pass_rate':>10} {'cost/resolved':>14} {'latency_p50':>12}\n"
        f"{'issue2pr':<20} {local_metrics['pass_rate']:>10.1%} "
        f"{local_metrics['cost_per_resolved']:>14.4f} "
        f"{local_metrics['latency_p50']:>12.3f}\n"
        f"{name[:20]:<20} {baseline_metrics['pass_rate']:>10.1%} "
        f"{baseline_metrics['cost_per_resolved']:>14.4f} "
        f"{baseline_metrics['latency_p50']:>12.3f}\n"
    )
