from __future__ import annotations

import json
from pathlib import Path

from issue2pr.cli import main
from issue2pr.eval import compare, run_eval


def test_eval_runs_all_thirty_cases(tmp_path: Path) -> None:
    state = tmp_path / "eval"
    report = run_eval(state)
    assert report["issues"] == 30
    assert report["resolved"] == 27
    assert report["pass_rate"] == 0.9
    assert report["outcomes"] == {"hidden_failed": 3, "verified": 27}
    assert report["prs_opened"] == 27
    assert report["cost_usd_total"] == 0.0
    assert report["avg_turns"] == 5
    assert report["avg_files_changed"] > 0
    assert report["avg_coverage_delta_pp"] is not None
    assert report["latency_seconds_p50"] > 0
    results = report["results"]
    assert len(results) == 30
    assert len({row["issue_id"] for row in results}) == 30
    failing = [row for row in results if not row["resolved"]]
    assert {row["issue_id"] for row in failing} == {
        "slugify-punctuation",
        "parse-date-invalid",
        "month-length-february",
    }
    assert all(row["pr_number"] is None for row in failing)
    assert (state / "eval-results.json").is_file()


def test_compare_intersects_shared_issue_ids() -> None:
    local = {
        "tool": "issue2pr-reference",
        "results": [
            {"issue_id": "a", "resolved": True, "cost_usd": 0.0, "latency_seconds": 2},
            {"issue_id": "b", "resolved": False, "cost_usd": 0.0, "latency_seconds": 4},
            {"issue_id": "local-only", "resolved": True, "cost_usd": 1.0, "latency_seconds": 1},
        ],
    }
    baseline = {
        "tool": "hosted-agent",
        "results": [
            {"issue_id": "a", "resolved": True, "cost_usd": 2.0, "latency_seconds": 10},
            {"issue_id": "b", "resolved": True, "cost_usd": 4.0, "latency_seconds": 20},
            {"issue_id": "baseline-only", "resolved": True, "cost_usd": 1.0, "latency_seconds": 1},
        ],
    }
    output = compare(local, baseline)
    assert "Shared issues: 2" in output
    assert "hosted-agent" in output


def test_demo_and_cli_helpers(tmp_path: Path, capsys: object) -> None:
    state = tmp_path / "demo"
    assert main(["demo", "--state", str(state)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "## Agent rationale" in captured.out
    assert "Coverage:" in captured.out
    assert "Turns: 5" in captured.out
    assert "Cost: $0.0000" in captured.out
    assert "needs-review" in captured.out


def test_cli_infer_budget_compare(tmp_path: Path, capsys: object) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "mod.py").write_text("VALUE = 1\n")
    assert main(["infer", str(repo), "--write"]) == 0
    assert (repo / "Dockerfile.issue2pr").is_file()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "language: python" in captured.out or "language:" in captured.out
    budget_state = tmp_path / "budget"
    assert main(["budget", "--state", str(budget_state)]) == 0
    local = {
        "tool": "issue2pr-reference",
        "results": [
            {"issue_id": "x", "resolved": True, "cost_usd": 0.0, "latency_seconds": 1}
        ],
    }
    baseline = {
        "tool": "hosted",
        "results": [
            {"issue_id": "x", "resolved": True, "cost_usd": 1.0, "latency_seconds": 2}
        ],
    }
    (tmp_path / "local.json").write_text(json.dumps(local))
    (tmp_path / "baseline.json").write_text(json.dumps(baseline))
    assert (
        main(
            [
                "compare",
                str(tmp_path / "local.json"),
                str(tmp_path / "baseline.json"),
            ]
        )
        == 0
    )
