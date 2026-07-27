from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from issue2pr.models import FileEdit
from issue2pr.safety import UnsafePushError
from issue2pr.sandbox import (
    CommandRunner,
    FixtureWorkspaceFactory,
    GitSandbox,
    agent_branch,
)
from issue2pr.models import IssueTask

from conftest import make_task


def test_sandbox_commits_pushes_agent_branch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "hello.py").write_text("VALUE = 1\n")
    sandbox = GitSandbox.from_directory(
        source, tmp_path / "run", "main", "agent/issue-1-test"
    )
    sandbox.create_agent_branch()
    (sandbox.repo / "hello.py").write_text("VALUE = 2\n")
    (sandbox.repo / "new.txt").write_text("new\n")
    sandbox.stage_all()
    assert sandbox.changed_paths() == ("hello.py", "new.txt")
    assert sandbox.diff_stats() == (2, 2, 1)
    sandbox.commit("fix: test")
    pushed, output = sandbox.push()
    assert pushed, output
    remote = tmp_path / "run" / "remote.git"
    refs = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "refs/heads/agent/issue-1-test" in refs


def test_force_and_base_push_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a")
    sandbox = GitSandbox.from_directory(source, tmp_path / "run", "main", "main")
    with pytest.raises(UnsafePushError):
        sandbox.push()


def test_command_runner_timeout_and_env(tmp_path: Path) -> None:
    runner = CommandRunner(env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        tmp_path,
        timeout=0.1,
    )
    assert not result.ok
    assert result.returncode == 124
    assert "timed out" in result.output


def test_fixture_workspace_factory_applies_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "mod.py").write_text("VALUE = 'base'\n")
    (repo / "pkg" / "__init__.py").write_text("")
    factory = FixtureWorkspaceFactory(
        repo, FileEdit("pkg/mod.py", "VALUE = 'base'", "VALUE = 'mutated'")
    )
    task = make_task()
    sandbox = factory(tmp_path / "run", task)
    assert "mutated" in (sandbox.repo / "pkg" / "mod.py").read_text()
    assert sandbox.agent_branch.startswith("agent/issue-42-")


def test_agent_branch_sanitizes_delivery() -> None:
    task = IssueTask(
        delivery_id="bad/delivery value",
        repo="acme/widget",
        issue_number=9,
        title="t",
        body="b",
    )
    branch = agent_branch(task)
    assert branch.startswith("agent/issue-9-")
    assert "/" not in branch.removeprefix("agent/")
    assert " " not in branch


def test_relative_workspace_paths_stay_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = Path("relative-source")
    source.mkdir()
    (source / "a.txt").write_text("a")
    sandbox = GitSandbox.from_directory(
        source, Path("relative-run"), "main", "agent/issue-2-relative"
    )
    assert sandbox.repo.is_absolute()
    assert (tmp_path / "relative-run" / "remote.git" / "HEAD").is_file()
    sandbox.create_agent_branch()
    (sandbox.repo / "a.txt").write_text("b")
    sandbox.stage_all()
    sandbox.commit("fix: relative")
    pushed, output = sandbox.push()
    assert pushed, output
