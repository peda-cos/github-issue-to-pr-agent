from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .agent import Policy
from .config import ConfigError, Settings
from .dispatcher import Dispatcher
from .eval import FIXTURE_ROOT, compare, format_report, run_eval
from .github_app import GithubAppTokenSource, UrllibTransport
from .github_client import FakeGitHubClient, GitHubClient, RepoScopedGithubClient
from .inference import infer
from .llm import LlmPolicy
from .models import IssueTask
from .policy import ReferencePolicy
from .sandbox import (
    CopyWorkspaceFactory,
    FixtureWorkspaceFactory,
    RemoteWorkspaceFactory,
    WorkspaceFactory,
)
from .store import StateStore
from .webhook import handle_webhook, serve
from .worker import Worker, default_verifier_factory


def _settings(state: str | None = None, provider: str | None = None) -> Settings:
    base = Settings.from_env()
    if state is not None:
        base = replace(base, state_dir=Path(state))
    if provider is not None:
        base = replace(base, provider=provider)
    base.validate()
    base.ensure_dirs()
    return base


def _app_credentials() -> tuple[int, str, int]:
    raw_id = os.environ.get("GITHUB_APP_ID", "")
    key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
    raw_installation = os.environ.get("GITHUB_INSTALLATION_ID", "")
    if not raw_id or not key_path or not raw_installation:
        raise ConfigError(
            "GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH, and "
            "GITHUB_INSTALLATION_ID are required for real GitHub mode"
        )
    try:
        app_id = int(raw_id)
        installation_id = int(raw_installation)
        private_key = Path(key_path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise ConfigError(f"invalid GitHub App configuration: {exc}") from exc
    return app_id, private_key, installation_id


def _real_github(settings: Settings) -> RepoScopedGithubClient:
    app_id, private_key, installation_id = _app_credentials()
    return RepoScopedGithubClient(
        app_id,
        private_key,
        installation_id,
        base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )


def _token_provider_factory(
    settings: Settings,
) -> Callable[[IssueTask], str]:
    del settings
    app_id, private_key, installation_id = _app_credentials()
    cache: dict[str, GithubAppTokenSource] = {}
    transport = UrllibTransport()

    def token_for(task: IssueTask) -> str:
        """Return a scoped installation token for the task repository."""
        source = cache.get(task.repo)
        if source is None:
            source = GithubAppTokenSource(
                app_id,
                private_key,
                task.installation_id or installation_id,
                task.repo,
                transport,
                base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            )
            cache[task.repo] = source
        return source.token()

    return token_for


def _llm_factory(settings: Settings) -> Callable[[], LlmPolicy]:
    env_key = (
        "ANTHROPIC_API_KEY"
        if settings.provider == "anthropic"
        else "OPENAI_API_KEY"
    )
    api_key = os.environ.get(env_key, "")
    if not api_key:
        raise ConfigError(f"{env_key} is required for {settings.provider}")

    def make_policy() -> LlmPolicy:
        return LlmPolicy(
            settings.provider,
            settings.model,
            api_key,
            price_input_per_mtok=settings.price_input_per_mtok,
            price_output_per_mtok=settings.price_output_per_mtok,
        )

    return make_policy


def _signed(
    secret: str, body: bytes, delivery_id: str, event: str = "issues"
) -> dict[str, str]:
    return {
        "x-hub-signature-256": "sha256="
        + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        "x-github-event": event,
        "x-github-delivery": delivery_id,
        "content-type": "application/json",
    }


def _dispatch(args: argparse.Namespace) -> int:
    settings = _settings(args.state, args.provider)
    store = StateStore(settings.state_dir / "state.db")
    github: GitHubClient
    if args.github == "http":
        github = _real_github(settings)
    else:
        github = FakeGitHubClient(settings.state_dir)
    token_for = _token_provider_factory(settings) if args.workspace == "remote" else None
    llm_factory = (
        _llm_factory(settings) if settings.provider != "reference" else None
    )

    def worker_factory(task: IssueTask) -> Worker:
        case = None
        workspace: WorkspaceFactory
        if args.workspace == "fixture":
            from bench.cases import get_case

            case = get_case(task.issue_number)
            if case is None:
                raise ConfigError(
                    f"no benchmark case for issue #{task.issue_number}"
                )
            workspace = FixtureWorkspaceFactory(FIXTURE_ROOT, case.mutation)
        elif args.workspace == "local":
            if not args.repo_path:
                raise ConfigError("--repo-path is required for --workspace local")
            workspace = CopyWorkspaceFactory(Path(args.repo_path))
        else:
            if token_for is None:
                raise ConfigError("remote workspace requires GitHub App credentials")
            workspace = RemoteWorkspaceFactory(token_for)
        policy_factory: Callable[[], Policy]
        if settings.provider == "reference":
            if case is None:
                raise ConfigError(
                    "reference provider requires --workspace fixture; "
                    "use --provider anthropic|openai for real repositories"
                )
            policy_factory = lambda: ReferencePolicy(case.reference_actions())
        else:
            if llm_factory is None:
                raise ConfigError("LLM provider was not configured")
            policy_factory = llm_factory
        return Worker(
            settings=settings,
            github=github,
            trace_dir=settings.trace_dir,
            workspace_factory=workspace,
            policy_factory=policy_factory,
            verifier_factory=default_verifier_factory(settings),
            hidden_test=case.hidden_test if case is not None else None,
        )

    dispatcher = Dispatcher(store, github, settings)
    if args.once:
        result = dispatcher.dispatch_once(worker_factory)
        if result is None:
            print("queue empty")
        else:
            print(
                f"{result.outcome.value}: #{result.issue_number} "
                f"PR={result.pr_number} cost=${result.cost_usd:.4f}"
            )
    else:
        results = dispatcher.drain(worker_factory)
        print(f"processed={len(results)}")
        for result in results:
            print(
                f"{result.outcome.value}: #{result.issue_number} "
                f"PR={result.pr_number} cost=${result.cost_usd:.4f}"
            )
    return 0


def _demo(args: argparse.Namespace) -> int:
    from bench.cases import CASES

    state = Path(args.state)
    settings = replace(
        Settings.from_env({}),
        state_dir=state,
        webhook_secret="demo-only-secret",
        provider="reference",
    )
    settings.ensure_dirs()
    case = next(c for c in CASES if c.expected_outcome == "verified")
    delivery = f"demo-{uuid.uuid4().hex[:8]}"
    repo = f"demo/{case.id}-{delivery.removeprefix('demo-')}"
    payload = {
        "action": "labeled",
        "label": {"name": settings.trigger_label},
        "issue": {
            "number": case.issue_number,
            "title": case.title,
            "body": case.body,
            "labels": [{"name": "bug"}],
        },
        "repository": {"full_name": repo, "default_branch": "main"},
        "installation": {"id": 1},
    }
    body = json.dumps(payload).encode()
    store = StateStore(state / "state.db")
    status, text = handle_webhook(
        _signed(settings.webhook_secret or "", body, delivery), body, store, settings
    )
    if status != 202:
        print(f"webhook failed: {status} {text}", file=sys.stderr)
        return 2
    github = FakeGitHubClient(state)
    dispatcher = Dispatcher(store, github, settings)

    def worker_factory(task: IssueTask) -> Worker:
        del task
        return Worker(
            settings=settings,
            github=github,
            trace_dir=settings.trace_dir,
            workspace_factory=FixtureWorkspaceFactory(FIXTURE_ROOT, case.mutation),
            policy_factory=lambda: ReferencePolicy(case.reference_actions()),
            verifier_factory=default_verifier_factory(settings),
            hidden_test=case.hidden_test,
        )

    result = dispatcher.dispatch_once(worker_factory)
    if result is None:
        print("demo produced no result", file=sys.stderr)
        return 2
    pull_requests = github.pull_requests(repo)
    print(f"outcome={result.outcome.value} pr={result.pr_number}")
    print(f"branch={result.branch} trace={result.trace_path}")
    if pull_requests:
        print("\n" + str(pull_requests[0]["body"]))
    return 0


def _eval(args: argparse.Namespace) -> int:
    report = run_eval(Path(args.state))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
        print(f"results: {Path(args.state) / 'eval-results.json'}")
    return 0


def _infer(args: argparse.Namespace) -> int:
    environment = infer(Path(args.path))
    print(f"language: {environment.language}")
    print(f"package_manager: {environment.package_manager}")
    print(f"test_command: {' '.join(environment.test_command)}")
    print(f"coverage_supported: {environment.coverage_supported}")
    print(f"evidence: {', '.join(environment.evidence)}")
    if environment.dockerfile:
        print("\nDockerfile.issue2pr:\n" + environment.dockerfile)
        if args.write:
            target = Path(args.path) / "Dockerfile.issue2pr"
            if target.exists():
                print(f"not overwriting {target}")
            else:
                target.write_text(environment.dockerfile, encoding="utf-8")
                print(f"wrote {target}")
    else:
        print("Dockerfile already exists; no synthesized file")
    return 0


def _budget(args: argparse.Namespace) -> int:
    settings = _settings(args.state)
    day = args.day or datetime.now(UTC).date().isoformat()
    store = StateStore(settings.state_dir / "state.db")
    rows = store.usage(day)
    if not rows:
        print(f"no budget usage for {day}")
        return 0
    print(f"{'repo':<40} {'spent':>10} {'reserved':>10} {'prs':>5}")
    for row in rows:
        print(
            f"{row.repo:<40} {row.spent_usd:>10.4f} {row.reserved_usd:>10.4f} "
            f"{row.opened_prs:>5}"
        )
    return 0


def _compare(args: argparse.Namespace) -> int:
    local = json.loads(Path(args.local).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    print(compare(local, baseline), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issue2pr")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="run the webhook receiver")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8787)
    serve_parser.add_argument("--state", default=None)

    dispatch_parser = sub.add_parser("dispatch", help="dispatch queued tasks")
    dispatch_parser.add_argument("--once", action="store_true")
    dispatch_parser.add_argument("--drain", action="store_true")
    dispatch_parser.add_argument("--state", default=None)
    dispatch_parser.add_argument("--github", choices=["fake", "http"], default="fake")
    dispatch_parser.add_argument(
        "--workspace", choices=["fixture", "local", "remote"], default="fixture"
    )
    dispatch_parser.add_argument("--repo-path", default=None)
    dispatch_parser.add_argument(
        "--provider", choices=["reference", "anthropic", "openai"], default=None
    )

    demo_parser = sub.add_parser("demo", help="end-to-end offline demo")
    demo_parser.add_argument("--state", default=".issue2pr/demo")

    eval_parser = sub.add_parser("eval", help="run the 30-issue benchmark")
    eval_parser.add_argument("--json", action="store_true")
    eval_parser.add_argument("--state", default=".issue2pr/eval")

    infer_parser = sub.add_parser("infer", help="infer language and Dockerfile")
    infer_parser.add_argument("path")
    infer_parser.add_argument("--write", action="store_true")

    budget_parser = sub.add_parser("budget", help="show daily budget usage")
    budget_parser.add_argument("--day", default=None)
    budget_parser.add_argument("--state", default=None)

    compare_parser = sub.add_parser("compare", help="compare eval results")
    compare_parser.add_argument("local")
    compare_parser.add_argument("baseline")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            settings = _settings(args.state)
            serve(settings, args.host, args.port)
            return 0
        if args.command == "dispatch":
            if args.github == "http" and args.workspace == "fixture":
                raise ConfigError(
                    "real GitHub mode requires --workspace local or remote"
                )
            return _dispatch(args)
        if args.command == "demo":
            return _demo(args)
        if args.command == "eval":
            return _eval(args)
        if args.command == "infer":
            return _infer(args)
        if args.command == "budget":
            return _budget(args)
        if args.command == "compare":
            return _compare(args)
        parser.error(f"unknown command: {args.command}")
    except (ConfigError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
