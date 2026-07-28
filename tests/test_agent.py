from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from issue2pr.agent import AgentLoop, Policy, PolicyError, PolicyRequest
from issue2pr.models import AgentAction, IssueTask, ToolResult, Usage
from issue2pr.policy import ReferencePolicy
from issue2pr.tools import ToolBox


def make_issue() -> IssueTask:
    return IssueTask(
        delivery_id="delivery",
        repo="acme/widget",
        issue_number=1,
        title="Fix",
        body="Broken",
    )


def test_tool_edit_write_search_and_repo_map(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    toolbox = ToolBox(tmp_path, (sys.executable, "-c", "print('ok')"))
    assert toolbox.read_file("pkg/mod.py").ok
    assert toolbox.execute(AgentAction("read_file", {"path": "../outside"})).ok is False
    assert toolbox.write_file("notes.txt", "hello").ok
    assert toolbox.read_file("notes.txt").output == "hello"
    assert not toolbox.edit_file("notes.txt", "l", "x").ok
    assert toolbox.edit_file("notes.txt", "hello", "world").ok
    assert not toolbox.edit_file("notes.txt", "missing", "x").ok
    search = toolbox.search("def add")
    assert search.ok and "pkg/mod.py" in search.output
    mapping = toolbox.repo_map()
    assert "def add" in mapping.output
    assert toolbox.run_tests().ok
    assert toolbox.git(["status"]).ok
    assert not toolbox.git(["push"]).ok
    assert not toolbox.git(["diff", "--no-index", "/etc/passwd", "/etc/hosts"]).ok


def test_tool_protects_workflows_and_traversal(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "ok.txt").write_text("ok")
    toolbox = ToolBox(tmp_path, (sys.executable, "-c", "pass"))
    assert not toolbox.write_file(".github/workflows/ci.yml", "evil").ok
    assert not toolbox.edit_file(".github/workflows/ci.yml", "a", "b").ok
    assert not toolbox.execute(AgentAction("write_file", {"path": "../escape.txt", "content": "evil"})).ok


class _LoopingPolicy:
    name = "looping"

    def __init__(self) -> None:
        self.calls = 0

    def next_turn(self, request: PolicyRequest) -> tuple[AgentAction, Usage]:
        del request
        self.calls += 1
        return AgentAction("read_file", {"path": "missing.txt"}), Usage()

    def cost(self, usage: Usage) -> float:
        del usage
        return 0.0


class _CostlyPolicy(_LoopingPolicy):
    name = "costly"

    def cost(self, usage: Usage) -> float:
        del usage
        return 1.0


class _FinishPolicy:
    name = "finish"

    def next_turn(self, request: PolicyRequest) -> tuple[AgentAction, Usage]:
        del request
        return AgentAction("finish", {"summary": "done"}), Usage(1, 1)

    def cost(self, usage: Usage) -> float:
        del usage
        return 0.0


class _Clock:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.index = 0

    def __call__(self) -> float:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


def test_agent_turn_and_cost_limits(tmp_path: Path) -> None:
    toolbox = ToolBox(tmp_path, (sys.executable, "-c", "pass"))
    turns = AgentLoop(max_turns=2, max_cost_usd=10, clock=_Clock([0, 0.1, 0.2, 0.3]))
    result = turns.run(make_issue(), toolbox, _LoopingPolicy())
    assert result.stop_reason == "turn_limit"
    assert result.turns == 2
    cost = AgentLoop(
        max_turns=10, max_cost_usd=0.5, clock=_Clock([0, 0.1, 0.2, 0.3, 0.4])
    )
    cost_result = cost.run(make_issue(), toolbox, _CostlyPolicy())
    assert cost_result.stop_reason == "cost_limit"
    assert cost_result.turns == 1


def test_agent_wall_clock_and_finish(tmp_path: Path) -> None:
    toolbox = ToolBox(tmp_path, (sys.executable, "-c", "pass"))
    wall = AgentLoop(
        max_turns=10,
        max_wall_seconds=2,
        max_cost_usd=10,
        clock=_Clock([0, 0.5, 3.0]),
    )
    assert wall.run(make_issue(), toolbox, _LoopingPolicy()).stop_reason == (
        "wall_clock_limit"
    )
    finished = AgentLoop(max_turns=3, clock=_Clock([0, 0.1, 0.2])).run(
        make_issue(), toolbox, _FinishPolicy()
    )
    assert finished.stop_reason == "finished"
    assert finished.rationale == "done"


def test_reference_policy_exhaustion_is_policy_error(tmp_path: Path) -> None:
    toolbox = ToolBox(tmp_path, (sys.executable, "-c", "pass"))
    policy = ReferencePolicy((AgentAction("read_file", {"path": "missing"}),))
    result = AgentLoop(max_turns=3, clock=_Clock([0, 0.1, 0.2])).run(
        make_issue(), toolbox, policy
    )
    assert result.stop_reason == "policy_error"


def test_agent_blocks_invalid_tool_and_keeps_going(tmp_path: Path) -> None:
    toolbox = ToolBox(tmp_path, (sys.executable, "-c", "pass"))
    (tmp_path / "a.txt").write_text("a")
    policy = ReferencePolicy(
        (
            AgentAction("write_file", {"path": ".github/workflows/x.yml", "content": "x"}),
            AgentAction("finish", {"summary": "blocked then done"}),
        )
    )
    result = AgentLoop(max_turns=4, clock=_Clock([0, 0.1, 0.2, 0.3])).run(
        make_issue(), toolbox, policy
    )
    assert result.stop_reason == "finished"
    assert result.actions[0].observation.ok is False
