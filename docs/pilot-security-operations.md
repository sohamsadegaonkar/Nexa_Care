# Pilot security operations

## Approved qualification shape

The doctor frontend reaches a continuously running Amazon ECS Fargate backend in
`ap-south-1`; focused qualification keeps `desiredCount=1`. Use only dedicated
synthetic PostgreSQL and TLS Redis/Upstash instances. Real patient PHI is
prohibited.

Do not deploy, restart, replace, or scale the service while a focused document
extraction is active.

## Redis reliability policy

| Control | Policy | Unavailable behavior |
| --- | --- | --- |
| Consent, replay nonce, capability claim | Security-critical | Fail closed with `503` |
| Provider login/MFA, patient OTP, break-glass and generic route throttles | Security-critical | Fail closed with `503` |
| Push concurrency/rate limiting | Security-critical | Fail closed with `503` |
| Push notification delivery | Availability-sensitive | Record delivery failure; approval remains pending |
| Audit ledger/outbox | Security-critical | Protected operation/qualification fails closed |

All security throttles are Redis-backed and shared across workers. A Redis
outage must never silently disable a rate limit. `PUSH_STATUS_TRANSPORT=poll`
is mandatory for the pilot contract.

## Reverse proxy and browser security

Set `TRUSTED_PROXY_NETWORKS` and `FORWARDED_ALLOW_IPS` only to the final direct
proxy/load-balancer addresses or CIDRs. Wildcards, `0.0.0.0/0`, and `::/0` are
prohibited. Set `TRUSTED_HOSTS` to explicit deployed API hosts and
`CORS_ALLOWED_ORIGINS` to explicit HTTPS doctor origins.

## Required qualification configuration

- `ENVIRONMENT=pilot` (or separately controlled staging/production)
- `DOCUMENT_EXTRACTION_PROVIDER=aws_textract`
- `DOCUMENT_AI_AWS_REGION=ap-south-1`
- no `DOCUMENT_AI_API_URL` or `DOCUMENT_AI_API_KEY`
- bounded provider/job/reconciliation retry settings
- `MAX_UPLOAD_BYTES=20971520` or lower
- `DOCUMENT_STORAGE_PROVIDER=s3`, bucket in `ap-south-1`, storage KMS key, and
  independent client-side storage encryption key
- `ENCRYPTION_BACKEND=kms`, `AWS_REGION=ap-south-1`, `KMS_KEY_ID`, and
  `AWS_PATIENT_SPECIFIC_KMS_KEYS=false`
- dedicated `DATABASE_URL` and TLS `UPSTASH_REDIS_URL`
- explicit trusted hosts, HTTPS CORS origins, trusted proxies and forwarded IPs
- independent Supabase, handshake, MFA, PII, patient JWT, OTP HMAC, provider
  registration HMAC, provider contact-assurance HMAC, document-storage, and
  `OPERATIONS_AUTH_TOKEN` secrets supplied through managed secret references
- `DATABASE_ECHO_SQL=false` and `AUTO_COMMIT=false`

Static `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`
environment variables are prohibited. Runtime AWS access comes only from the ECS
task role.

Before deployment run:

```text
python scripts/check_pilot_environment.py
```

From the intended task identity, `--live-aws` additionally checks configured KMS
keys and the S3 encryption/public-block/versioning posture. These checks are
read-only.

## Startup, migration and dependency gates

Database migration remains a separate one-time release task with
`MIGRATION_DATABASE_URL`:

```text
python scripts/run_pilot_migrations.py
```

The current exact repository migration head is
`20260910_registration_recovery_review`. The migration task upgrades and verifies that
exact single repository head. API containers never run migrations. In a
production-like runtime, API startup then independently refuses to start unless:

1. static production configuration is valid;
2. PostgreSQL is reachable;
3. `alembic_version` exactly equals the repository head;
4. Redis is reachable;
5. both configured KMS keys are enabled for encrypt/decrypt;
6. the S3 bucket is reachable, uses default SSE-KMS, has all four public-access
   block controls enabled, and has versioning enabled.

The checks occur before background workers start, so a stale schema or unusable
security dependency cannot produce a partially alive task.

## Health, metrics and background workers

- `GET /healthz` — dependency-free public liveness only.
- `GET /health` — public coarse readiness. It exposes status classes, not
  exception names, URLs, credentials, bucket/key IDs, or outbox counts.
- `GET /ops/health` — detailed aggregate operational readiness. Requires
  `X-Nexa-Operations-Token` in production-like environments and includes the
  live AWS metadata recheck.
- `GET /metrics` — Prometheus exposition. Protected by the same operations
  token in production-like environments.

The audit-outbox, failure-quarantine and optional provider-reconciliation loops
run under supervisors. An unexpected top-level worker exit is logged with
sanitized metadata and restarted with bounded exponential backoff. Shutdown
signals and joins each worker independently; one failed worker cleanup cannot
skip cleanup of the others.

## Logging and error response policy

Application request logs are structured JSON. Client-supplied trace IDs are
accepted only in a bounded hex format; otherwise the server generates one.
Request logs use server-owned route templates rather than raw URL paths so path
parameters are not emitted. General exception logging uses the safe-exception
allow-list and does not serialize exception strings, traceback locals, database
URLs, Redis URLs, AWS identifiers, tokens, or secrets.

## Exact rollback sequence

1. Stop new qualification traffic and uploads.
2. Preserve PostgreSQL, S3 and audit evidence.
3. Roll the frontend and ECS service back to the last qualified immutable
   frontend version and backend image digest.
4. Do **not** automatically downgrade PostgreSQL. Migrations may contain
   forward-only safety changes; use an approved corrective forward revision
   after impact review.
5. Start the previous image only against a schema it is explicitly compatible
   with; its startup revision gate must pass.
6. Re-run `/healthz`, public `/health`, protected `/ops/health`, and relevant
   synthetic smoke tests.
7. Invalidate provider/patient sessions and require fresh consent before
   resuming protected workflows.
8. Revoke task-role access only when containment requires it.

Never delete suspected clinical rows or source objects during rollback.

## Retention boundary

Documents remain client-side AES-GCM encrypted and S3 server-side KMS encrypted;
tenant/patient ownership is enforced on every adapter read/delete. **Do not
configure or change S3 lifecycle/retention rules until the repository's pending
security/privacy/legal retention decision is approved.** Evidence preservation
and legal-hold requirements take precedence over cleanup convenience.
