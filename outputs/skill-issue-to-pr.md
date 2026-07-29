# Skill: issue-to-PR autonomous agent operator

## What it does

Turns a maintainer-labeled issue into a verified, review-ready PR. It does not merge. It
runs a bounded agent in an isolated checkout, gates the result on the full suite plus
coverage, and posts rationale, diff metrics, cost, turns, and a trace.

## Trigger

Apply the label `@agent fix this` to an issue. On an existing issue or PR, a maintainer can
comment:

- `@agent fix this` — rerun the fix from the default branch
- `@agent retry` — retry after a failure or budget reset
- `@agent status` — show the latest outcome, PR, turns, cost, and trace

Free-form questions are not handled. Ask for a code change by re-running the fix.

## Run it

```bash
# install, test, typecheck
./verify.sh

# offline end-to-end proof and benchmark
uv run issue2pr demo
uv run issue2pr eval

# webhook receiver + dispatcher (separate terminals/processes)
uv run issue2pr serve --state /var/lib/issue2pr
uv run issue2pr dispatch --state /var/lib/issue2pr \
  --github http --workspace remote --provider anthropic --drain

# inspect daily ceilings
uv run issue2pr budget --state /var/lib/issue2pr
```

## Read the PR

The PR body has three sections:

1. **Agent rationale** — what changed and why.
2. **Verification** — tests, hidden checks, coverage before/after, diff stats, turns, cost,
   trace, labels.
3. **Safety** — head branch, no force push, protected paths unchanged, base branch.

Every agent PR is `needs-review`. A coverage drop greater than 2 percentage points adds
`coverage-drop`. The PR is never merged or force-pushed by the worker.

## Failure handling

- Failure comments include the outcome, reason, turns, cost, trace, and literal
  `@agent retry`.
- Red CI, hidden-check failure, protected path, detected secret, empty diff, and agent
  limits create no PR.
- Budget denial creates no worker and no PR; retry after 00:00 UTC.
- `@agent status` reports the latest run even if the webhook/worker processes restarted.

## Safety checklist

- App permissions: issues write, PRs write, contents write, workflows read only.
- `main` protected: required review, required `verify`, no force push, no deletion,
  admins enforced, App not in bypass.
- Worker path allowlist blocks `.github/workflows` writes; branch protection remains
  authoritative.
- Installation tokens are scoped to the repository and expire after one hour; never log or
  persist them.
- Run untrusted repositories in a container/cloud sandbox, not local subprocess mode.
- Inspect the trace before trusting a PR that changed test infrastructure.

## Benchmark caveat

`issue2pr eval` uses a deterministic `ReferencePolicy`, not a model. Its 27/30 result
measures pipeline correctness and the verification gate, not LLM coding ability. Hosted
agent comparison requires externally collected JSON and `issue2pr compare`; no hosted
numbers are included.
