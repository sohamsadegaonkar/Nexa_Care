# Slice 8B — Live Cloud Qualification

**Status:** LIVE QUALIFICATION ATTEMPTED — BLOCKED BY AWS ACCOUNT WIRING / NOT DEPLOYED

**Authoritative base `main`:** `c1cf58c3fb7d0caefb2a275c6cca0b0e1f5fab5e`

**Qualification branch:** `slice-8b-live-cloud-qualification`

## Purpose

Slice 8B moves Nexa Care from the internally-qualified Slice 8A runtime contract toward a real AWS pilot qualification without fabricating external evidence.

The governing distinction remains:

`IMPLEMENTED != INTERNALLY QUALIFIED != DEPLOYED != EXTERNALLY QUALIFIED != PHYSICALLY QUALIFIED`.

This slice is permitted to claim live qualification only after a connected AWS identity reaches the approved pilot account and the live ECS/API gates pass.

## Live attempt performed

A temporary same-repository probe branch, `slice-8b-live-cloud-probe`, executed two sanitized GitHub Actions runs:

- Live Cloud Probe run `34395256596` on probe head `701b453c848f65cdcea3b0db96bf20fc2c11cce1`
- Live Cloud Probe run `34395397862` on probe head `466e4263c9ee6cea2d6c07545e85f78e0aba7f07`

Both runs stopped before AWS authentication. The broadened second probe tested Nexa-specific and common GitHub Actions variable/secret naming conventions and observed all required deployment inputs absent:

- AWS OIDC deployment/qualification role
- ECS cluster
- ECS service
- ECR repository
- runtime Secrets Manager identifier
- document-storage Secrets Manager identifier

The second probe emitted `LIVE_CLOUD_PROBE=BLOCKED_MISSING_OIDC_ROLE` and exited before `configure-aws-credentials`, STS, ECS, ECR or Secrets Manager calls. Therefore no AWS resource was read or mutated through an authenticated AWS session, and no deployment occurred.

## Permanent live qualification workflow

`.github/workflows/live-cloud-qualification.yml` is the permanent fail-closed operator gate. It is manual-only (`workflow_dispatch`) and uses GitHub OIDC; static AWS credentials are not part of the workflow contract.

When account wiring exists, the workflow requires and verifies:

1. approved AWS OIDC role assumption in `ap-south-1`;
2. one configured ECS service at its desired stable count;
3. a Fargate-backed service contract;
4. an immutable ECR image pinned by digest;
5. an application task role and absence of static AWS credential variables in the task definition;
6. Secrets Manager metadata reachability without reading secret values;
7. public `/healthz` and `/health` success;
8. protected `/ops/health` success;
9. protected `/metrics` success.

The workflow masks the AWS account ID and avoids printing role ARNs, service identifiers, repository names, secret identifiers, operations tokens or secret values.

## Required account-side wiring before the next live run

The approved pilot AWS account or GitHub `pilot` environment must provide the following values without committing them to the repository:

- `NEXA_PILOT_AWS_ROLE_ARN` (or common `AWS_ROLE_ARN`) — an OIDC-assumable least-privilege qualification/deployment role;
- `NEXA_PILOT_ECS_CLUSTER`;
- `NEXA_PILOT_ECS_SERVICE`;
- `NEXA_PILOT_ECR_REPOSITORY`;
- `NEXA_PILOT_RUNTIME_SECRET_ID`;
- `NEXA_PILOT_DOCUMENT_STORAGE_SECRET_ID`;
- `NEXA_PILOT_API_BASE_URL`;
- `NEXA_PILOT_OPERATIONS_AUTH_TOKEN` as a secret.

Account-side OIDC trust must restrict the subject/audience to this repository and the intended GitHub environment/branch policy. The AWS role must be least privilege and must not grant broad administrative access merely to make qualification pass.

## Deployment boundary

This repository currently contains the Slice 8A ECS task-definition/runtime templates and deployment runbook, but it does not contain a complete VPC/RDS/Redis/ALB/ECR/ECS/KMS/S3 infrastructure-as-code stack. Creating those resources blindly would require inventing account-specific networking, domains, certificates, IAM boundaries, database sizing, Redis topology and cost decisions.

Therefore this slice does not create infrastructure speculatively. The live deployment gate remains blocked until approved AWS account wiring and target metadata are supplied through the GitHub environment or equivalent authorized execution path.

## What is not yet qualified

The following remain `NOT_RUN` / `NOT_DEPLOYED` because AWS authentication never became available:

- ECS/Fargate deployment or service update;
- live application task-role identity;
- live KMS/S3 startup preflight;
- live PostgreSQL exact-head/reachability startup preflight;
- live TLS Redis startup/outage behavior;
- live CloudWatch logs inspection;
- protected Prometheus scrape against the deployed service;
- live rollback to a previously qualified immutable image;
- production-patient workload (which is not authorized by the pilot contract in any case).

Existing Slice 7 external, retention-approval and physical-device boundaries are unchanged.

## Qualification statement

Slice 8B has established a repository-side, fail-closed live-cloud qualification entry point and has performed a real connection attempt. That attempt proves the current blocker is missing AWS account/GitHub environment wiring, not a passed deployment.

Current truthful status:

**LIVE CLOUD QUALIFICATION: BLOCKED BY AWS ACCOUNT WIRING / NOT DEPLOYED.**
