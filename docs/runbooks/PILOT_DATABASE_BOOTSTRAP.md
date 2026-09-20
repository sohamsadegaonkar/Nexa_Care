# Pilot database bootstrap repository contract

Status: repository preparation only. No AWS IAM, Secrets Manager, ECS, RDS, or PostgreSQL mutation is authorized by this document.

## Principals

| Principal | Purpose | Authority |
| --- | --- | --- |
| `nexacare_admin` | RDS-managed master/bootstrap identity | create/alter the two fixed login roles and establish database/schema grants |
| `nexa_migrator` | schema migration identity | CONNECT + CREATE on `nexacare_pilot`, USAGE + CREATE on `public`; owns default runtime DML privileges |
| `nexa_api_runtime` | API runtime identity | CONNECT + USAGE + application DML only; no schema CREATE |

The bootstrap script never creates the database and never grants `rds_superuser`.

## Idempotency state machine

The existing Secrets Manager containers are fixed:

- `nexa-care/pilot/db/runtime`
- `nexa-care/pilot/db/migrator`

The script treats only an entirely version-empty container as EMPTY. Historical/ambiguous version state without exactly one `AWSCURRENT` is rejected.

1. EMPTY / EMPTY -> generate two cryptographically random passwords in task memory, persist both secret values, re-read AWSCURRENT, and use those persisted values for PostgreSQL role mutation.
2. AWSCURRENT / AWSCURRENT -> reuse the persisted values exactly; retry does not rotate credentials.
3. Exactly one AWSCURRENT -> fail closed with `BOOTSTRAP_PARTIAL_SECRET_STATE`.

If PostgreSQL mutation fails after both values are persisted, a retry enters state 2 and `ALTER ROLE` reconciles the DB roles to the already-persisted values.

## TLS and logging

The one-off task uses the same committed CA bundle and shared database TLS function as API/Alembic:

`DATABASE_SSL_CA_PATH=/app/deploy/ssl/aws-rds-ca-bundle.pem`

The shared context must have `ssl.CERT_REQUIRED` and `check_hostname=True`. Connections are opened with individual asyncpg parameters, not a logged DSN. SQLAlchemy/SQL echo is disabled. Password-bearing generated DDL is never logged.

## Bootstrap transaction sequence

1. Read the RDS-managed `nexacare_admin` secret through the task role.
2. Resolve the runtime/migrator Secret Manager state.
3. Persist/reuse the two role credentials.
4. Connect as `nexacare_admin` using verify-full TLS.
5. Create or ALTER exactly `nexa_migrator` and `nexa_api_runtime`.
6. Revoke PUBLIC database/schema rights and establish the explicit grant matrix.
7. Connect a second time as `nexa_migrator` using the persisted migrator credential.
8. As `nexa_migrator`, set default table DML and sequence USAGE for `nexa_api_runtime`.

Role creation uses fixed identifiers plus parameterized `SELECT format(... %L ..., $1)` to quote passwords safely. Returned DDL is executed but never emitted to logs.

## Future release sequence — not executed by this slice

1. Run the bootstrap task.
2. Run `scripts/run_pilot_migrations.py` as `nexa_migrator` and require exact head `20260919_medication_catalog`.
3. Run `python scripts/bootstrap_pilot_database.py post-migration` using the same immutable image/task role.
4. Post-migration reconciliation grants DML on existing public tables, USAGE on existing sequences, explicitly removes sequence UPDATE, and hardens `public.alembic_version` to runtime SELECT-only.
5. Later live verification must prove runtime SELECT on `alembic_version` succeeds; INSERT/UPDATE/DELETE/TRUNCATE and runtime DDL fail.

## IAM templates

The trust template is restricted to `ecs-tasks.amazonaws.com`, account `654654144224`, region `ap-south-1`, and tasks in cluster `nexa-care-pilot`.

The execution policy permits only ECR authorization, pull from the exact API repository placeholder to be resolved by the account owner, and writes to the dedicated bootstrap log group. It has no Secrets Manager permissions.

The task policy can read the exact RDS-managed master secret placeholder and can Describe/Get/Put only the two fixed pilot DB role secrets. It contains no KMS, S3, Textract, medication-signing, IAM-management, or broad Secrets Manager permissions. The selected secrets use AWS-managed `aws/secretsmanager`, so KMS permissions are deliberately absent unless a future measured denial proves they are required.

## One-off Fargate task

`deploy/ecs/nexa-care-pilot-database-bootstrap-task-definition.template.json` is `awsvpc` + Fargate in `ap-south-1`, has no port mappings, is not a service, and uses the same digest-pinned API image that contains this script and the committed RDS CA bundle.

Future network execution uses the existing ECS task security group `sg-0920ce63d7ff6f3fe`, which is already approved for PostgreSQL SG egress/connectivity on 5432. This repository slice does not register or run the task.
