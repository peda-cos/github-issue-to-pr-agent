from __future__ import annotations

import base64
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from .inference import build_command_env
from .models import FileEdit, IssueTask
from .safety import UnsafePathError, resolve_inside, safe_push

_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "node_modules"
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        combined = (self.stdout + self.stderr).strip()
        return combined[-16_384:]


def _cap(text: str, limit: int = 16_384) -> str:
    return text[-limit:]


class CommandRunner:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env = dict(env) if env is not None else build_command_env()

    def run(
        self,
        argv: list[str],
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=dict(env) if env is not None else dict(self.env),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            return CommandResult(
                returncode=completed.returncode,
                stdout=_cap(completed.stdout or ""),
                stderr=_cap(completed.stderr or ""),
                duration_seconds=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return CommandResult(
                returncode=124,
                stdout=_cap(stdout or ""),
                stderr=_cap((stderr or "") + "\ncommand timed out"),
                duration_seconds=time.monotonic() - started,
            )
        except OSError as exc:
            return CommandResult(
                returncode=127,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                duration_seconds=time.monotonic() - started,
            )


def agent_branch(task: IssueTask) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", task.delivery_id)[:40]
    return f"agent/issue-{task.issue_number}-{safe}"


def github_git_env(token: str) -> dict[str, str]:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    env = build_command_env()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


def apply_edit(root: Path, edit: FileEdit) -> None:
    target = resolve_inside(root, edit.path)
    if not target.is_file():
        raise UnsafePathError(f"edit target does not exist: {edit.path}")
    text = target.read_text(encoding="utf-8")
    occurrences = text.count(edit.old)
    if occurrences != 1:
        raise ValueError(
            f"edit anchor for {edit.path} must occur once, found {occurrences}"
        )
    target.write_text(text.replace(edit.old, edit.new, 1), encoding="utf-8")


class GitSandbox:
    def __init__(
        self,
        repo: Path,
        base_branch: str,
        agent_branch_name: str,
        origin: str,
        *,
        runner: CommandRunner | None = None,
        push_env_factory: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.repo = repo.resolve()
        self.base_branch = base_branch
        self.agent_branch = agent_branch_name
        self.origin = origin
        self.runner = runner or CommandRunner()
        self._push_env_factory = push_env_factory

    def __repr__(self) -> str:
        return (
            f"GitSandbox(repo={self.repo!s}, base_branch={self.base_branch!r}, "
            f"agent_branch={self.agent_branch!r})"
        )

    def _git(
        self,
        argv: list[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = 120,
    ) -> CommandResult:
        return self.runner.run(["git", *argv], self.repo, timeout, env)

    def _require_git(self, argv: list[str], env: Mapping[str, str] | None = None) -> str:
        result = self._git(argv, env=env)
        if not result.ok:
            raise RuntimeError(f"git {' '.join(argv)} failed: {result.output}")
        return result.output

    @classmethod
    def from_directory(
        cls,
        source: Path,
        run_dir: Path,
        base_branch: str,
        agent_branch_name: str,
    ) -> "GitSandbox":
        run_dir = run_dir.resolve()
        source = source.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        repo = run_dir / "repo"
        shutil.copytree(source, repo, ignore=_IGNORE)
        sandbox = cls(repo, base_branch, agent_branch_name, "origin")
        sandbox._require_git(["init", "-b", base_branch])
        sandbox._configure_identity()
        sandbox._require_git(["add", "-A"])
        sandbox._require_git(["commit", "-m", "base snapshot"])
        remote = (run_dir / "remote.git").resolve()
        remote.mkdir(parents=True, exist_ok=True)
        init_result = sandbox.runner.run(
            ["git", "init", "--bare", str(remote)], run_dir, 120
        )
        if not init_result.ok:
            raise RuntimeError(f"git init --bare failed: {init_result.output}")
        sandbox._require_git(["remote", "add", "origin", str(remote)])
        sandbox._require_git(["push", "--porcelain", "origin", base_branch])
        return sandbox

    @classmethod
    def from_clone(
        cls,
        repo_slug: str,
        run_dir: Path,
        base_branch: str,
        agent_branch_name: str,
        clone_env: Mapping[str, str],
        push_env_factory: Callable[[], dict[str, str]],
    ) -> "GitSandbox":
        run_dir = run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        repo = run_dir / "repo"
        url = f"https://github.com/{repo_slug}.git"
        completed = subprocess.run(
            ["git", "clone", "--branch", base_branch, "--depth", "50", url, str(repo)],
            capture_output=True,
            text=True,
            timeout=300,
            env=dict(clone_env),
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"git clone failed: {(completed.stdout + completed.stderr)[-2000:]}"
            )
        sandbox = cls(
            repo,
            base_branch,
            agent_branch_name,
            "origin",
            push_env_factory=push_env_factory,
        )
        sandbox._configure_identity()
        return sandbox

    def _configure_identity(self) -> None:
        self._require_git(["config", "user.name", "issue2pr-agent"])
        self._require_git(["config", "user.email", "agent@issue2pr.invalid"])

    def create_agent_branch(self) -> None:
        self._require_git(["switch", "-c", self.agent_branch])

    def stage_all(self) -> None:
        self._require_git(["add", "-A"])

    def changed_paths(self) -> tuple[str, ...]:
        output = self._require_git(["diff", "--cached", "--name-only", "-z"])
        return tuple(item for item in output.split("\0") if item)

    def staged_diff(self) -> str:
        return self._require_git(["diff", "--cached", "--no-color"])

    def diff_stats(self) -> tuple[int, int, int]:
        output = self._require_git(["diff", "--cached", "--numstat", "-z"])
        files = 0
        insertions = 0
        deletions = 0
        for entry in output.split("\0"):
            if not entry:
                continue
            parts = entry.split("\t")
            if len(parts) < 3:
                continue
            files += 1
            if parts[0].isdigit():
                insertions += int(parts[0])
            if parts[1].isdigit():
                deletions += int(parts[1])
        return files, insertions, deletions

    def commit(self, message: str) -> str:
        self._require_git(["commit", "-m", message])
        return self.head_sha()

    def head_sha(self) -> str:
        return self._require_git(["rev-parse", "HEAD"]).strip()

    def push(self) -> tuple[bool, str]:
        env = self._push_env_factory() if self._push_env_factory else None
        result = safe_push(
            self.repo,
            self.origin,
            self.agent_branch,
            self.base_branch,
            env=env,
        )
        return result.ok, result.output


class WorkspaceFactory(Protocol):
    def __call__(self, run_dir: Path, task: IssueTask) -> GitSandbox: ...


class FixtureWorkspaceFactory:
    def __init__(self, fixture_root: Path, mutation: FileEdit) -> None:
        self.fixture_root = fixture_root
        self.mutation = mutation

    def __call__(self, run_dir: Path, task: IssueTask) -> GitSandbox:
        run_dir = run_dir.resolve()
        seed = run_dir / "seed"
        run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.fixture_root, seed, ignore=_IGNORE)
        apply_edit(seed, self.mutation)
        return GitSandbox.from_directory(
            seed, run_dir, task.base_branch, agent_branch(task)
        )


class CopyWorkspaceFactory:
    def __init__(self, source: Path) -> None:
        self.source = source.resolve()

    def __call__(self, run_dir: Path, task: IssueTask) -> GitSandbox:
        return GitSandbox.from_directory(
            self.source, run_dir, task.base_branch, agent_branch(task)
        )


class RemoteWorkspaceFactory:
    def __init__(self, token_provider: Callable[[IssueTask], str]) -> None:
        self.token_provider = token_provider

    def __call__(self, run_dir: Path, task: IssueTask) -> GitSandbox:
        token = self.token_provider(task)
        env = github_git_env(token)
        return GitSandbox.from_clone(
            task.repo,
            run_dir,
            task.base_branch,
            agent_branch(task),
            clone_env=env,
            push_env_factory=lambda: github_git_env(self.token_provider(task)),
        )
