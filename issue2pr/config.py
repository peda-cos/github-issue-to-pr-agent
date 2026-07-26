from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Invalid environment configuration."""


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be positive")
    return value


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    state_dir: Path = Path(".issue2pr")
    webhook_secret: str | None = field(default=None, repr=False)
    trigger_label: str = "@agent fix this"
    allowed_repos: frozenset[str] = frozenset()
    max_turns: int = 30
    max_wall_seconds: int = 1800
    max_cost_per_task_usd: float = 20.0
    max_daily_cost_per_repo_usd: float = 100.0
    max_prs_per_repo_per_day: int = 5
    coverage_drop_threshold_pp: float = 2.0
    test_timeout_seconds: int = 300
    provider: str = "reference"
    model: str = ""
    price_input_per_mtok: float = 0.0
    price_output_per_mtok: float = 0.0
    trace_url_template: str | None = None

    @property
    def workspace_root(self) -> Path:
        return self.state_dir / "runs"

    @property
    def trace_dir(self) -> Path:
        return self.state_dir / "traces"

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        if not self.trigger_label.strip():
            raise ConfigError("trigger label must not be empty")
        for name, value in (
            ("max_turns", self.max_turns),
            ("max_wall_seconds", self.max_wall_seconds),
            ("test_timeout_seconds", self.test_timeout_seconds),
        ):
            if value <= 0:
                raise ConfigError(f"{name} must be positive")
        if self.max_cost_per_task_usd <= 0:
            raise ConfigError("max_cost_per_task_usd must be positive")
        if self.max_daily_cost_per_repo_usd <= 0:
            raise ConfigError("max_daily_cost_per_repo_usd must be positive")
        if self.max_prs_per_repo_per_day <= 0:
            raise ConfigError("max_prs_per_repo_per_day must be positive")
        if self.coverage_drop_threshold_pp < 0:
            raise ConfigError("coverage_drop_threshold_pp must be non-negative")
        if self.provider not in {"reference", "anthropic", "openai"}:
            raise ConfigError("ISSUE2PR_PROVIDER must be reference, anthropic, or openai")
        if self.provider != "reference":
            if not self.model:
                raise ConfigError("ISSUE2PR_MODEL is required for a real provider")
            if self.price_input_per_mtok <= 0 or self.price_output_per_mtok <= 0:
                raise ConfigError(
                    "ISSUE2PR_PRICE_INPUT_PER_MTOK and "
                    "ISSUE2PR_PRICE_OUTPUT_PER_MTOK are required for a real provider"
                )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        allowed_raw = source.get("ISSUE2PR_ALLOWED_REPOS", "")
        allowed = frozenset(
            item.strip() for item in allowed_raw.split(",") if item.strip()
        )
        settings = cls(
            state_dir=Path(source.get("ISSUE2PR_STATE_DIR", ".issue2pr")),
            webhook_secret=source.get("ISSUE2PR_WEBHOOK_SECRET") or None,
            trigger_label=source.get("ISSUE2PR_TRIGGER_LABEL", "@agent fix this"),
            allowed_repos=allowed,
            max_turns=_int(source, "ISSUE2PR_MAX_TURNS", 30),
            max_wall_seconds=_int(source, "ISSUE2PR_MAX_WALL_SECONDS", 1800),
            max_cost_per_task_usd=_float(source, "ISSUE2PR_MAX_COST_PER_TASK", 20.0),
            max_daily_cost_per_repo_usd=_float(
                source, "ISSUE2PR_MAX_DAILY_COST_PER_REPO", 100.0
            ),
            max_prs_per_repo_per_day=_int(source, "ISSUE2PR_MAX_PRS_PER_DAY", 5),
            coverage_drop_threshold_pp=_float(
                source, "ISSUE2PR_COVERAGE_DROP_THRESHOLD", 2.0
            ),
            test_timeout_seconds=_int(source, "ISSUE2PR_TEST_TIMEOUT", 300),
            provider=source.get("ISSUE2PR_PROVIDER", "reference").strip() or "reference",
            model=source.get("ISSUE2PR_MODEL", "").strip(),
            price_input_per_mtok=float(
                source.get("ISSUE2PR_PRICE_INPUT_PER_MTOK", "0") or "0"
            ),
            price_output_per_mtok=float(
                source.get("ISSUE2PR_PRICE_OUTPUT_PER_MTOK", "0") or "0"
            ),
            trace_url_template=source.get("ISSUE2PR_TRACE_URL_TEMPLATE") or None,
        )
        settings.validate()
        return settings
