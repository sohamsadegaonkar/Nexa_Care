# Pilot database bootstrap repository contract

Status: repository preparation only. No AWS IAM, Secrets Manager, ECS, RDS, or PostgreSQL mutation is authorized by this document.

## Principals

| Principal | Purpose | Authority |
| --- | --- | --- |
| RDS-managed master/bootstrap identity | Privileged bootstrap connection identity (`NEXA_DB_MASTER_USERNAME`, e.g. `postgres`) | create/alter the two fixed login roles and establish database/schema grants |
| `nexa_migrator` | schema migration identity | CONNECT + CREATE on `nexacare_pilot`, USAGE + CREATE on `public`; owns default runtime DML privileges |
| `nexa_api_runtime` | API runtime identity | CONNECT + USAGE + application DML only; no schema CREATE |

The bootstrap script never creates the database and never grants `rds_superuser`.
Both fixed application roles are always created or normalized with `LOGIN`,
`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, `NOREPLICATION`, and
`NOBYPASSRLS`. Existing role state is inspected before alteration. If either
fixed role is a member of any other PostgreSQL role, bootstrap fails closed with
`BOOTSTRAP_ROLE_MEMBERSHIP_INVALID`; arbitrary memberships are never silently
preserved or automatically revoked.

## Idempotency state machine

The existing Secrets Manager containers are fixed:

- `nexa-care/pilot/db/runtime`
- `nexa-care/pilot/db/migrator`

The script treats only an entirely version-empty container as EMPTY. Historical/ambiguous version state without exactly one `AWSCURRENT` is rejected.

1. EMPTY / EMPTY -> generate two cryptographically random passwords in task memory, persist both secret values, re-read AWSCURRENT, and use those persisted values for PostgreSQL role mutation.
2. AWSCURRENT / AWSCURRENT -> reuse the persisted values exactly; retry does not rotate credentials.
3. Exactly one AWSCURRENT -> fail closed with `BOOTSTRAP_PARTIAL_SECRET_STATE`.

If PostgreSQL mutation fails after both values are persisted, a retry enters state 2 and `ALTER ROLE` reconciles the DB roles to the already-persisted values.

### Partial-secret recovery is a separate approval gate

A partial state is intentionally not repaired by this program. If one DB role
secret has `AWSCURRENT` and the other does not, operators must stop and obtain a
separately authorized recovery procedure. This repository slice does not delete,
overwrite, rotate, or otherwise destructively repair partial Secrets Manager
state.

## TLS and logging

The one-off task uses the same committed CA bundle and shared database TLS function as API/Alembic:

`DATABASE_SSL_CA_PATH=/app/deploy/ssl/aws-rds-ca-bundle.pem`

The shared context must have `ssl.CERT_REQUIRED` and `check_hostname=True`. Connections are opened with individual asyncpg parameters, not a logged DSN. SQLAlchemy/SQL echo is disabled. Password-bearing generated DDL is never logged.

## Bootstrap transaction sequence

1. Read the RDS-managed master secret through the task role and validate that its username matches the deployment expectation in `NEXA_DB_MASTER_USERNAME`.
2. Resolve the runtime/migrator Secret Manager state.
3. Persist/reuse the two role credentials.
4. Connect as the validated RDS-managed master/bootstrap identity using verify-full TLS.
5. In one explicit asyncpg transaction, inspect membership/state, create or normalize exactly `nexa_migrator` and `nexa_api_runtime`, revoke PUBLIC database/schema rights, and establish the exact master grant matrix. Any failure rolls back that whole master phase.
6. Connect a second time as `nexa_migrator` using the persisted migrator credential.
7. In one explicit asyncpg transaction, reset the migrator-owned defaults for the runtime role and grant only table `SELECT, INSERT, UPDATE, DELETE` plus sequence `USAGE`. Any failure rolls back that whole default-privilege phase.

Role creation/alteration uses fixed identifiers plus parameterized `SELECT format(... %L ..., $1)` to quote passwords safely. Returned DDL is executed but never emitted to logs.

## Future release sequence — not executed by this slice

1. Run the bootstrap task.
2. Run `scripts/run_pilot_migrations.py` as `nexa_migrator` and require exact head `20260919_medication_catalog`.
3. Run `python scripts/bootstrap_pilot_database.py post-migration` using the same immutable image/task role.
4. `post-migration` connects as `nexa_migrator` over verify-full TLS and begins one explicit transaction. Before any privilege mutation it requires exactly one row from `public.alembic_version`, equal to `20260919_medication_catalog`. Missing table/query failure, zero rows, multiple rows, or a different revision fails closed as `BOOTSTRAP_MIGRATION_HEAD_MISMATCH`.
5. Only after the exact-head gate passes, the same transaction resets existing runtime table/sequence privileges to table `SELECT, INSERT, UPDATE, DELETE` and sequence `USAGE`, then hardens `public.alembic_version` to runtime SELECT-only and preserves `REVOKE CREATE ON SCHEMA public`. Any failure rolls back the entire post-migration privilege phase.
6. Later live verification must prove runtime SELECT on `alembic_version` succeeds; INSERT/UPDATE/DELETE/TRUNCATE and runtime DDL fail.

The API runtime role is never granted `TRUNCATE`, `REFERENCES`, `TRIGGER`,
`CREATE`, `ALTER`, or `DROP`; sequence `UPDATE` is never granted.

## IAM templates

### ECS task-role trust boundary

The task-role trust principal remains `ecs-tasks.amazonaws.com` and retains
`aws:SourceAccount=654654144224`. The supported ECS confused-deputy SourceArn
form is deliberately:

`arn:aws:ecs:ap-south-1:654654144224:*`

AWS ECS task-role trust does not currently support using `aws:SourceArn` to
constrain assumption to one ECS cluster, so the previous cluster-shaped task
ARN is not used. The required cluster boundary is instead applied to the
identity that is authorized to call `ecs:RunTask`.

`deploy/iam/pilot-database-bootstrap-run-task-policy.template.json` permits
`ecs:RunTask` only for the exact cluster ARN
`arn:aws:ecs:ap-south-1:654654144224:cluster/nexa-care-pilot` and an exact rendered revision of task-definition family `nexa-care-pilot-database-bootstrap`.
The revision placeholder must be replaced with one approved numeric revision;
it must not be widened to `*`. Any separately required `iam:PassRole` authority
must remain restricted to the exact bootstrap execution/task roles.

### Execution and task roles

The execution policy permits only ECR authorization, pull from the exact API repository placeholder to be resolved by the account owner, and writes to the dedicated bootstrap log group. It has no Secrets Manager permissions.

The task policy can read the exact RDS-managed master secret placeholder (`<RDS_MANAGED_MASTER_SECRET_ARN>`) and can Describe/Get/Put only the two fixed pilot DB role secrets using exact rendered ARN placeholders (`<RUNTIME_DATABASE_SECRET_ARN>`, `<MIGRATOR_DATABASE_SECRET_ARN>`) without wildcard resource patterns. It contains no KMS, S3, Textract, medication-signing, IAM-management, or broad Secrets Manager permissions. The selected secrets use AWS-managed `aws/secretsmanager`, so KMS permissions are deliberately absent unless a future measured denial proves they are required.

## One-off Fargate task

`deploy/ecs/nexa-care-pilot-database-bootstrap-task-definition.template.json` is `awsvpc` + Fargate in `ap-south-1`, has no port mappings, is not a service, and uses the same digest-pinned API image that contains this script and the committed RDS CA bundle. It defines required environment variables including `NEXA_DB_MASTER_USERNAME` set to `<RDS_MASTER_USERNAME>`, resolving at deployment time to the verified master username (e.g. `postgres`).

Future network execution uses the existing ECS task security group `sg-0920ce63d7ff6f3fe`, which is already approved for PostgreSQL SG egress/connectivity on 5432. This repository slice does not register or run the task.
