# Azure staging deployment and recovery

Scope is an approved synthetic-only deployment of the original FastAPI, Next
and Expo code. Treat Azure billing, access, AWS and Supabase activation as
separate owner decisions. This runbook applies to the Central India staging
templates in [deploy/azure](../../deploy/azure).

## Foundation and prerequisites

1. Record the approved Azure subscription, staging resource group, available
   credit, billing ceiling and expiry. Check registration, capacity and policy
   for Microsoft.App, Microsoft.Network, Microsoft.DBforPostgreSQL,
   Microsoft.Cache, Microsoft.ContainerRegistry, Microsoft.KeyVault,
   Microsoft.Storage, Microsoft.ManagedIdentity, Microsoft.OperationalInsights
   and Microsoft.Insights. Check Central India ACA environment and CPU quotas,
   PostgreSQL 16 and Standard B1ms availability, Balanced B0 non-HA and private
   links. Verify no existing workload uses the resource group or VNet.
2. Bind temporary deployment rights to the staging resource group. Separate
   control-plane deployment and exact-scope role assignments from Key Vault
   seeding, registry publishing and AWS role administration. Use reviewed
   GitHub environment protection and federated credentials before automating
   any cloud write. The checked-in PR workflow builds and tests without Azure
   or AWS credentials and does not publish images.
3. With spending approved, review provider validation and a minimal-output ARM
   what-if. Apply `foundation.bicep` in incremental mode. Supply the secure
   PostgreSQL administrator parameter from protected custody. Check the result
   for unexpected resources, same-region DNS, PostgreSQL delegation, private
   endpoints, Key Vault RBAC, purge protection and ACR admin access disabled.
4. From an approved staging-only private operator path, use TLS with hostname
   verification to reach PostgreSQL. Prepare `nexa_migrator` and
   `nexa_api_runtime` as independent LOGIN roles with no privileged attributes
   or role inheritance. Revoke public database and schema privileges. Allow
   the migrator to own the schema objects and enable only required `pgcrypto`.
   Grant runtime usage and the privileges required by the application on
   tables/sequences. Keep write and DDL privileges on `alembic_version` away
   from the runtime. Apply default privileges and verify effective grants
   again after migration. Use separate bootstrap credentials and an approved
   input channel. The AWS pilot DB bootstrap script uses AWS-specific naming
   and secret custody, so it must not be run against Azure.
5. Seed versioned Key Vault values via the isolated operator path. Runtime DB
   and migration DB credentials target the foundation FQDN and
   `nexacare_staging` using distinct expected usernames and
   `postgresql+asyncpg` with no TLS URL overrides. Use the image's maintained
   CA root bundle, verified against the Azure CA guidance. The Redis URL uses
   the exact foundation FQDN, `rediss`, port `10000`, DB 0 and
   `ssl_check_hostname=true`. Check certificate and hostname mismatch denial.
   Beware password escaping when constructing connection URIs. No credentials,
   keys, patient data or test phone numbers belong in GitHub, Notion or logs.
6. Independently approve synthetic-only tenancy with Supabase Auth and Twilio.
   Point the application at Auth only. Demonstrate signup/OTP restrictions,
   the controlled identity binding in Azure PostgreSQL and no reads of alpha
   application rows. If that cannot be proven, use a dedicated staging Auth
   project with the same provider contract. Put the private authorized E.164
   phone allowlist in Key Vault. Supply approved terms/privacy versions,
   copies and SHA-256 values required by the original onboarding flow.
7. Qualify the managed identity to AWS connection. A sandbox AWS trust must
   accept only the reviewed Entra issuer, intended audience and exact workload
   subject. Confirm the actual token version and IAM condition-key mapping
   with a dedicated test principal. The SDK credential process must obtain a
   short-lived role for the isolated Mumbai staging S3 bucket, KMS wrapping
   and SSE keys, catalog signing key metadata/public key, and required Textract
   operations. Prove negative access to existing/pilot buckets and keys with
   IAM policy review and permissions tests using known staging resources.
   Keep the API secrets and registry pull identities unavailable to running
   containers. The ClamAV sidecar shares the replica trust boundary, including
   access to the workload identity endpoint. Verify its digest and provenance,
   and keep clinical production designs subject to explicit review of that
   sidecar boundary. [AWS SDK credential process](https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html)

## Build, migrate and activate

1. Check out the reviewed exact commit. Build the API using the root Dockerfile
   and `INSTALL_AZURE_SDK=true`. Build the original Next workspace with
   `nexa-client/Dockerfile.azure-staging` and the exact returned `webOrigin`
   for `NEXT_PUBLIC_API_URL` and `apiOrigin` for `API_PROXY_TARGET`. Build
   the scanner using `scanner_integration/Dockerfile` and fresh definitions.
   Verify dependency and image scans, provenance, SBOM and release image
   digests. Publish those exact images to the dedicated ACR via the approved
   identity as `nexa-api`, `nexa-web` and `nexa-clamd`. Anchor release evidence
   to their digests, source revision, architecture and timestamp.
2. Verify the PostgreSQL network and roles. Create the `migration.bicep`
   manual job using the exact API digest and migration secret version. The
   job checks the Azure FQDN, database, role and migration graph. Start a
   single approved execution and wait for success before creating the API
   app. The runner verifies both the repository and the database at
   `20261120_document_access`. Alembic holds the staging advisory lock on
   the executing database connection. Evidence must include the exact job
   revision, current head and reviewed effective privileges.
3. Validate `application.bicep` with the foundation outputs, approved
   external test IPv4 addresses and verified private ingress peer addresses.
   Validate ingress host forwarding and Azure's rightmost XFF boundary from
   controlled sources before enabling OTP. Use the reviewed staging peer
   addresses only. If the platform cannot provide a stable trustworthy peer,
   hold activation and use an independently qualified trusted proxy
   design. Ensure only the Next app is reachable outside the ACA environment.
4. Use `application.bicep` in incremental mode with approved image digests
   and version IDs for every named secret. The API validates database and
   Redis targets, then runs the canonical entrypoint and hosted startup checks.
   The Next image verifies its origins match runtime metadata. FastAPI
   `/healthz` and `/health` probe liveness and readiness respectively.
   Next `/readyz` reflects the private API dependency response. Check protected
   API operations health with an authorized mechanism. Verify log redaction,
   VA/RBAC audit access, and App/Vault activity evidence before test data use.
5. Create synthetic identities through approved real app setup. Use the
   genuine approved patient/clinician browser flows and signed Expo build.
   Observe every row in the [qualification register](AZURE_STAGING_QUALIFICATION.md).
   Real SMS, actual reviewer and eligible clinician identities, and physical
   device checks require owner-coordinated test fixtures. Keep external
   traffic allowlisted and no clinical use.

## Migration phases after the first hosted rehearsal

| Phase | Engineering work | Exit evidence |
| --- | --- | --- |
| 1. Fresh hybrid staging | Isolated Azure services and actual app with approved staging-only AWS and Auth dependencies. Prove the current synchronous extraction path and qualify governed multipage provider configuration where required by the selected product release. | Full staged acceptance and isolated recovery with no real records. |
| 2. Azure adapter qualification | Independently qualify Blob, Key Vault and Document Intelligence in an isolated synthetic sandbox. Verify private access, permissions, immutable refs, exact plaintext hashes, AEAD context and patient ownership, key-version lifecycle, document limits, meaningful evidence and extraction outcomes for forms, tables and multipage cases. Establish Azure-based catalog signing or a separately approved retained signing service. | Value-safe benchmark, recoverable key versions, accurate erasure claims and hosted provider-specific preflight parity. The existing one-page developer adapter needs work before parity. |
| 3. Authorized historical rehearsal | Inventory existing object version identifiers, encrypted DEK metadata, format/algorithm versions, patient ownership, erasure state, storage hashes and retention holds. Create an encrypted export snapshot and record a signed integrity manifest. Rehearse on a separately authorized copy with a named custody owner. Prefer maintaining old-key read ability and explicitly versioning each conversion. Verify ciphertext hashes for unchanged copies and decrypted byte hashes where re-encryption changes ciphertext. Reconcile counts and references per tenant and checkpoint every conversion. | Independent restore of representative records with old and new key versions, authorization/audit/erasure parity, and a separately authorized privacy-approved cutover and backout plan. |
| 4. Cutover | After owner and independent reviewers approve, rehearse capture of incremental changes, then execute an approved write freeze and final reconciliation. Route an approved cohort to the new runtime only after the DB, object manifests, audit head and erasure state agree. Keep one write authority at a time. Verify Supabase identity, device and session continuity under the planned origin; issue fresh credentials/capabilities where appropriate. | Document measured downtime, RPO/RTO, failure handling and human sign-offs. Keep prior dependencies and keys recoverable until separate decommission authorization. |

Do not switch historical storage references in bulk. S3 and Blob prefixes,
cipher formats and authenticated data differ, and AWS KMS material does not
become Azure Vault material by changing an environment variable. Do not erase
old key versions or delete provider resources during this migration.

## Rollback and disaster recovery

1. Define rollback triggers before activation. Examples are readiness failures,
   scanner outage, unexplained auth/consent denials, incorrect tenancy, audit
   discontinuity and extraction or integrity regressions. Make the stage
   unavailable to testers, stop writes and preserve sanitized diagnostics and
   authoritative database/audit state. Maintain the failed revision digests.
2. If schema and APIs are backward compatible, restore the independently
   verified previous web/API/scanner digest set and compatible secret versions
   using the repository release commands and existing ingress restrictions.
   Verify route parity, origin, scanner, database head, authorization, consent,
   audit and protected operational health. Do not downgrade a database blindly,
   stamp around a failed schema or reopen before requalification.
3. Where a restore is required, choose a confirmed database/object/audit
   checkpoint within the backup window. Restore PostgreSQL to a private fresh
   server and verify the Alembic head, counts, audit chains/outbox and
   erasure registry. Restore an approved isolated version set of encrypted
   document objects and verify manifest signatures, ciphertext lengths/hashes
   and authorized decrypted hash equality with the corresponding retained
   wrapping-key versions. Retain both independently captured database and
   object evidence, including readback of a recovery artifact from private
   Blob under a separate recovery identity. Validate separately that
   permissions deny artifact writes by the application and reads by
   unauthorized recovery actors.
4. Start fresh Redis security state after a recovery and require renewed
   sessions, challenges and grants. Reconcile current expiry, revocations
   and erasure against the authoritative ledger/tombstones before serving.
   Legacy restore material must never re-enable erased data or revive
   an expired grant. Inspect ingress, key/version access and audit before
   approving any resume.
5. Record checkpoint and restore times, evidence links, manifest checksum,
   exact digests, verified outcomes and reviewer. Proposed staging target is
   combined RPO 24 hours and RTO 4 hours until measured. Production objectives
   require a separate approved service and recovery design. No restore,
   failover, data transfer or recovery-job schedule is implied by IaC.
