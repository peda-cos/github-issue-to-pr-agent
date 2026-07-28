from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .inference import RepoEnvironment
from .models import RunOutcome
from .sandbox import CommandRunner


@dataclass(frozen=True)
class VerificationRun:
    ok: bool
    output: str
    coverage: float | None = None


class Verifier:
    def __init__(
        self,
        repo: Path,
        environment: RepoEnvironment,
        timeout: float = 300.0,
        runner: CommandRunner | None = None,
    ) -> None:
        self.repo = repo
        self.environment = environment
        self.timeout = timeout
        self.runner = runner or CommandRunner()

    def run_base(self, run_dir: Path) -> VerificationRun:
        return self.run_visible(run_dir, label="base")

    def run_visible(self, run_dir: Path, label: str = "head") -> VerificationRun:
        run_dir.mkdir(parents=True, exist_ok=True)
        if self.environment.language != "python":
            result = self.runner.run(
                list(self.environment.test_command), self.repo, self.timeout
            )
            return VerificationRun(result.ok, result.output, None)
        data_file = run_dir / f"coverage-{label}.data"
        report_file = run_dir / f"coverage-{label}.json"
        test = self.runner.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--data-file={data_file}",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            self.repo,
            self.timeout,
        )
        if not test.ok:
            return VerificationRun(False, test.output, None)
        report = self.runner.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "json",
                f"--data-file={data_file}",
                "-o",
                str(report_file),
            ],
            self.repo,
            60,
        )
        coverage: float | None = None
        if report.ok and report_file.is_file():
            try:
                payload = json.loads(report_file.read_text(encoding="utf-8"))
                total = payload.get("totals", {}).get("percent_covered")
                if isinstance(total, (int, float)):
                    coverage = float(total)
            except (OSError, json.JSONDecodeError, AttributeError):
                coverage = None
        return VerificationRun(test.ok, test.output, coverage)

    def run_hidden(self, source: str | None, run_dir: Path) -> VerificationRun:
        if source is None:
            return VerificationRun(True, "hidden checks skipped", None)
        run_dir.mkdir(parents=True, exist_ok=True)
        hidden_file = run_dir / "hidden_test.py"
        hidden_file.write_text(source, encoding="utf-8")
        env = dict(self.runner.env)
        env["PYTHONPATH"] = str(self.repo)
        result = self.runner.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                str(hidden_file),
            ],
            self.repo,
            self.timeout,
            env=env,
        )
        return VerificationRun(result.ok, result.output, None)


def classify(
    *,
    base_ok: bool,
    visible_ok: bool,
    hidden_ok: bool,
    coverage_base: float | None,
    coverage_head: float | None,
    threshold_pp: float = 2.0,
) -> RunOutcome:
    if not base_ok:
        return RunOutcome.BASE_TESTS_FAILED
    if not visible_ok:
        return RunOutcome.TESTS_FAILED
    if not hidden_ok:
        return RunOutcome.HIDDEN_FAILED
    if (
        coverage_base is not None
        and coverage_head is not None
        and coverage_head - coverage_base < -threshold_pp
    ):
        return RunOutcome.COVERAGE_LOW
    return RunOutcome.VERIFIED
