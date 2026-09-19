# AWS Pilot Activation Readiness

**Status:** OPERATOR-READY REPOSITORY PACKAGE / LIVE AWS PILOT NOT_RUN

**Starting main:** `05dde001fde464223480cc3da230e592117354a1`

**Readiness branch:** `task1/aws-pilot-activation-readiness`

This runbook turns the remaining Nexa Care AWS pilot blocker into an explicit
account-owner activation sequence. It does not create AWS resources, deploy an
ECS service, read secret values, or claim a live qualification.

The approved architecture remains:

```text
GitHub Actions -> AWS OIDC -> ECR
                         -> ECS/Fargate task
                              |- nexa-care-pilot-api
                              `- patient-source-clamd
API -> S3 + KMS + Textract + PostgreSQL + TLS Redis
Vercel -> approved HTTPS ingress -> API
```

Google Cloud is not part of this plan.

## 1. Asset audit

| Asset | Before this slice | Readiness disposition |
| --- | --- | --- |
| `deploy/ecs/nexa-care-pilot-task-definition.template.json` | READY | Reuse. Fargate/awsvpc, essential API + clamd, task-local scanner, no scanner port mapping. |
| `deploy/ecs/pilot-runtime-contract.template.json` | READY | Reuse. It fixes Textract/S3/KMS/clamd providers, startup preflight, protected health and no-static-key boundaries. |
| `deploy/ecs/pilot-deployment-values.schema.json` | PARTIAL | Strengthened to require the complete activation/render contract rather than only task size + scanner image. |
| `deploy/ecs/pilot-activation-inputs.template.json` | MISSING | Added as the sanitized account-owner input template. |
| `scripts/check_pilot_environment.py` | READY | Reuse for final runtime configuration and optional read-only in-task AWS checks. |
| `scripts/generate_pilot_deployment_values.py` | READY | Reuse once authorized AWS identity exists; it resolves metadata only and never reads secret values or mutates AWS. |
| `scripts/check_aws_pilot_activation.py` | MISSING | Added as an offline account-input/task renderer and security preflight; it makes no AWS calls. |
| `scripts/run_pilot_migrations.py` | STALE | Reconciled to current single head `20260919_medication_catalog`. No migration is introduced. |
| `.github/workflows/clamd-integration.yml` | READY | Reuse as controlled real-clamd repository qualification; it is not a deployed-pilot claim. |
| `.github/workflows/live-cloud-qualification.yml` | PARTIAL | Reuse for OIDC/ECS/ECR/health verification. It is verification-only and does not deploy or perform the clean/EICAR/outage functional sequence. |
| `.github/workflows/rollback-runtime-qualification.yml` | STALE | Reconciled to require the post-D6 essential API + clamd rollback pair, both digest-pinned and scanner task-local. |
| `scripts/validate_pilot_runtime_evidence.py` | PARTIAL | Historical v1 remains accepted; v2 adds current migration head and D6 live-scanner evidence gates. |
| `deploy/ecs/pilot-runtime-evidence-v2.template.json` | MISSING | Added as the current sanitized live-evidence starting point. |
| Complete VPC/ECS/RDS/Redis/ALB infrastructure-as-code stack | MISSING BY DESIGN | Do not invent account-specific networking, certificates, database topology, or cost decisions. Use account-owner approved resources. |
| Authorized AWS account wiring and live synthetic pilot | MISSING EXTERNAL | Required before any live deployment/qualification can run. |

## 2. Minimum AWS resource inventory

Create or identify only the resources below. Reuse an already-approved resource
when its isolation, region and permission boundaries meet this contract.

1. **Pilot account/environment boundary** in `ap-south-1`.
2. **GitHub OIDC provider** for `token.actions.githubusercontent.com`, if the
   account does not already have the approved provider.
3. **GitHub pilot deployment/qualification role**, assumable only through the
   repository's intended GitHub `pilot` environment.
4. **API ECR repository**.
5. **Clamd ECR repository** containing the approved non-base scanner image.
6. **ECS cluster**.
7. **ECS Fargate service** for the API + same-task clamd pair.
8. **ECS task execution role**.
9. **Application task runtime role**.
10. **CloudWatch log group** used by both containers.
11. **Synthetic-only S3 document bucket** in `ap-south-1`, with all public
    access block controls enabled, versioning enabled and default SSE-KMS.
12. **Application envelope KMS key**.
13. **S3 encryption KMS key**, unless an explicitly reviewed same-key design is
    already approved.
14. **Runtime Secrets Manager secret bundle**.
15. **Document-storage Secrets Manager secret bundle**.
16. **VPC/subnets + task security group** with only approved ingress from the
    HTTPS entry point and the existing approved egress path.
17. **HTTPS ingress** (existing approved load balancer/target group/certificate/
    DNS path or equivalent current ingress). Do not invent a second ingress.
18. **Dedicated synthetic PostgreSQL** reachable by the task.
19. **Dedicated TLS Redis / approved Redis path** reachable by the task.
20. **Textract permission** for the runtime identity in `ap-south-1`. Textract
    itself is an AWS API, not a separately provisioned server.
21. **Independent operations credential** stored in Secrets Manager and as the
    protected GitHub environment secret used for qualification.

The pilot does not require a standalone scanner service, scanner load balancer,
scanner security-group ingress, or port 3310 exposure.

## 3. Least-privilege IAM role map

### 3.1 GitHub deployment / qualification identity

**Trust boundary**

- Principal: the account's GitHub Actions OIDC provider.
- Audience: `sts.amazonaws.com`.
- Subject must be restricted to this repository and the intended `pilot`
  GitHub environment, for example the environment subject form for
  `sohamsadegaonkar/Nexa_Care`.
- No static AWS access keys.
- No `AdministratorAccess`.

**Permission boundary**

Grant only what the activation path actually uses:

| Purpose | Actions | Resource scope |
| --- | --- | --- |
| Authenticate to ECR | `ecr:GetAuthorizationToken` | `*` only where AWS requires it |
| Push/inspect API + clamd images | `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage`, `ecr:DescribeImages` | exact two ECR repositories |
| Register/inspect task definitions | `ecs:RegisterTaskDefinition`, `ecs:DescribeTaskDefinition` | task-definition registration requires `*`; compensate with exact `iam:PassRole`, branch/environment trust and rendered-task preflight |
| Deploy/inspect service | `ecs:UpdateService`, `ecs:DescribeServices` | exact pilot cluster/service where supported |
| Run release/qualification one-off tasks | `ecs:RunTask`, `ecs:DescribeTasks`, `ecs:StopTask` when cleanup is required | exact task-definition family and pilot cluster; use cluster conditions |
| Pass ECS roles | `iam:PassRole` | exact execution + runtime role ARNs, conditioned to `ecs-tasks.amazonaws.com` |
| Resolve approved roles | `iam:GetRole` | exact execution + runtime roles |
| Verify secret metadata | `secretsmanager:DescribeSecret` | exact runtime + document-storage secrets |
| Verify KMS metadata | `kms:DescribeKey` | exact application + storage KMS keys |
| Verify S3 security metadata | `s3:GetBucketLocation`, `s3:GetEncryptionConfiguration`, `s3:GetBucketPublicAccessBlock`, `s3:GetBucketVersioning` | exact pilot bucket |
| Verify log group | `logs:DescribeLogGroups` | restrict with account policy/conditions where possible |

The deployment role does **not** need broad secret-value reads for the
repository's verification workflows.

### 3.2 ECS task execution role

This role is for the ECS/Fargate agent, not application code.

Permit only:

- private ECR image pull for the API and clamd repositories;
- CloudWatch `awslogs` stream creation/writes to the pilot log group;
- `secretsmanager:GetSecretValue` for the exact secret references injected by
  the task definition;
- `kms:Decrypt` only when those Secrets Manager values use a customer-managed
  KMS key that requires it.

The task execution role must not become the application's general AWS identity.

### 3.3 Application runtime role

The API container receives the task role through the normal ECS credential
chain. Do not inject `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or
`AWS_SESSION_TOKEN`.

Minimum application permissions are the relevant subset of:

- `textract:AnalyzeDocument`; scope/conditions as narrowly as the AWS action
  permits for this non-adapter flow;
- `s3:ListBucket` on the exact bucket;
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on that bucket's object
  ARN;
- `s3:GetEncryptionConfiguration`,
  `s3:GetBucketPublicAccessBlock`, `s3:GetBucketVersioning` on the exact
  bucket;
- `kms:DescribeKey`, `kms:GenerateDataKey`, `kms:Decrypt` on the exact
  application/storage keys.

Secrets injected by ECS remain an **execution-role** responsibility unless
application code is later explicitly approved to call Secrets Manager itself.

## 4. Operator input contract

Never commit a filled activation file. Copy the sanitized template outside the
repository:

```bash
cp deploy/ecs/pilot-activation-inputs.template.json /tmp/nexa-pilot-activation.json
```

Fill the copy with the approved values:

```text
ROLE_ARN
AWS_REGION=ap-south-1
ECS_CLUSTER
ECS_SERVICE
ECR_REPOSITORY
CLAMD_ECR_REPOSITORY
API_IMAGE_DIGEST
CLAMD_IMAGE_DIGEST
RUNTIME_SECRET_ID
DOCUMENT_STORAGE_SECRET_ID
API_BASE_URL

ECS_EXECUTION_ROLE_ARN
ECS_TASK_ROLE_ARN
QUALIFIED_ECR_IMAGE_URI_BY_DIGEST
QUALIFIED_CLAMD_IMAGE_URI_BY_DIGEST
DOCUMENT_STORAGE_S3_BUCKET
DOCUMENT_STORAGE_S3_KMS_KEY_ID
APPLICATION_ENVELOPE_KMS_KEY_ID
CLOUDWATCH_LOG_GROUP

all Secrets Manager value references required by the task definition
FINAL_API_HOST
FINAL_DOCTOR_HTTPS_ORIGIN
FINAL_TRUSTED_PROXY_CIDRS
FINAL_FORWARDED_PROXY_CIDRS
```

The raw `OPERATIONS_AUTH_TOKEN` is deliberately **not** a field in that file.
Use:

- `OPERATIONS_AUTH_TOKEN_SECRET_REFERENCE` when rendering the ECS task; and
- GitHub environment secret `NEXA_PILOT_OPERATIONS_AUTH_TOKEN` for protected
  live qualification.

The other live workflow values are:

```text
NEXA_PILOT_AWS_ROLE_ARN
NEXA_PILOT_ECS_CLUSTER
NEXA_PILOT_ECS_SERVICE
NEXA_PILOT_ECR_REPOSITORY
NEXA_PILOT_RUNTIME_SECRET_ID
NEXA_PILOT_DOCUMENT_STORAGE_SECRET_ID
NEXA_PILOT_API_BASE_URL
NEXA_PILOT_OPERATIONS_AUTH_TOKEN   # secret
```

## 5. Offline activation preflight

This stage requires **no AWS credentials**.

Render and validate outside the repository:

```bash
python scripts/check_aws_pilot_activation.py \
  /tmp/nexa-pilot-activation.json \
  --render-output /tmp/nexa-care-pilot-task-definition.json
```

It must fail on:

- absent or placeholder activation inputs;
- malformed IAM role ARNs;
- region drift away from `ap-south-1`;
- tag-only or `:latest` API/scanner images;
- image digest/repository/account mismatches;
- static AWS credential variables;
- plaintext task secret values instead of Secrets Manager references;
- non-Fargate/non-`awsvpc` task topology;
- any scanner port mapping;
- missing essential API + clamd pair;
- scanner transport other than task-local loopback;
- provider drift away from Textract/S3/KMS/clamd;
- wildcard/public trusted-proxy CIDRs.

The rendered JSON is intentionally refused if the output path is inside the
repository.

## 6. Read-only AWS metadata resolution

After an authorized temporary AWS identity exists, use the existing generator.
It performs only approved metadata reads and writes output outside the repo.

Example operator shape:

```bash
python scripts/generate_pilot_deployment_values.py \
  --profile <TEMPORARY_APPROVED_PROFILE> \
  --storage-profile <TEMPORARY_APPROVED_STORAGE_PROFILE> \
  --region ap-south-1 \
  --repository-name <API_ECR_REPOSITORY> \
  --image-tag <QUALIFIED_API_TAG> \
  --scanner-repository-name <CLAMD_ECR_REPOSITORY> \
  --scanner-image-tag <QUALIFIED_CLAMD_TAG> \
  --execution-role-name <EXECUTION_ROLE_NAME> \
  --task-role-name <TASK_ROLE_NAME> \
  --log-group-name <LOG_GROUP> \
  --bucket <SYNTHETIC_PILOT_BUCKET> \
  --runtime-secret-id <RUNTIME_SECRET_ID> \
  --storage-secret-id <DOCUMENT_STORAGE_SECRET_ID> \
  --envelope-kms-key-id <APPLICATION_KMS_KEY> \
  --storage-kms-key-id <STORAGE_KMS_KEY> \
  --output /tmp/nexa-pilot-resolved-values.json
```

Do not create a long-lived IAM-user access key simply to run this generator.
Use the account's approved temporary identity mechanism.

## 7. Cost guardrails

The cheapest safe posture is deliberately small, not weakened:

- Keep focused qualification at `desiredCount=1`.
- When no qualification window or retained pilot traffic is authorized, the
  account owner may scale the synthetic-only service to `desiredCount=0`
  after confirming no extraction/release task is active. This is an operational
  cost action, not a production availability recommendation.
- Do not run a second scanner service. The one clamd sidecar in the API task is
  the only scanner runtime.
- Keep the qualification baseline at 1 vCPU / 3 GiB total unless measured
  startup/runtime evidence shows it is inadequate.
- Apply ECR cleanup only to unqualified/obsolete images; retain the current
  candidate and explicitly selected rollback API + scanner digest pair.
- Set an explicit finite CloudWatch retention suitable for the approved
  synthetic pilot and evidence window instead of leaving unlimited retention by
  accident. Do not delete evidence still required for security review.
- **Do not add S3 lifecycle/retention rules in this slice.** The repository's
  retention decision is still a governance boundary; cost reduction cannot
  override it.
- Reuse the account's approved secure egress/ingress path. Do not add a NAT
  gateway, duplicate load balancer, duplicate database, or duplicate scanner
  merely for convenience; likewise, do not remove a required secure network
  control only to save cost.
- Delete/scale down only synthetic pilot resources when the account owner's
  evidence-retention and rollback requirements permit it.

Never reduce encryption, malware scanning, audit, health checks, HTTPS, IAM
separation, or fail-closed behavior for cost.

## 8. Deterministic account-owner activation order

### Step 1 — Establish account and region

Confirm the approved pilot account and `ap-south-1`. Record account-specific
identifiers only in protected operator/GitHub environment configuration, not in
committed repository files.

### Step 2 — Configure GitHub OIDC

Create/reuse the GitHub OIDC provider and configure the least-privilege pilot
role with repository + environment trust restrictions.

### Step 3 — Establish ECS roles

Create/reuse the separate task execution and runtime roles described above.

### Step 4 — Create/reuse ECR repositories

Use separate API and clamd repositories unless the account's approved repository
layout already gives equivalent digest isolation and policy control.

### Step 5 — Build and push immutable images

Build the API image from the exact reviewed Git commit. Mirror/qualify the
approved non-base clamd image into the account's ECR. Record both immutable
`sha256:` digests. Never deploy `latest`.

### Step 6 — Create/reuse S3, KMS and secret bundles

Confirm:

- S3 in `ap-south-1`;
- all four public-access block settings enabled;
- versioning enabled;
- default SSE-KMS;
- application envelope KMS key enabled for `ENCRYPT_DECRYPT`;
- storage KMS key enabled for `ENCRYPT_DECRYPT`;
- runtime and document-storage Secrets Manager bundles contain the required
  keys;
- no secret value is copied into Git, issue/PR text or qualification evidence.

Application envelope encryption remains required even when S3 SSE-KMS is
enabled.

### Step 7 — Establish networking and HTTPS ingress

Use approved subnets/security groups and the existing HTTPS ingress design.
Only the API port is exposed to approved ingress. There is no clamd port mapping
and no security-group ingress for 3310.

### Step 8 — Resolve values and run offline preflight

Complete Sections 4-6. Do not continue if the offline validator fails.

### Step 9 — Register the rendered task definition

Only after authorization:

```bash
aws ecs register-task-definition \
  --region ap-south-1 \
  --cli-input-json file:///tmp/nexa-care-pilot-task-definition.json
```

Record the returned task-definition ARN in protected operator evidence. Do not
paste the rendered task definition into public logs or PR comments.

### Step 10 — Run the one-time migration release task

Use the **same immutable API image** and the approved migration command:

```text
python scripts/run_pilot_migrations.py
```

Supply only the protected `MIGRATION_DATABASE_URL` to that release task. It
must exit zero and prove repository/database head:

```text
20260919_medication_catalog
```

The API container never performs schema migration on startup.

### Step 11 — Deploy/update the candidate service

Only after the migration task passes:

```bash
aws ecs update-service \
  --region ap-south-1 \
  --cluster <ECS_CLUSTER> \
  --service <ECS_SERVICE> \
  --task-definition <NEW_TASK_DEFINITION_ARN>

aws ecs wait services-stable \
  --region ap-south-1 \
  --cluster <ECS_CLUSTER> \
  --services <ECS_SERVICE>
```

### Step 12 — Run runtime preflight and protected health

From the intended task identity:

```bash
python scripts/check_pilot_environment.py --live-aws
```

Then verify:

- `GET /healthz`;
- `GET /health`;
- protected `GET /ops/health`;
- protected `GET /metrics`.

The protected health response must report the patient-source scanner as
configured and ready. Scanner readiness includes daemon reachability, bounded
signature freshness and a real clean scan probe; a TCP listener alone is not
readiness.

Run the manual `Live Cloud Qualification` workflow after the GitHub `pilot`
environment values/secrets are wired. That workflow verifies AWS identity,
stable Fargate service, immutable images, D6 topology, secret metadata and
health surfaces. It still does **not** replace the functional tests below.

### Step 13 — Clean synthetic patient import

Use only an approved synthetic patient account and a non-sensitive synthetic
PDF/PNG/JPEG that the current Textract contract can extract.

Use the existing patient-self import workflow:

1. `POST /api/v2/patient/me/external-records` with a synthetic file and
   category;
2. `POST /api/v2/patient/me/external-records/{import_id}/process`;
3. require transition to patient review rather than retry/terminal failure;
4. `GET /api/v2/patient/me/external-records/{import_id}/review`;
5. submit explicit synthetic review decisions;
6. `POST /api/v2/patient/me/external-records/{import_id}/save`;
7. confirm the external `DocumentReference` + `TimelineEvent` result through
   the existing patient record surface.

Record only IDs/statuses that are approved for sanitized qualification
evidence. Never retain extracted clinical-looking values in the manifest.

Mark `clean_synthetic_import=PASS` only after the live deployed task reaches
review/save successfully.

### Step 14 — Harmless antivirus-test rejection

Upload the approved harmless antivirus test source through the **same** patient
import path and invoke `process`.

Required result:

- scanner returns the malicious/terminal policy outcome;
- the import cannot enter review/save;
- no canonical document/timeline output is created.

The deployed exact Git/image identity is part of the proof: in the current
implementation, `qualify_patient_source_for_extraction(...)` executes before
the Textract extractor is constructed/called. Therefore the combination of:

1. exact deployed repository/API digest;
2. live terminal scanner-blocked result; and
3. absence of review/canonical output

is the qualification evidence for `eicar_blocked_before_extraction=PASS`.
If any of those facts cannot be established, record `BLOCKED` or `NOT_RUN`,
not PASS.

Do not log the source bytes or scanner signature text.

### Step 15 — Scanner-outage fail-closed proof

Do **not** break the running pilot service merely to create evidence.

Run an isolated one-off Fargate task from the same immutable candidate task
definition/network with an API environment override pointing the scanner client
to an unused task-local port. Keep the real clamd sidecar present. The API
startup preflight must fail because scanner readiness cannot be established.

This proves the deployed image remains fail closed under task-local scanner
unavailability without making the service accept unscanned uploads.

A successful API startup or a clean import while the scanner is unreachable is
a qualification failure.

### Step 16 — Inspect sanitized logs/metrics and create evidence

Inspect CloudWatch and protected metrics for:

- no PHI/source text/filename leakage;
- no secret/token/credential leakage;
- scanner health only in coarse configured/ready/unavailable terms;
- no scanner public endpoint;
- no static AWS credentials.

Copy `deploy/ecs/pilot-runtime-evidence-v2.template.json` outside the
repository, replace placeholders only with approved sanitized evidence, and
keep top-level `status=BLOCKED` until every required check has measured
evidence.

Validate it:

```bash
python scripts/validate_pilot_runtime_evidence.py \
  /tmp/nexa-pilot-runtime-evidence-v2.json
```

A top-level PASS is structurally rejected if any D6 live check is
`FAIL`, `BLOCKED` or `NOT_RUN`.

### Step 17 — Stop/scale down when appropriate

After evidence collection and when no release/extraction task is active, the
account owner may scale the synthetic service to zero according to the approved
pilot availability/evidence-retention plan. Do not delete current/rollback
image digests or required evidence.

## 9. Clamd pilot contract

The live pilot must prove all of the following against the deployed task:

- API + clamd are in the same Fargate task;
- both image identities are immutable digests;
- clamd has no task port mapping/public ingress;
- API connects only to task-local clamd;
- signature database is inside the configured 48-hour freshness budget;
- protected scanner health is configured + ready;
- connect/scan timeouts remain bounded;
- clean synthetic source reaches extraction/review/save;
- harmless antivirus-test source is blocked before extraction;
- scanner unavailability fails closed;
- retry cannot bypass the scanner gate.

No repository/CI result substitutes for those runtime measurements.

## 10. Textract contract

Do not replace Textract. Pilot configuration remains:

```text
DOCUMENT_EXTRACTION_PROVIDER=aws_textract
DOCUMENT_AI_AWS_REGION=ap-south-1
```

The application uses the ECS task role through the AWS SDK credential chain.
Static AWS access keys remain forbidden.

## 11. S3 + KMS contract

Do not redesign cryptography.

- S3 bucket region: `ap-south-1`.
- All four S3 public-access block controls: enabled.
- S3 versioning: enabled.
- S3 default server-side encryption: SSE-KMS.
- Application envelope backend: KMS.
- Client/application envelope encryption remains independent of S3 SSE-KMS.
- Runtime role is restricted to the exact bucket/object and KMS key resources.
- Existing application AAD/encryption-context semantics remain unchanged.
- No S3 lifecycle policy is added by this activation slice.

## 12. External inputs still required

A live run remains blocked until the account owner supplies or creates:

```text
ROLE_ARN
ECS_CLUSTER
ECS_SERVICE
ECR_REPOSITORY
CLAMD_ECR_REPOSITORY
immutable API image digest
immutable clamd image digest
ECS deployment authorization
ECS execution role ARN
ECS runtime task role ARN
RUNTIME_SECRET_ID
DOCUMENT_STORAGE_SECRET_ID
all task-definition Secrets Manager value references
S3 bucket and KMS key identifiers
CloudWatch log group
approved VPC/subnets/security groups/HTTPS ingress
synthetic PostgreSQL and TLS Redis connectivity
OPERATIONS_AUTH_TOKEN through protected secret channels
API_BASE_URL
protected /ops/health access
protected /metrics access
synthetic patient pilot credentials
live AWS inspection/deployment authorization
```

Do not fabricate any of them.

## 13. Live deployment state

Until the account wiring above exists and Steps 9-16 actually run:

```text
PRODUCTION SCANNER DEPLOYMENT NOT_RUN
LIVE AWS PILOT NOT_RUN
```

Repository readiness, controlled clamd integration, task-definition validation,
or a successful offline render must never be reported as a live AWS deployment.


### Slice 10B.5k medication-catalog signing readiness

The pilot runtime contract now requires the deployment-specific key identifier
`MEDICATION_CATALOG_SIGNING_KEY_ID`. The key is not provisioned by this
repository slice. Before live activation, operations must supply an existing
dedicated asymmetric KMS key whose metadata proves `SIGN_VERIFY`,
`ECC_NIST_P256`, and `ECDSA_SHA_256`, and scope task-role IAM to the exact key
with only the required catalog signing/verification actions. This readiness
contract does not change `PRODUCTION SCANNER DEPLOYMENT NOT_RUN` or
`LIVE AWS PILOT NOT_RUN`.
