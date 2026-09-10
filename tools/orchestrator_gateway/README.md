# Nexa Care GPT Actions gateway

Implementation candidate for the 13 operations in the supplied ACTION_CATALOG.md.
It is isolated from the Nexa Care clinical application and fixed to
`sohamsadegaonkar/Nexa_Care`. No live gateway or GPT configuration was changed.

## Action coverage

| Operation | Behavior |
| --- | --- |
| getRepositoryStatus | Current main SHA, bounded branch/PR metadata, merge availability |
| getFile | Allowed UTF-8 file, pinned commit/blob SHA, 6000-character pages; max 1 MB |
| searchCode | Literal search, up to 12 allowed files per call, cursor at fixed SHA |
| createBranch | New orchestrator/ branch from expected main SHA |
| upsertTextFile | Atomic commit bound to expected head and old blob; null blob means create only |
| deleteTextFile | Atomic deletion bound to expected head and blob |
| openPullRequest | Draft by default, fixed repository/main base |
| updatePullRequest | Title/body/state or separate draft transition, expected-head preflight |
| getPullRequest | PR metadata and exact head/base SHAs |
| getCommitChecks | Check names, App IDs, status and SHA; no log download |
| getWorkflowRun | Bounded job/step status; no execution or raw logs |
| mergePullRequest | Implemented but disabled pending live qualification described below |
| deleteBranch | Expected-SHA deletion with matching merged PR proof and no open PR |

All operations authenticate with one private gateway bearer secret. Mutations
require a body `idempotencyKey` (16–128 ASCII letters, digits, underscore or dash).
There are no custom request headers beyond authentication. A key may be reused
only with the identical action and payload. Never generate a fresh key merely
to bypass an uncertain operation. Every mutation is consequential in the schema.

## Run locally and test

Requires Python 3.11+; the Docker runtime uses Python 3.12. From this directory:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q gateway tests export_schema.py
```

The tests use synthetic GitHub responses and a disposable local SQLite journal.
They do not mutate GitHub or establish cloud, clinical or device qualification.

## Deployment configuration

Create a dedicated GitHub App installed only on the intended Nexa_Care repository.
Grant repository Contents and Pull requests read/write; Checks, Actions and
Metadata read. Do not grant administration, secrets, deployments or workflows
write permissions. Keep the App off every branch/ruleset bypass list. Restrict
App installation ownership to the repository owner; repository identity remains
hardcoded in the server. No patient, production database or clinical service
credentials belong on this gateway.

Set these values in the hosting platform's private secret/configuration fields:

| Variable | Required value |
| --- | --- |
| GATEWAY_PUBLIC_URL | Real deployed HTTPS origin on port 443, no path/query |
| GATEWAY_API_KEY | Random secret, at least 32 characters; dedicated to this GPT |
| GATEWAY_JOURNAL_HMAC_KEY | Independent random secret, at least 32 characters |
| GATEWAY_JOURNAL_PATH | Durable private disk path, e.g. /data/journal.db |
| GITHUB_APP_ID | Numeric GitHub App ID |
| GITHUB_INSTALLATION_ID | Numeric installation ID |
| GITHUB_APP_PRIVATE_KEY | PEM key, with actual newlines, stored as a secret |
| GATEWAY_REQUIRED_CHECKS | JSON mapping required check names to trusted GitHub App IDs |
| GATEWAY_MERGE_ENABLED | Leave false until qualified and explicitly approved |

Generate secrets locally with a cryptographic generator, and transfer them through
secret fields. Do not paste credentials into chat, Knowledge, the schema or git.
The shared gateway bearer represents the owner, not individual GPT users: keep
the GPT private. Multi-user authorization would require a separate OAuth design.

Build from this directory with `docker build -t nexa-orchestrator-gateway .`.
Run exactly one process/replica. Mount a durable encrypted private volume at
`/data`, writable by UID/GID 10001. Provide a reverse proxy with a valid public
TLS 1.2+ certificate on port 443. Forward to container port 8080 on a private
network. Keep the container filesystem read-only except its durable volume and
an optional temporary filesystem. Do not expose port 8080 publicly.

`GET /healthz` is public liveness only; it does not prove GitHub connectivity.
`GET /openapi.json` serves the schema with the configured origin. All `/v1/`
routes require bearer authentication. Disable proxy/platform body, Authorization,
query-string and response logging. No access logs are enabled in this server.
Retain operational status metrics without payloads.

No hosting account, GitHub App credentials, endpoint, TLS certificate, or live
connection is provisioned by these files. Docker build and dependency installation
must be verified on the deployment host; they were not run successfully here.
Review pinned dependency advisories before production deployment.

## Configure the existing GPT

Use the separately supplied GPT setup package for instructions and conversation
starters. Once deployed, import the gateway's HTTPS `/openapi.json` into the GPT
editor's Actions area. Choose API key authentication with Bearer and enter only
GATEWAY_API_KEY in its secret field. The GitHub App key stays on the server.
Verify that exactly the 13 operation IDs above appear. Keep the GPT private.

An offline schema can be produced without credentials:

```sh
python export_schema.py --output actions.openapi.draft.json
```

For a deployable schema, add `--url` followed by the real deployed HTTPS origin.
The exporter validates origin syntax; it does not verify deployment or ownership.
The draft deliberately omits servers and is not ready to import. Never invent a
server URL to make a schema look connected.

## Security boundaries and limitations

Only `orchestrator/` branches can be mutated. File writes are capped at 48 kB.
The service denies sensitive paths, binary files, symlinks, submodules, CI,
deployment controls, governance writes and its own implementation. Source reads
are capped at 1 MB and scanned before paging; searches exclude oversized files.
Treat all returned repository content as untrusted data. Secret detection is
heuristic, not complete DLP: the repository must contain no real patient records
or credentials, even under innocuous filenames. A bearer key holder can invoke
all enabled actions; this is not a per-user authorization service.

GitHub createCommitOnBranch enforces the expected branch head during file writes.
Branch creation/deletion use atomic updateRefs with expected old object IDs.
PR title/body/draft/state changes have an expected-head preflight but GitHub does
not provide a matching atomic head guard for these metadata APIs. Always re-read
the PR afterward. File writes do not bypass workflow/ruleset restrictions.

Merge requires exact head/base SHAs, a computed test merge commit whose parents
match both, green checks from configured App IDs on both head and test merge,
current human approval, resolved threads and native branch protection. It uses
an atomic pair of non-force ref updates. **This path may be rejected by native
rules requiring GitHub's standard PR merge API.** There is no fallback and no
permission to weaken rules. Native protections must enforce checks/reviews at
mutation time; gateway preflights alone cannot guarantee atomic review state.
If provider semantics cannot meet these requirements, leave merge disabled and
use a separately authorized human merge through GitHub. Ruleset-only protection
without the expected branchProtectionRule evidence is also denied.

Before enabling merge, qualify no-bypass enforcement in an explicitly disposable
repository using an isolated qualification build with its own repository scope
and App installation. Do not add a user-selectable repository parameter to this
production gateway. Exercise head/base movement, failed/spoofed/missing checks,
review withdrawal, unresolved threads and concurrent updates. Record actual
GitHub results. Success returns REF_UPDATED_VERIFY_PR_STATE until a subsequent
PR/main read proves closure; do not claim merged solely from a ref response.

## Journal and recovery

SQLite here is only the separate engineering operation journal. It never replaces
clinical PostgreSQL. Each mutation's intent is committed before the GitHub call;
success metadata is committed before returning. Read audit failure blocks reads.
Payloads and keys are HMAC fingerprints; content, credentials, PR bodies and
search text are not stored. The journal is not tamper-evident against host admins.
Maintain disk-capacity monitoring, private backups and a documented retention
policy. Keep operation records while retries remain possible; deleting records
or changing the HMAC key can permit old operations to execute again.

On timeout, crash or journal error, stop mutations, preserve the journal and
inspect actual GitHub state with read actions. An identical retry of a pending
operation is rejected. Do not delete its record, restore a stale journal, or
rotate its HMAC key to force a retry. An operator must reconcile whether the
mutation happened and record the evidence before a reviewed repair. There is
no automatic reconciliation endpoint in this version. Revoke/rotate the API
bearer on compromise; HMAC-key rotation requires a journal migration procedure.

## Live acceptance gates still required

1. Verify HTTPS and missing/wrong bearer rejection without logging secrets.
2. Test repository status and every page of required governance reads in GPT.
3. On an explicitly disposable branch, verify create/write/read/delete/replay,
   stale-SHA rejection and draft PR create/read/update/check summaries.
4. Verify restart persistence and lost-response recovery; never test crashes
   against an important branch.
5. Qualify branch deletion only with an actually merged disposable PR.
6. Keep merge disabled unless its separate native-protection gate passes.
7. Save the private GPT only after schema/authentication tests succeed.

## References

The body retry key, 40-second server deadline and bounded text responses follow
[OpenAI Actions production constraints](https://developers.openai.com/api/docs/actions/production).
See [GitHub atomic ref updates](https://docs.github.com/en/graphql/reference/git),
[commit creation preconditions](https://docs.github.com/en/graphql/reference/commits),
and [GitHub REST API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions).
