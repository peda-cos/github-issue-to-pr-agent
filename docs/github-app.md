# GitHub App and deployment notes

## Register the App

The checked-in manifest is at `.github/issue2pr-app-manifest.json`. Register it from the
GitHub App settings page, replacing `https://example.invalid` URLs with the public webhook
endpoint. The App requests exactly:

| Permission | Level | Why |
|---|---|---|
| Issues | write | read issue triggers and post failure/status comments |
| Pull requests | write | open review-ready PRs and label them |
| Contents | write | clone and push the agent branch |
| Workflows | read | read Actions files; never write them |

The token exchange requests the same permissions plus the target repository, so an
installation token cannot exceed the App's grant. `metadata: read` is implicit. The token
lifetime is one hour; the worker caches it only in memory.

## Webhook

- URL: `https://<host>/webhook`; local development is `http://127.0.0.1:8787/webhook`.
- Secret: a high-entropy value stored only in `ISSUE2PR_WEBHOOK_SECRET`.
- Events: `Issues`, `Issue comments`.
- The receiver fails closed with 503 when the secret is absent, rejects a missing or
  mismatched `X-Hub-Signature-256` with 401, and verifies the raw body before parsing.
- `X-GitHub-Delivery` is the queue idempotency key. GitHub retries do not duplicate work.
- Comment triggers require `OWNER`, `MEMBER`, or `COLLABORATOR` association. Bot comments
  are ignored. `@agent fix this`, `@agent retry`, and `@agent status` are supported;
  free-form follow-up Q&A is not.

## Branch protection

Apply `deploy/branch-protection.json` to `main`:

```bash
gh api -X PUT repos/OWNER/REPO/branches/main/protection \
  --input deploy/branch-protection.json
```

The payload sets `enforce_admins=true`, `allow_force_pushes=false`,
`allow_deletions=false`, one required approval, and a strict required `verify` status. The
App is never added to the bypass list. App permissions cannot express "no force push"; the
branch protection rule is the authoritative control. The worker also refuses force flags
and base-branch refspecs as defense in depth.

## Environment

```bash
ISSUE2PR_WEBHOOK_SECRET=...
ISSUE2PR_STATE_DIR=.issue2pr
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY_PATH=/secure/app.private-key.pem
GITHUB_INSTALLATION_ID=...
GITHUB_API_URL=https://api.github.com          # optional GH Enterprise API
ISSUE2PR_ALLOWED_REPOS=owner/repo,owner/other # optional allowlist
```

For a real model:

```bash
ISSUE2PR_PROVIDER=anthropic|openai
ISSUE2PR_MODEL=<current model identifier>
ISSUE2PR_PRICE_INPUT_PER_MTOK=<current input price>
ISSUE2PR_PRICE_OUTPUT_PER_MTOK=<current output price>
ANTHROPIC_API_KEY=...  # or OPENAI_API_KEY
```

Prices are required in real-provider mode so the cost ceiling is meaningful; no prices are
hardcoded. Model ids and prices change, so the operator supplies current values.

## Process layout

Run the webhook receiver and dispatcher as separate processes:

```bash
uv run issue2pr serve --state /var/lib/issue2pr
uv run issue2pr dispatch --state /var/lib/issue2pr \
  --github http --workspace remote --provider anthropic --drain
```

`--drain` processes the current queue and exits; schedule it from a worker/cron or replace
the loop with a long-running ECS task. The SQLite queue is the local SQS stand-in; its
claim semantics are atomic, but production should replace the persistence with SQS while
keeping the delivery-id idempotency contract.

`--workspace remote` clones `https://github.com/<repo>.git` with an installation token
passed through `GIT_CONFIG_*`/`http.extraheader` environment values. The token never appears
in argv. The synthesized Dockerfile is available from:

```bash
uv run issue2pr infer path/to/repo --write
```

Build and run it in your sandbox provider if you need stronger isolation than local
subprocesses. The worker's local mode is intended for tests, the demo, and trusted repos.

## Credential hygiene checklist

- Keep the private key outside the repository, mode 0600.
- Never put tokens in issue bodies, PR bodies, traces, or command argv.
- Leave `ISSUE2PR_ALLOWED_REPOS` set if the App is installed broadly.
- Rotate the webhook secret and private key through your secret store.
- Run agent jobs in a container/cloud sandbox for untrusted repositories; the local worker
  is not a security boundary.
- Keep the App out of the branch-protection bypass list.
