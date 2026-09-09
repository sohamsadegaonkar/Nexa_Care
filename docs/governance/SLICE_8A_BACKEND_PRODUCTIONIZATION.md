# Slice 8A — Backend Productionization Qualification

**Status:** IMPLEMENTATION COMPLETE / INTERNAL QUALIFICATION CANDIDATE — LIVE CLOUD `NOT_RUN`

**Authoritative base `main`:** `dd81d50473f4d78b9f153c65466979d18c53ebb5`

**Qualification branch:** `slice-8a-backend-productionization`

## Purpose

Slice 8A hardens the existing Nexa Care backend for a production-like runtime. It does not deploy infrastructure and does not convert repository/CI evidence into a live AWS qualification claim.

The governing distinction remains:

`IMPLEMENTED != INTERNALLY QUALIFIED != DEPLOYED != EXTERNALLY QUALIFIED != PHYSICALLY QUALIFIED`.

## Productionization scope and findings

| Area | Baseline finding | Slice 8A control |
| --- | --- | --- |
| Production configuration | Runtime settings were validated by several independent call sites and deployment scripts, leaving drift possible. | `app/core/production_runtime.py` establishes one fail-closed production contract reused by pilot preflight and API startup. |
| Secrets | Provider registration/contact secrets and operational diagnostics credentials were not fully represented in the ECS runtime contract. | Full secret set is required/injected; placeholders, undersized security secrets, cross-purpose secret reuse, and static AWS credentials are rejected without echoing values. |
| Database migration/startup | Migration was correctly separate, but API startup did not independently prove the live database was on the exact repository Alembic head. | API startup performs read-only PostgreSQL reachability plus exact single-head `alembic_version` verification before workers/traffic; API never runs migrations. |
| PostgreSQL/Redis dependency behavior | Public readiness leaked implementation exception classes; generic rate limiting could fail open on Redis outage. | Dependency failures produce coarse readiness classes; security rate limiting fails closed; startup refuses production-like service when required dependencies are unavailable. |
| KMS/S3 runtime integration | Deployment metadata existed, but startup did not prove KMS/S3 security posture. | Read-only startup checks require enabled encrypt/decrypt KMS keys, S3 default SSE-KMS, complete public-access blocking and versioning. |
| Structured logging/metrics | Request logging could accept unsafe trace IDs/raw path values; Prometheus metrics were publicly mounted. | Server-safe trace IDs, route templates, structured JSON logging, and operations-token protected `/metrics`. |
| Error sanitization | Global erased-data response included a patient identifier and health responses exposed diagnostic classes/counts. | Global response removes the identifier; public readiness is coarse; detailed aggregate health is protected. |
| Rate limiting | Generic Redis limiter explicitly failed open. | Generic, OTP and push security throttles fail closed on enforcement backend failure. |
| Background jobs | Individual worker loops handled cycle failures, but top-level exits were not supervised uniformly. | Audit-outbox, failure-quarantine and optional provider-reconciliation workers run under restart supervisors with bounded exponential delay and independent shutdown. |
| Operational health | `/health` mixed public readiness and internal diagnostics. | `/healthz` is dependency-free liveness, `/health` coarse readiness, `/ops/health` protected detailed readiness, `/metrics` protected metrics. |
| Deployment/rollback | Existing deployment artifacts omitted late-added provider secrets and rollback language could be interpreted independently of schema compatibility. | ECS/runtime contract includes complete secret/config set; immutable-image rollback is schema-gated and explicitly forbids automatic PostgreSQL downgrade. |
| Retention | Older operational language could be read as permission to configure lifecycle retention. | Runbook explicitly preserves the pending security/privacy/legal retention approval and prohibits lifecycle changes before approval. |

## Startup safety contract

For `staging`, `preview`, `pilot`, and `production`, startup must fail before background workers begin unless all of the following are true:

1. the production configuration and secret contract is valid;
2. PostgreSQL is reachable through `postgresql+asyncpg`;
3. the repository exposes exactly one Alembic head and the database `alembic_version` exactly matches it;
4. Redis is reachable over the required TLS configuration;
5. the configured application/storage KMS keys are enabled for `ENCRYPT_DECRYPT`;
6. the configured S3 bucket is reachable, has default SSE-KMS, all four public-access-block controls enabled, and versioning enabled.

The preflight is read-only. It does not migrate the database, mutate AWS resources, upload documents, or perform a live Textract call.

## Operational access boundary

Production-like detailed operational surfaces require the independent `OPERATIONS_AUTH_TOKEN` supplied via `X-Nexa-Operations-Token`:

- `/ops/health`
- `/metrics`

Public `/healthz` and `/health` intentionally remain non-diagnostic. They must not expose credentials, URLs, bucket/key identifiers, exception messages/classes, patient/provider identifiers, raw outbox records, or detailed queue counts.

## Deployment and rollback contract

- Deploy only an immutable image identity.
- Run database migrations as a separate controlled release task; API startup never runs Alembic upgrade/stamp/downgrade.
- Do not enable traffic until startup preflight and readiness pass.
- Rollback to a prior application image only if its schema compatibility gate accepts the current database revision.
- Do **not** automatically downgrade PostgreSQL; use an approved corrective forward revision after impact review.
- Preserve PostgreSQL, S3 and audit evidence during rollback.
- Invalidate sessions and require fresh consent before protected workflows resume after rollback.

## Internal qualification gates

Slice 8A is eligible for internal qualification only when the exact evidence-bearing PR head satisfies all of these gates:

1. Ruff/lint succeeds.
2. Backend CI partitions A, B and C complete with zero failures, zero errors and zero skips under their qualification assertions.
3. Production runtime/config/preflight regressions pass.
4. Rate-limit failure behavior is fail-closed.
5. Public readiness/error sanitization and operations-token boundaries pass regression tests.
6. Background-worker supervision/start-before-preflight boundaries pass regression tests.
7. Deployment templates, direct operator preflight entrypoint, AWS metadata read-only boundary and rollback/retention contracts pass regression tests.
8. Frontend CI tests and production/workspace builds pass.
9. Native iOS and Android CI compilation passes.
10. PR review comments/threads contain no unresolved finding.
11. `main` is still the qualified base, or the branch is explicitly requalified against any newer `main` before merge.

Measured CI evidence is intentionally not recorded in this initial candidate revision. It will be added only after successful first-pass qualification, and that evidence-bearing commit will itself receive a fresh exact-head CI qualification before merge.

## Explicit nonclaims

Slice 8A repository and CI evidence does **not** claim any of the following:

- no ECS/Fargate service has been deployed by this slice;
- no live task-role credential chain has been exercised;
- no live KMS or S3 startup preflight has been executed;
- no live dedicated PostgreSQL or Redis dependency/outage qualification has been executed;
- no live rollback has been performed;
- no live CloudWatch/Prometheus scrape has been qualified;
- Slice 7B live cloud/runtime qualification remains `NOT_RUN`;
- document extraction live accuracy, external FHIR interoperability, ABDM/NHA machine-contract qualification, operational retention approval, and physical-device qualification remain governed by their existing separate boundaries.

Therefore a green Slice 8A PR can establish **internal software/runtime-contract qualification only**. Live cloud qualification remains a separate execution gate.
