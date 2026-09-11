# Validation — 2026-09-10

Status: IMPLEMENTED locally; live integration BLOCKED pending provisioning and
editor access. The existing GPT has not been edited.

- Repository: sohamsadegaonkar/Nexa_Care.
- Starting commit: b5e6152af001d297338f476859d0fe00d432142e.
- Local branch: feature/gpt-orchestrator-gateway.
- Refreshed base before publication: 859bde2aeaefe1c172e0e224746e4cbfffe4dac4.
- User authorized commit/push and draft PR publication on 2026-09-10.
  See the pull request for its current commit and remote check status.
  No gateway deployment or GPT editor change is claimed by this report.
- Added: gateway modules (policy, requests, HTTP/OpenAPI, service, GitHub adapter,
  settings, journal, runner), 55 synthetic tests, schema exporter, Dockerfile,
  dependency pins, deployment guide and implementation decisions.
- Added focused CI: .github/workflows/ci-orchestrator-gateway.yml runs the
  gateway suite, compilation, schema export and container build without secrets.
- Changed governance: proposed SEC-053 entry in SECURITY_NON_REGRESSION.md.
  Adds the engineering gateway boundary; weakens no existing rule.
- Applicable invariants: fixed repository scope, least privilege, bounded
  disclosure, expected-SHA writes, durable idempotency, fail-closed audit,
  native-protection enforcement and truthful evidence.
- Patient/clinical runtime changes: none. Clinical migrations: none.
  The separate engineering journal initializes its own local SQLite tables.

## Passed locally

`python -m unittest discover -s tests -p '*_suite.py' -q`: 55 tests passed, 0 failed, 0 skipped.
Tests include synthetic service calls, local aiohttp HTTP integration, real
SQLite persistence/restart, RSA signing verification, stale-SHA/path/content
rejections, uncertain replay, journal failure, redirect and response bounds,
review/check gates, and exact 13-operation OpenAPI parity.

`python -m compileall -q gateway tests export_schema.py`: passed.
`ruff check tools/orchestrator_gateway` and `ruff format --check tools/orchestrator_gateway`: passed.
`git diff --check`: passed for the final patch; new text files checked separately.
Schema JSON and local component references: checked by tests/export validation.
All five mandatory repository governance/agent files fit the bounded read policy.

## NOT_RUN / limitations

- Actual GitHub App token exchange, API/GraphQL behavior and provider permissions.
- Native protected-branch merge and concurrent live review/check changes.
- Live write/replay/restart qualification or fault injection against GitHub.
- Real GPT schema import/authentication, action calls, editor save or publication.
- Public hosting, DNS/TLS, reverse-proxy logs, container build or deployment.
- Fresh dependency installation: attempted earlier but network approval was
  cancelled; tests used already-installed matching dependencies.
- Dependency vulnerability scan. Ruff became available during publication;
  scoped lint and formatting checks passed after fixing import/style issues.
- Repository CI and full backend/frontend/clinical infrastructure suites;
  this independent gateway imports no clinical application modules.
- Legal, privacy, regulatory, physical-device or production qualification.

Merge is disabled by default. Its atomic ref approach may be rejected by native
PR rules and must never be activated by weakening them. PR metadata preconditions
are not atomic. Secret scanning is heuristic. The journal requires one instance,
a durable private volume and operator reconciliation of uncertain operations.
See README.md for deployment and acceptance gates. Security-owner review pending.
