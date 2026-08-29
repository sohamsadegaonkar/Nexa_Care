# Milestone 6 Fargate deployment and Textract qualification

This is a platform-neutral operator plan for the isolated Milestone 6 physical
Android qualification. It creates no infrastructure by itself. Substitute only
approved deployment values at execution time; never commit domains, account
identifiers, role ARNs, database URLs, Redis URLs, credentials, or secrets.

## Immutable release inputs

1. Begin from a reviewed clean commit and record its full Git SHA as
   `<full-git-sha>`.
2. Build the repository Dockerfile and tag the image with the full SHA. Do not
   use `latest` as the deployed identity.
3. Push the immutable image to the approved ECR repository in `ap-south-1`, for
   example `<ecr-image-uri>:<full-git-sha>`, and record its digest.
4. Record the matching doctor-frontend commit and Android build profile. The
   backend, web, and device evidence must identify the versions actually used.

## Infrastructure preparation

- Create or select the isolated ECR repository and ECS cluster in
  `ap-south-1` under `<aws-account-id>`.
- Use a dedicated Supabase PostgreSQL database and a dedicated TLS
  Redis/Upstash instance. They must not contain real patient PHI.
- Create an S3 bucket in `ap-south-1` with public access blocked, versioning and
  approved lifecycle/retention controls, and SSE-KMS using a configured key.
- Configure the shared application envelope KMS key and the S3 KMS key.
  `AWS_PATIENT_SPECIFIC_KMS_KEYS=false` remains explicit for qualification.
- Store backend secrets in AWS Secrets Manager or SSM Parameter Store. ECS task
  definition secret entries reference `<secret-arn>`; no plaintext secret is
  placed in the task definition, image, logs, repository, or frontend.
- Provision an HTTPS Application Load Balancer for `<api-pilot-domain>`. Its
  target group uses `/healthz`; TLS terminates only at the approved ingress.
  Restrict the task security group to traffic from that load balancer.

## ECS roles and minimum application permissions

Use separate roles:

- Execution role `<ecs-execution-role-arn>`: ECR image pull, CloudWatch log
  delivery, and retrieval of only the task-definition secret references.
- Application task role `<ecs-task-role-arn>`: runtime Textract, S3, and KMS
  access only. Do not put AWS access keys in environment variables.

Constrain all resource permissions to the configured bucket/key resources and
the deployed region. The minimum task-role actions for the current code paths
are:

```text
textract:AnalyzeDocument
s3:ListBucket
s3:GetObject
s3:PutObject
s3:DeleteObject
kms:DescribeKey
kms:GenerateDataKey
kms:Decrypt
```

The bucket permission is metadata/readiness only; object actions are restricted
to the qualification object prefix. Do not grant `s3:ListAllMyBuckets`, broad
KMS administration, Textract asynchronous APIs, or KMS key creation/deletion.
The preflight identity call uses the SDK identity chain and does not justify
broader application permissions.

## Fargate task definition and service

1. Define a Linux Fargate task using `<ecr-image-uri>:<full-git-sha>` (prefer the
   recorded digest), container port 8000, and the non-root image user.
2. Reference managed secrets and provide only non-secret runtime settings. Run
   `python scripts/check_pilot_environment.py` against the final configuration
   before enabling traffic.
3. Configure `FORWARDED_ALLOW_IPS` and `TRUSTED_PROXY_NETWORKS` to the exact
   final ingress addresses/CIDRs. Wildcard forwarding trust is prohibited.
4. Send container stdout/stderr to a dedicated CloudWatch log group with
   approved retention. Do not log tokens, patient/provider identities, document
   content, object names, credentials, URLs containing credentials, or secret
   ARNs.
5. Configure container and target-group liveness on `GET /healthz`. Do not use
   `GET /health` as a restart loop; it is the human/deployment readiness gate.
6. Create the ECS service with `desiredCount=1` and no autoscaling. Do not use a
   serverless or scale-to-zero backend. Do not deploy, restart, or scale while
   an extraction is active.

## One-time database release task

1. Back up the dedicated database and verify the approved restore procedure.
2. Launch a one-off Fargate task from the same immutable image and network
   boundary, overriding only the command to
   `python scripts/run_pilot_migrations.py`.
3. Supply `MIGRATION_DATABASE_URL` through a managed secret reference. The API
   service must not receive or run the migration command.
4. Require the task to exit zero and report both repository and database head
   `20260827_patient_public_id`. Stop if it reports any other head.
5. Start/update the API service only after the migration task succeeds. Require
   HTTPS `GET https://<api-pilot-domain>/healthz` and then
   `GET https://<api-pilot-domain>/health` to pass before qualification traffic.

## Doctor frontend on Vercel

1. Deploy the reviewed doctor frontend to `https://<doctor-pilot-domain>`.
2. Set server-only `API_PROXY_TARGET=https://<api-pilot-domain>` so Next.js
   rewrites `/api/:path*` to the backend. Configure the browser API base as the
   doctor origin/same-origin path; do not expose backend secrets.
3. Set backend `CORS_ALLOWED_ORIGINS` to the explicit doctor HTTPS origin and
   `TRUSTED_HOSTS` to the API host. Verify secure HttpOnly session cookies,
   CSRF-cookie/header double submit, origin rejection, and logout through the
   same-origin rewrite.

## Android build and synthetic enrollment

1. Set the production/qualification EAS environment
   `EXPO_PUBLIC_API_URL=https://<api-pilot-domain>` and keep HTTP fallback off.
2. Produce a new EAS Android build; changing the deployed API URL requires a new
   build because Expo public configuration is bundled into the application.
3. Install that exact build on the physical Android device and record its build
   identity. Confirm HTTPS API reachability without ADB reverse or LAN URLs.
4. Create only the approved synthetic patient/provider identities and synthetic
   one-page test document. Enroll the physical device and establish fresh
   document-processing consent with `documents` scope. Capabilities remain
   memory-only and bound to patient, provider, hospital, session, and workflow.

## Focused qualification

1. Confirm the ECS service has exactly one healthy task and no deployment or
   scaling event is active.
2. Confirm `/healthz` and `/health`, CloudWatch safe logs, migration head, TLS
   Redis, KMS/S3 readiness, and `DOCUMENT_EXTRACTION_PROVIDER=aws_textract`.
3. In the doctor UI begin at **Upload & AI Extract**, select only the synthetic
   document, and complete the physical-device consent flow.
4. Verify real Textract evidence includes field-level source text, page,
   bounding box, and confidence. Do not accept fabricated or source-free data.
5. Accept only automated `SOURCE_ONLY` or `QUARANTINE` routing. Runtime
   `AUTO_COMMIT` remains disabled; clinical commitment requires explicit
   clinician source adjudication.
6. Preserve versioned qualification evidence without patient data, secrets,
   tokens, object names, or document content.

## Rollback

1. Stop new qualification traffic.
2. Stop uploads.
3. Preserve database, S3, and audit evidence.
4. Roll back Vercel and the ECS service to the last qualified immutable
   frontend version and image digest.
5. Do not automatically downgrade PostgreSQL; use an approved forward fix after
   impact review.
6. Invalidate sessions and require fresh consent before resuming.
7. Revoke `<ecs-task-role-arn>` access only when containment is required.

After rollback, keep `desiredCount=1`, verify `/healthz` and `/health`, and do
not resume until audit, consent, database, Redis, KMS, S3, web-cookie/CSRF, and
physical-device boundaries have been requalified.
