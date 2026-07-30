# GitHub Issue-to-PR Autonomous Agent

Label an issue `@agent fix this`; a signed GitHub webhook enqueues work, a dispatcher
enforces per-repo budgets, an isolated checkout is built, a bounded agent edits code and
runs tests, verification gates the result, and a review-ready PR is posted with rationale,
coverage delta, cost, turns, and a trace.

This is the self-hosted, offline-first core of the async cloud coding-agent product shape
(AWS Remote SWE Agents, Cursor Background Agents, OpenAI Codex cloud, Google Jules). The
repository runs the complete pipeline locally with deterministic fixtures; real GitHub,
LLM, and container boundaries are implemented as narrow adapters and documented below.

## Architecture

```text
GitHub issue label / PR comment
        |
        v
webhook.py  HMAC-SHA256 verify -> parse trigger -> SQLite task queue (SQS stand-in)
        |
        v
dispatcher.py  atomic claim -> per-repo/day cost + PR reservations -> worker
        |
        v
worker.py
  workspace: fixture copy / local copy / real git clone with scoped App token
  inference: language, package manager, test command, Dockerfile.issue2pr
  preflight: base tests + base coverage
  agent: bounded tool loop (read/edit/search/repo_map/run_tests/git/finish)
  safety: path allowlist, .github/workflows write block, secret scan, no force push
  verify: full visible suite + hidden benchmark checks + coverage delta
  post: commit -> push agent branch -> PR needs-review (+ coverage-drop) or issue comment
        |
        v
FakeGitHubClient (offline artifact)  or  HttpGitHubClient / GitHub App token
```

The production substitutions are explicit:

| Production | Local implementation | Real mode |
|---|---|---|
| Lambda + SQS | `lambda_handler` / local HTTP server, SQLite queue with delivery-id idempotency and atomic claim | replace the queue behind the same claim interface |
| ECS Fargate / Daytona / E2B | per-run directory, local bare remote, sanitized subprocesses and timeouts | `--workspace remote` clones with an installation token; generated Dockerfile is the container recipe |
| Claude / Codex loop | real bounded loop with `ReferencePolicy` fixture actions | `LlmPolicy` with `ISSUE2PR_PROVIDER=anthropic|openai` |
| Langfuse | sanitized JSON trace; local path in PR body | set `ISSUE2PR_TRACE_URL_TEMPLATE` to a hosted trace URL |

The SQLite stand-in is not a mock of the workflow: it enforces delivery-id idempotency,
atomic claiming, daily cost reservations, PR-count reservations, and settlement. The local
sandbox is not a security boundary; see [Security](#security).

## Quickstart

Requirements: Python 3.14 and `uv` (Docker and Node are only needed for other-language
target repositories). No API keys or network calls are used by the demo/eval/tests.

```bash
./verify.sh                 # installs pinned deps into .venv, runs tests + mypy
uv run issue2pr demo        # signed webhook -> queue -> worker -> verified fake PR
uv run issue2pr eval        # 30 seeded issues, measured metrics
uv run issue2pr infer path/to/repo [--write]
uv run issue2pr budget --state .issue2pr
```

`./verify.sh` is idempotent: it syncs the committed `uv.lock` into `.venv` and `.uv-cache`,
then runs pytest and `mypy --strict issue2pr`.

The demo prints the full PR body. The evaluator writes `eval-results.json` and prints
pass rate, outcomes, coverage delta, diff size, turns, cost, and p50 latency.

## What is real, what is deterministic

- **Real:** HMAC validation, webhook trigger parsing and authorization, task idempotency,
  budget reservation/settlement, git branch creation, commits, pushes to a real remote,
  file tools, subprocess test execution, coverage.py measurement, hidden verification,
  protected-path checks, secret scanning/redaction, PR bodies, GitHub App JWT signing and
  narrowed installation-token requests, Anthropic/OpenAI request/response adapters.
- **Deterministic local substitutes:** the `ReferencePolicy` replays case-specific
  read/edit/test/finish actions with zero token usage; `FakeGitHubClient` writes PR and
  comment artifacts locally; the benchmark fixture supplies 30 seeded mutations and strict
  hidden tests. The reference policy measures the **pipeline**, not LLM capability.
- **Real mode:** `HttpGitHubClient` posts actual PRs/comments; `RemoteWorkspaceFactory`
  clones with a repo-scoped, one-hour installation token; `LlmPolicy` uses real provider
  APIs, reports provider token usage, and computes cost from configured per-million-token
  prices. No live credentials exist in this repository or its tests.

## Agent loop and invariants

The loop enforces `30` turns, `1800` seconds, and `$20` per task by default. It stops on
`finish` only with a non-empty rationale. Tools are restricted to the workspace:
`read_file`, `write_file`, `edit_file`, `search` (ripgrep with a Python fallback),
`repo_map` (Python AST + regex symbol map), `run_tests`, and read-only `git`.
Writes under `.github/workflows` are rejected by the tool and re-checked on the staged diff.
The final staged diff is scanned for secret-shaped content; a hit aborts the PR.
The worker only commits on `agent/...`, never on the base branch, and `safe_push` refuses
force flags and base-branch refspecs.

## Verification and benchmark

The in-sandbox gate runs the detected full test command under coverage.py. The base branch
is measured first; the agent branch is measured after edits. A drop greater than 2
percentage points still opens a PR, but labels it `needs-review` + `coverage-drop`; a red
suite, failed hidden check, protected path, secret hit, empty diff, or agent limit produces
no PR and posts a failure comment containing `@agent retry`. Coverage gating is Python-only;
other detected languages get full test execution and remain `needs-review`.

`issue2pr eval` drives all 30 cases through the signed webhook, queue, dispatcher, sandbox,
agent, verification, and fake PR path. Three cases are deliberate negative controls whose
reference patch passes its own regression test but not the stricter hidden check; they
prove the gate blocks PRs rather than inflating the pass rate.

### Measured results

Measured locally with the reference policy over 30 seeded issues (no hosted comparison was
run; see Limitations):

| Metric | Value |
|---|---|
| Issues | 30 |
| Resolved (CI green + coverage OK) | 27 (90.0%) |
| PRs opened | 27 |
| Negative controls rejected | 3 (`hidden_failed`) |
| Average turns | 5 |
| Average files changed | 2.0 |
| Average coverage delta | +0.14 pp |
| Cost | $0.0000 (reference policy; no API spend) |
| Latency p50 | 1.561 s |

These values are from the final local evaluator run; `issue2pr eval` writes
`eval-results.json` under the selected `--state` directory. Latency and coverage vary by
machine. No hosted or LLM benchmark numbers are claimed because none were run.

## Real GitHub / real provider mode

1. Register the GitHub App from `.github/issue2pr-app-manifest.json`, install it on the
   target repository, and set the webhook secret. The App requests only:
   `issues: write`, `pull_requests: write`, `contents: write`, `workflows: read`.
   Installation tokens are further narrowed to the repository and those permissions; they
   expire after one hour and are never persisted.
2. Protect `main` using `deploy/branch-protection.json`: no direct pushes, no force pushes,
   no deletions, required review, required `verify` check, admins included. **Do not add
   the App to the branch-protection bypass list.** Branch protection, not App permission,
   is the final authority.
3. Export real-mode environment variables:

```bash
export ISSUE2PR_WEBHOOK_SECRET='...'
export GITHUB_APP_ID='...'
export GITHUB_APP_PRIVATE_KEY_PATH='/secure/app.private-key.pem'
export GITHUB_INSTALLATION_ID='...'
export ISSUE2PR_PROVIDER='anthropic'          # or openai
export ISSUE2PR_MODEL='<current model id>'
export ISSUE2PR_PRICE_INPUT_PER_MTOK='<current price>'
export ISSUE2PR_PRICE_OUTPUT_PER_MTOK='<current price>'
export ANTHROPIC_API_KEY='...'                # or OPENAI_API_KEY
```

```bash
uv run issue2pr serve --host 127.0.0.1 --port 8787
uv run issue2pr dispatch --github http --workspace remote --provider anthropic --drain
```

`serve` verifies `X-Hub-Signature-256` before parsing. `dispatch --workspace remote` clones
over HTTPS with the token supplied through git's environment (`GIT_CONFIG_*` extraheader),
not through argv or logs. Real operations should run in the container image implied by the
generated `Dockerfile.issue2pr`; local subprocess mode is for development and the benchmark.

See [`docs/github-app.md`](docs/github-app.md) for registration, webhook, branch protection,
and credential details. Operator workflow is summarized in
[`outputs/skill-issue-to-pr.md`](outputs/skill-issue-to-pr.md).

## Security

- **Scoped identity:** App JWT signed RS256; installation token narrowed to one repository
  and four permissions; token cached only until expiry; response permissions validated.
- **No direct main writes:** worker branches are `agent/...`; push guard rejects base-branch
  pushes and force flags; branch protection is documented as authoritative.
- **Path allowlist:** `.github/workflows` writes are blocked by the file tool and the staged
  diff check. `.git` and traversal/symlink escapes are blocked by path resolution.
- **Credential hygiene:** subprocess environments strip token/secret/API-key variables;
  traces, PR bodies, comments, and logs redact token-shaped values and Authorization headers;
  the worker never logs installation tokens.
- **Budgets:** every task reserves the per-task ceiling and a PR slot before work starts;
  settlement records actual cost and releases a slot if no PR is opened. Defaults: `$20`
  per task, `$100` per repo per UTC day, 5 PRs per repo per day.
- **PRs are review-gated:** all agent PRs carry `needs-review`; the coverage gate can add
  `coverage-drop`; hidden checks cannot bypass it.

## Limitations

- The reference policy is a deterministic test double, not a model. Its 90% pipeline pass
  rate must not be read as an LLM success rate.
- Local subprocess workspaces are not a security boundary. Use the generated container or a
  cloud sandbox for untrusted repositories; the path/env controls are defense in depth, not
  isolation.
- SQS, Lambda, ECS/Fargate, Daytona/E2B, and Langfuse are adapter boundaries here, not
  deployed services; no AWS/cloud credentials or live GitHub webhooks were tested.
- Coverage is measured with coverage.py for Python targets only. Node, Go, and Rust are
  detected, tested, and given Dockerfiles; their coverage delta is unavailable.
- Free-form PR follow-up Q&A is not implemented; operator commands are `@agent status` and
  `@agent retry`. The optional capstone exercises (flaky stabilization, hosted comparison,
  budget dashboard alerts, dry-run, branch retention) are not included.
- Hosted-agent comparison is not run; `issue2pr compare LOCAL.json BASELINE.json` computes
  pass rate, cost per resolved issue, and p50 latency over shared issue ids when externally
  collected results are supplied. No baseline numbers are fabricated.

## Design decisions and sources

- A minimal linear agent loop (state, one JSON action per turn, append observation) follows
  the mini-swe-agent lesson that a simple, transparent loop is easier to audit and sandbox
  than a tool-heavy agent scaffold.
- Python AST replaces tree-sitter for the repo map, and coverage.py replaces jacoco, to keep
  the local target stdlib-first and deterministic.
- The GitHub App requests `contents: write` for git push; `.github/workflows` remains
  read-only by permission *and* by worker policy, because GitHub App permissions are not
  path-scoped.
- No force-push code path exists; branch protection is configured without the App in the
  bypass list. Redaction is applied at trace/PR/comment boundaries, not only at logging.
- The benchmark is a self-contained `benchlab` repository so failures are reproducible and
  hidden checks are not visible to the agent.

## References

1. GitHub Docs — [Choosing permissions for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app)
2. GitHub Docs — [Generating an installation access token for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
3. GitHub Docs — [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
4. GitHub Docs — [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
5. SWE-agent — [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)

## License

MIT © 2026 Pedro Monteiro. See [LICENSE](LICENSE).
