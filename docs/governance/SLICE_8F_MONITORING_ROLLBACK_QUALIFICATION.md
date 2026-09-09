# Slice 8F — Production Monitoring and Rollback Runtime Qualification

Status: **ROLLBACK/MONITORING GATE IMPLEMENTED; LIVE QUALIFICATION BLOCKED BY PILOT AWS/TARGET WIRING**

Authoritative base: `ddf7417a752a6b3de54f182d4e42eb9c733331d1`

Qualification branch: `slice-8f-monitoring-rollback-qualification`

## Purpose

Slice 8F turns the existing rollback runbook into an executable fail-closed
qualification gate without switching live traffic. It proves that an explicitly
selected prior immutable backend task definition can still satisfy the current
production startup contract before an operator considers a real rollback.

## Permanent rollback-candidate gate

`.github/workflows/rollback-runtime-qualification.yml` runs in the protected
`pilot` environment and requires explicit values for the AWS OIDC role, ECS
cluster/service, ECR repository, prior rollback task definition, deployed API
base URL and operations token.

The candidate is never inferred from an ECS revision number or mutable image
tag. It must be an explicitly configured task definition different from the
currently deployed service task definition.

Before launching anything, the workflow requires:

- Fargate + `awsvpc` compatibility;
- exactly one essential application container;
- an image pinned by `@sha256:...`;
- that digest to exist in the configured ECR repository;
- an application task role;
- `awslogs` logging;
- no static AWS credential variables in task environment or secrets;
- a stable current ECS service;
- usable network configuration copied from the current service.

## Isolated runtime proof

The candidate is launched only as a one-off `ecs run-task` Fargate task. The
workflow does **not** call `ecs update-service`, does not register a deployment,
and does not route user traffic to the candidate.

Its command is overridden to execute the candidate image's own
`run_production_startup_preflight()` implementation. A zero exit therefore
requires that candidate to accept all production-like startup gates it knows
about, including:

- static production configuration;
- PostgreSQL reachability;
- exact candidate repository migration head versus the current database
  `alembic_version`;
- TLS Redis reachability;
- KMS readiness;
- S3 security/readiness.

This is intentionally strict. An older image whose embedded migration head no
longer matches the current database is **not rollback-compatible**, even if its
container could otherwise start.

## Monitoring proof

After the isolated candidate task exits successfully, the workflow rechecks the
currently deployed service without changing it:

- public `/healthz`;
- public `/health`;
- protected `/ops/health`;
- protected `/metrics`.

The operations token is supplied only from the protected environment and is not
printed.

## Measured live attempt

The corrected qualification workflow was actually executed on isolated head
`0333817550adaebf3bf33ac9d38c604c3b23383b`:

- workflow run: `34402566056`
- result: fail closed before AWS authentication
- marker: `ROLLBACK_RUNTIME_QUALIFICATION=BLOCKED_MISSING_ACCOUNT_WIRING`
- required pilot inputs missing: `7 / 7`
- AWS OIDC configuration executed: **NO**
- ECS one-off task launched: **NO**
- ECS service changed: **NO**
- traffic switched: **NO**
- database migration/downgrade executed: **NO**
- production monitoring endpoint probe executed: **NO**

This is concrete blocker evidence, not a live rollback-runtime PASS.

## Non-mutation invariants

The qualification workflow contains no command to:

- update the ECS service;
- change desired count;
- modify load-balancer routing;
- run Alembic upgrade/downgrade;
- run the pilot migration script;
- modify PostgreSQL, Redis, KMS, S3 retention, or audit state as part of the
  rollback exercise.

A real rollback remains an operator-controlled incident action after this
preflight passes. The runbook's traffic stop, evidence preservation, session
invalidation and fresh-consent requirements remain authoritative.

## Completion conditions

Repository-side 8F work is complete when the workflow, tests and governance
contract pass normal CI. Live rollback/runtime qualification additionally
requires the protected pilot environment to provide the AWS identity/targets
and an explicitly selected previously qualified task definition, followed by a
successful one-off preflight and current-service monitoring check.

## Nonclaims

Until that live run succeeds, this slice does not claim:

- a rollback was performed;
- any production traffic was switched;
- the prior image is compatible with the current operational database;
- pilot AWS runtime qualification PASS;
- production deployment qualification PASS.
