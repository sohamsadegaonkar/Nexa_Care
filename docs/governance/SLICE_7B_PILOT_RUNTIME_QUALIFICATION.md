# Slice 7B — Pilot Runtime and Cloud-Security Qualification

Status: **SOFTWARE HARNESS QUALIFIED CANDIDATE — LIVE PILOT NOT_RUN**

Baseline `main`: `03aa3bed8097dac989fe81ce34097d17da62f58d`

Branch: `slice-7b-pilot-runtime-qualification`

Pre-attestation qualification head: `7611d0e904065f84934cc2d6dcfae89e05a3aa8c`

## Objective

Qualify one exact immutable synthetic-data pilot deployment rather than inferring deployment safety from repository code, unit tests, or cloud configuration prose.

A Slice 7B PASS means only:

> the exact backend image digest, frontend deployment identity, repository commit, database migration head, and runtime/cloud configuration represented by one sanitized evidence manifest passed every required 7B gate.

It does not mean generic production certification and does not authorize real patient PHI.

## Existing controls reused

Slice 7B builds on existing repository controls, including:

- `scripts/check_pilot_environment.py` for fail-closed static pilot configuration checks and optional AWS STS/KMS/S3 reachability;
- `scripts/run_pilot_migrations.py` for explicit migration to the single repository head rather than startup migration;
- the Fargate deployment and pilot security operations runbooks;
- runtime `/healthz` and `/health` behavior;
- the existing KMS envelope-encryption and encrypted S3 document-storage implementations;
- PostgreSQL/Redis qualification and authority fail-closed invariants established in earlier slices.

Those controls are necessary but do not by themselves prove that a particular deployed runtime exercised every required boundary.

## New evidence contract

`validate_pilot_runtime_evidence.py` defines schema:

`nexa-slice-7b-pilot-runtime-evidence-v1`

The manifest is deliberately sanitized and must contain no credential secret, token, private key, patient identifier, or other patient data.

It binds qualification to:

- exact 40-character repository commit SHA;
- immutable backend `sha256:` image digest;
- stable sanitized frontend deployment identity;
- exact migration head `20260909_device_trust_lifecycle`;
- AWS region `ap-south-1`;
- `synthetic-only` data classification;
- STS assumed-role identity sourced from `ecs-task-role` with static credentials absent;
- KMS-backed application encryption and explicit encryption-context binding;
- S3 `aws:kms` encryption and public-access block;
- dedicated PostgreSQL at the exact migration head;
- dedicated TLS Redis;
- all required runtime checks below.

## Required runtime gates

A manifest with top-level `status=PASS` is rejected unless every gate is exactly `PASS`:

1. ECS task-role credentials;
2. KMS envelope encryption;
3. S3 encrypted storage;
4. S3 public-access block;
5. PostgreSQL connectivity;
6. Redis TLS connectivity;
7. `/healthz`;
8. `/health`;
9. audit-outbox health/backlog;
10. trusted-host enforcement;
11. CORS enforcement;
12. trusted-proxy boundary;
13. rollback rehearsal;
14. session/consent invalidation during rollback;
15. Redis-unavailability fail-closed behavior;
16. KMS-unavailability fail-closed behavior;
17. S3-unavailability fail-closed behavior;
18. database-unavailability fail-closed behavior.

`FAIL`, `BLOCKED`, and `NOT_RUN` are legitimate per-check states. They cannot be hidden by a top-level PASS.

## Software-harness qualification evidence

Pre-attestation exact head:

`7611d0e904065f84934cc2d6dcfae89e05a3aa8c`

### Backend CI

Backend CI #378, run `34348795109`: **SUCCESS**.

- Ruff: **SUCCESS** — `All checks passed!`.
- Partition A — Quality & Pure Unit: **3622 passed**, 396 deselected; JUnit failures/errors/skips `0/0/0`; zero-skip assertion **SUCCESS**.
- Partition B — PostgreSQL Qualification: **SUCCESS**; zero-skip assertion **SUCCESS**.
- Partition C — PostgreSQL + Redis Qualification: **SUCCESS**; zero-skip assertion **SUCCESS**.

This is repository/CI qualification of the validator and existing application guardrails. The CI PostgreSQL/Redis services are not a deployed pilot runtime.

### Frontend CI

Frontend CI #327, run `34348795192`: **SUCCESS**.

- frontend tests: **SUCCESS**;
- Next production build: **SUCCESS**;
- workspace package build: **SUCCESS**;
- iOS native project generation, CocoaPods install, and native-source compile: **SUCCESS**;
- Android native project generation and native-source compile: **SUCCESS**.

Native compilation remains source/build evidence only and creates no physical-device claim.

## Live execution boundary

Repository CI can qualify the evidence validator and the application behavior it can exercise locally. It cannot truthfully manufacture live ECS/Fargate, task-role, KMS key, S3 bucket policy, deployed frontend identity, or cloud-outage evidence.

Therefore Slice 7B remains **LIVE PILOT NOT_RUN** until an authorized pilot environment exists and an operator runs the exact deployment/runbook checks, records sanitized measured results, validates the manifest, and attaches that manifest to an immutable repository qualification head.

No AWS secrets should ever be committed as evidence. Only resource identifiers/ARNs that are already appropriate for sanitized operational evidence and non-secret measured statuses may be recorded.

## Qualification path

1. qualify the new validator/tests in normal Backend A/B/C and Frontend CI;
2. preserve existing static environment and migration guardrails;
3. deploy an immutable backend image and exact frontend version to the authorized synthetic-data pilot runtime;
4. run static environment checks and live AWS identity/KMS/S3 reachability from the task-role context;
5. verify database migration head before traffic;
6. execute readiness, audit-outbox, host/CORS/proxy, rollback/invalidation, and dependency-outage scenarios;
7. create a sanitized evidence manifest;
8. run `python scripts/validate_pilot_runtime_evidence.py <manifest>`;
9. record measured evidence and exact deployment identities in this governance artifact;
10. requalify the final documentation/evidence head before any live-runtime PASS claim.

## Merge boundary for the harness

The validator, tests, and this governance artifact may merge after the exact final documentation head requalifies in Backend A/B/C and Frontend CI and review/base-state checks are clean. Such a merge means only that the **7B evidence harness is internally qualified and ready for live execution**.

It does not mark the live runtime as PASS. A future measured pilot manifest must be committed and separately exact-head qualified before that claim can change.

## Explicit nonclaims

This slice currently does not claim:

- a live pilot deployment PASS;
- production certification;
- real-patient-data authorization;
- external penetration testing;
- Slice 6I physical handset qualification;
- official ABDM/NHA HPR/HFR machine-contract qualification;
- external FHIR certification;
- Textract extraction-accuracy PASS.
