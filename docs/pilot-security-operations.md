# Pilot security operations

## Redis reliability policy

| Control | Policy | Unavailable behavior |
| --- | --- | --- |
| Consent challenge, signed approval, replay nonce, capability claim | Security-critical | Fail closed with `503`; never issue or accept access |
| Provider login target/IP throttles, MFA, patient OTP, NFC, policy mutation | Credential/security-critical | Fail closed with `503`; operator alert required |
| General read-route abuse limiting | Abuse reduction | Fail open only with structured degraded telemetry; authorization and consent still apply |
| Push delivery notification | Availability-sensitive | Record delivery failure; approval remains pending and can be polled |
| Audit ledger writes for grants, reads, review, and commit | Security-critical | Fail closed; do not complete the protected operation |

All async request paths use `redis.asyncio`. Atomic counters and consent resolution use Lua so increment/TTL and status/nonce transitions cannot split.

## Reverse proxy deployment

Set `TRUSTED_PROXY_NETWORKS` to the exact CIDRs of the final reverse proxies that directly connect to the application. Do not enter public client ranges. Forwarded headers are ignored unless the direct peer is in this list. Render or another ingress must publish its current egress/proxy CIDRs; deployment is blocked until those CIDRs are confirmed. IPv4 and IPv6 CIDRs are supported.

## Required production configuration

- `ENVIRONMENT=pilot` (or `production`)
- `TRUSTED_HOSTS` and `CORS_ORIGINS` containing only deployed origins
- `TRUSTED_PROXY_NETWORKS` containing only direct ingress proxy CIDRs
- `DOCUMENT_EXTRACTION_PROVIDER=remote`, HTTPS `DOCUMENT_AI_API_URL`, secret `DOCUMENT_AI_API_KEY`
- `DOCUMENT_STORAGE_PROVIDER=s3`, bucket/region, storage encryption key
- `ENCRYPTION_BACKEND=kms`, `KMS_KEY_ID`, `AWS_REGION`
- Redis, database, OTP/login HMAC, MFA encryption, Supabase, and audit-ledger secrets from the deployment secret manager

Local extraction, storage, and envelope encryption are rejected in production-like environments. Startup validation prevents an unsafe mode from serving traffic.

## Migration and rollback

1. Back up Postgres and verify restore procedures before deployment.
2. Install dependencies, including `boto3`.
3. Run `alembic upgrade 20260717_secure_document_pipeline` during a write-maintenance window.
4. Run `scripts/quarantine_fabricated_pipeline_rows.py` without flags and review the dry-run report.
5. Configure remote extraction, S3, AWS KMS, Redis, proxy CIDRs, and browser origins.
6. Start one instance and verify `/health`, extraction failure behavior, cookie login, CSRF rejection, and KMS decrypt using synthetic records before scaling.

Rollback the application before downgrading the database. The migration downgrade removes newly added metadata and must only run after confirming no new-version jobs or KMS-wrapped DEKs need those columns. Object storage is not deleted by database rollback. Never delete suspected clinical rows during rollback; quarantine and investigate them.

## Document retention

Documents are client-side AES-GCM encrypted and S3 server-side KMS encrypted. Tenant/patient ownership is enforced on every adapter read/delete. Retention and legal-hold lifecycle rules must be configured on the bucket; application deletion removes only the authorized object and is idempotent. Database failures after upload trigger object cleanup.
