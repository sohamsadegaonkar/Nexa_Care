# Milestone 6 / Slice 8A Fargate deployment and qualification runbook

This operator plan creates no infrastructure by itself. Use only approved
synthetic environments and never commit domains, account identifiers, role
ARNs, database/Redis URLs, credentials, secret ARNs, or secret values.

## Immutable release inputs

1. Start from a reviewed clean Git commit and record the full SHA.
2. Build the repository Dockerfile from that SHA.
3. Push the image to ECR and record its immutable digest. Never deploy `latest`.
4. Record matching frontend/mobile build identities when they are in scope.

## Infrastructure preparation

- Use an isolated ECS/Fargate service in `ap-south-1`.
- Use dedicated synthetic PostgreSQL and TLS Redis/Upstash instances.
- Use an S3 bucket in `ap-south-1` with default SSE-KMS, all four public-access
  block settings enabled, and versioning enabled.
- **Do not add/change lifecycle retention while the repository retention
  approval is pending.**
- Store runtime secrets in Secrets Manager/SSM and reference them from the task
  definition. Do not put plaintext secrets in image/environment/logs.
- Terminate TLS only at the approved HTTPS ingress and restrict the task security
  group to that ingress.

## ECS roles and minimum runtime permissions

Use separate execution and application task roles. The application task role is
restricted to the exact configured bucket and KMS resources. Current runtime
and startup-preflight code paths require only the relevant subset of:

```text
textract:AnalyzeDocument
s3:ListBucket
s3:GetObject
s3:PutObject
s3:DeleteObject
s3:GetEncryptionConfiguration
s3:GetBucketPublicAccessBlock
s3:GetBucketVersioning
kms:DescribeKey
kms:GenerateDataKey
kms:Decrypt
```

Do not grant `s3:ListAllMyBuckets`, broad KMS administration, KMS key creation or
deletion, secret-value reads to the application role beyond the task-definition
mechanism, or wildcard resource access when resource scoping is possible.

Static `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`
environment variables are forbidden. Runtime AWS identity comes from the task
role.

## Configuration materialization

1. Resolve the immutable image, role, log-group, secret metadata, KMS metadata,
   and S3 security metadata with `scripts/generate_pilot_deployment_values.py`.
   The generator is read-only and writes its rendered output outside the repo.
2. Populate `deploy/ecs/nexa-care-pilot-task-definition.template.json` using the
   approved values.
3. Supply all runtime secrets, including provider-registration HMAC,
   provider-contact HMAC and `OPERATIONS_AUTH_TOKEN`.
4. Run `python scripts/check_pilot_environment.py` against the final task
   environment. From the intended task identity, additionally run
   `python scripts/check_pilot_environment.py --live-aws`.
5. Stop on any mismatch; do not weaken the checker to make a deployment pass.

## One-time migration task

Database migration remains separate from API startup:

1. Back up the dedicated database and verify the approved restore procedure.
2. Run the same immutable image as a one-off task with command
   `python scripts/run_pilot_migrations.py`.
3. Provide only `MIGRATION_DATABASE_URL` for that release task.
4. Require exit zero and exact repository/database head
   `20260909_device_trust_lifecycle`.
5. The API container must never run `alembic upgrade`, stamp, or downgrade on
   startup.

## API startup contract

After the migration task succeeds, start/update the API service. Production-like
startup must finish these read-only checks **before any background worker starts**:

1. static production configuration and secret shape;
2. PostgreSQL reachability;
3. exact `alembic_version` = repository migration head;
4. TLS Redis reachability;
5. application and storage KMS keys enabled for `ENCRYPT_DECRYPT`;
6. S3 bucket reachable with default SSE-KMS, complete public-access block and
   versioning enabled.

A failure is a failed deployment, not a degraded-but-acceptable startup.

## Service, health and observability

- Keep focused qualification at `desiredCount=1`; do not scale/restart while an
  extraction is active.
- Container/load-balancer liveness uses `GET /healthz` only.
- Traffic enablement requires public `GET /health` to be healthy.
- Operator diagnostics require `GET /ops/health` with
  `X-Nexa-Operations-Token`.
- Prometheus scraping uses protected `GET /metrics` with the same token.
- Public readiness exposes only coarse dependency classes. Exception class
  names, outbox counts, AWS identifiers and configuration values are not public.
- Request logs use structured JSON and server-owned route templates, never raw
  URL path parameter values.

The audit-outbox, failure-quarantine and optional provider-reconciliation loops
are supervised and restarted with bounded exponential backoff after unexpected
top-level exits.

## Frontend/mobile qualification boundary

The backend `CORS_ALLOWED_ORIGINS`, `TRUSTED_HOSTS`, `TRUSTED_PROXY_NETWORKS`
and `FORWARDED_ALLOW_IPS` must match the actual HTTPS ingress topology. Mobile
and web builds must use the exact deployed API identity. Only approved synthetic
identities/documents may be used for pilot/physical qualification.

## Focused qualification

1. Confirm exactly the intended healthy task set and no deployment/scaling event.
2. Confirm `/healthz`, `/health`, protected `/ops/health`, safe CloudWatch logs,
   exact migration head, TLS Redis and KMS/S3 readiness.
3. Exercise the approved synthetic extraction/consent workflow.
4. Preserve sanitized evidence tied to exact Git/image/frontend/mobile identities.
5. Do not claim live production qualification from repository/CI evidence alone.

## Exact rollback contract

1. Stop new traffic and uploads.
2. Preserve PostgreSQL, S3 and audit evidence.
3. Select the last **qualified immutable** frontend version and backend image
   digest; never roll back to an unpinned tag.
4. Review database compatibility before changing application version.
5. **Do not automatically downgrade PostgreSQL.** Existing migrations include
   forward-only safety changes; use an approved corrective forward migration
   after impact review.
6. Start the prior image only if its startup schema gate accepts the current
   database head.
7. Verify `/healthz`, `/health`, protected `/ops/health`, audit/Redis/KMS/S3 and
   synthetic smoke tests.
8. Invalidate provider/patient sessions and require fresh consent before
   protected workflows resume.
9. Revoke the task role only when containment requires it.

A rollback is not complete merely because a container is running. The previous
qualified image must be compatible with the current schema and all security
readiness gates must pass before traffic resumes.
