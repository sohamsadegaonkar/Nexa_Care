# Pilot security operations

## Approved Milestone 6 qualification shape

The doctor frontend is deployed on Vercel and reaches the API through its
same-origin `/api` rewrite. The backend is a continuously running Amazon ECS
Fargate service in `ap-south-1` with `desiredCount=1`. Autoscaling,
serverless/scale-to-zero hosting, and multiple API workers are disabled during
focused qualification. Use a dedicated Supabase PostgreSQL database and a
dedicated TLS Redis/Upstash instance.

Do not deploy, restart, replace, or scale the ECS service while a focused
document extraction is active. Only synthetic identities and documents are
allowed; real patient PHI is prohibited.

## Redis reliability policy

| Control | Policy | Unavailable behavior |
| --- | --- | --- |
| Consent challenge, signed approval, replay nonce, capability claim | Security-critical | Fail closed with `503`; never issue or accept access |
| Provider login target/IP throttles, MFA, patient OTP, NFC, policy mutation | Credential/security-critical | Fail closed with `503`; operator alert required |
| General read-route abuse limiting | Abuse reduction | Fail open only with structured degraded telemetry; authorization and consent still apply |
| Push delivery notification | Availability-sensitive | Record delivery failure; approval remains pending and can be polled |
| Audit ledger writes for grants, reads, review, and commit | Security-critical | Fail closed; do not complete the protected operation |

All asynchronous request paths use `redis.asyncio`. Atomic counters and consent
resolution use Lua so increment/TTL and status/nonce transitions cannot split.
`PUSH_STATUS_TRANSPORT=poll` is mandatory for this qualification.

## Reverse proxy and browser security

Set `TRUSTED_PROXY_NETWORKS` to only the private CIDRs of the final load
balancer/proxies that directly connect to the task. Set `FORWARDED_ALLOW_IPS`
to only those direct proxy addresses or CIDRs. Wildcards, `0.0.0.0/0`, and
`::/0` are prohibited. Uvicorn proxy-header processing is enabled only when
`FORWARDED_ALLOW_IPS` is explicit.

Set `TRUSTED_HOSTS` to the deployed API host and
`CORS_ALLOWED_ORIGINS` to explicit HTTPS doctor-frontend origins. The Vercel
same-origin API rewrite is preferred so secure provider cookies and the CSRF
double-submit flow remain first-party in the browser.

## Required qualification configuration

- `ENVIRONMENT=pilot` (or the separately controlled `staging`/`production` mode)
- `DOCUMENT_EXTRACTION_PROVIDER=aws_textract`
- `DOCUMENT_AI_AWS_REGION=ap-south-1`
- no `DOCUMENT_AI_API_URL` or `DOCUMENT_AI_API_KEY`
- `DOCUMENT_STORAGE_PROVIDER=s3`, with bucket, `ap-south-1` region, storage KMS
  key, and client-side storage encryption secret
- `ENCRYPTION_BACKEND=kms`, `AWS_REGION=ap-south-1`, shared `KMS_KEY_ID`, and
  `AWS_PATIENT_SPECIFIC_KMS_KEYS=false`
- dedicated `DATABASE_URL` and TLS `UPSTASH_REDIS_URL`
- `TRUSTED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `TRUSTED_PROXY_NETWORKS`, and
  `FORWARDED_ALLOW_IPS` restricted as described above
- Supabase, OTP/login HMAC, handshake, MFA, PII, patient-JWT, and storage
  encryption secrets supplied through managed secret references

Static `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`
configuration is prohibited. Application AWS access must come from the ECS task
IAM role through the normal SDK credential chain.

Before deployment, run the static check in the same environment contract:

```text
python scripts/check_pilot_environment.py
```

Use `--live-aws` only from the intended task identity to check the configured
KMS keys and S3 bucket metadata. It does not submit content to Textract.

## Migration, health, and routing gates

Database migration is a separate one-time ECS release task or controlled
operator command. It must set `MIGRATION_DATABASE_URL` and run:

```text
python scripts/run_pilot_migrations.py
```

The repository and database must both resolve to the single Alembic head
`20260905_verification_application`. API container startup never runs migrations and
must not be used to stamp or downgrade a database.

`GET /healthz` is the dependency-free liveness probe for the container and load
balancer. `GET /health` is the deployment readiness gate and must report healthy
PostgreSQL, Redis, and audit-outbox worker/backlog state before traffic is
enabled.

Runtime `AUTO_COMMIT` remains disabled. `SOURCE_ONLY` and `QUARANTINE` are the
only accepted automated routing lanes. Clinical commitment requires the
existing explicit clinician adjudication boundary.

## Exact rollback sequence

1. Stop new qualification traffic.
2. Stop uploads.
3. Preserve database, S3, and audit evidence.
4. Roll back the frontend deployment and backend image to the last qualified immutable versions.
5. Do not automatically downgrade PostgreSQL; assess and apply only an approved forward fix.
6. Invalidate provider/patient sessions and require fresh consent before resuming.
7. Revoke task-role access only when containment is required.

Never delete suspected clinical rows or source objects during rollback. Isolate
and investigate them under the approved retention and audit rules.

## Document retention

Documents are client-side AES-GCM encrypted and S3 server-side KMS encrypted.
Tenant/patient ownership is enforced on every adapter read/delete. Configure
approved retention and legal-hold lifecycle rules on the bucket; application
deletion removes only the authorized object and is idempotent. Database failures
after upload trigger object cleanup.
