# Azure migration foundation — developer-only first slice

Status: draft / synthetic development only / not deployed. Do not provision or
transfer existing data based on this change.

## Why this boundary

The investor demonstration is the immediate milestone. Azure is the candidate
replacement for AWS infrastructure, not authorization to delete old resources
or migrate existing ciphertext. Keep the independent document-consent feature
branch unchanged. PostgreSQL and Redis retain their existing semantics. The
AWS KMS, S3, Textract and ECS/ECR paths remain intact until replacements are
reviewed, qualified and cut over separately.

## First implementation

The optional `azure_blob` document adapter is available only in local,
development and test with an explicit synthetic-only opt-in. Its account name
and dedicated `nexa-demo-*` container are validated. Neither the opt-in nor
a container name proves a data set is synthetic; the operator must use a
separate empty synthetic database and account/container, and never expose
development login on the public internet. Only managed identity is supported;
there is no shared-key/SAS/CLI fallback. It makes no resource creation calls.

Azure Blob receives AES-256-GCM ciphertext only (12-byte nonce followed by
ciphertext and tag). AAD binds account/container, owner namespace and exact
object key. Cloud blob names are opaque digests of the unique internal ref;
no patient identifiers, original filename, clinical values or source MIME
metadata are sent as object names/metadata. Writes are non-overwriting; reads
are size bounded and authenticated. Wrong owner, tenant, account, malformed
ref and legacy storage scheme are denied. Private container access is checked
before operations. SDK failures are normalized; diagnostic logging must remain
redacted. A logical current-blob delete is **not** proof of permanent erasure
from versions, snapshots, backups or soft-delete retention.

The application-level document key remains required. This does not implement
Azure Key Vault patient DEK wrapping, independent patient-specific destruction
or a new production-equivalent security assurance. No authorization, audit,
clinical extraction, identity-review, consent or emergency rule changes.
Existing source consumers still enforce their own authority, erasure and
source integrity. Do not use the new backend with historical S3/local refs:
storage selection does not rewrite references or route between providers.

## Dependencies and setup (not an execution authorization)

Install the optional SDKs alongside the ordinary project requirements only
after dependency/security review. On an independently authorized Azure-hosted
development environment with an explicitly scoped managed identity, configure:

- `ENVIRONMENT=development` (or a disposable test runtime)
- `DOCUMENT_STORAGE_PROVIDER=azure_blob`
- `DOCUMENT_STORAGE_AZURE_SYNTHETIC_ONLY=YES`
- `DOCUMENT_STORAGE_AZURE_ACCOUNT`: dedicated Nexa demo account
- `DOCUMENT_STORAGE_AZURE_CONTAINER`: dedicated private nexa-demo-* container
- `DOCUMENT_STORAGE_ENCRYPTION_KEY`: independently generated 32-byte key,
  supplied privately from approved secret custody, never committed
- Optional `DOCUMENT_STORAGE_AZURE_MANAGED_IDENTITY_CLIENT_ID`: explicit UUID

Do not reuse CampusPool's account resources, data, credentials, identity or
backup container. Do not create an extra Azure for Students subscription to
circumvent offer eligibility; use an appropriately permitted separate account
if needed. Verify actual subscription terms, budget, region, quotas and costs.

## Validation and migration gates

Offline tests exercise fake SDK round trips, ciphertext tampering, namespace
isolation, non-overwrite, delete, private-container check, failure normalization
and environment gates. These are not live Azure evidence. Before a protected
synthetic live smoke test: approve isolated resource/identity, exact RBAC, no
anonymous access/shared key, network restrictions, log redaction, retention,
cost, independent recovery and SDK dependency validation. Verify ciphertext
readback and logical deletion, with separate version/backup handling. No
production environment support until Key Vault, storage erasure, readiness,
provider contracts and operational reviews are complete.

The migration order is Azure Blob foundation; Key Vault envelope design with
patient/epoch binding and explicit old-key recovery; Azure Document Intelligence
with strict source/identity benchmark; Azure deployment/IaC and equivalent
preflight/rollback; independently verified restore; only then an approved
cutover. Existing AWS KMS-wrapped data cannot be unwrapped merely by choosing
a different vault. Preserve old AWS configuration, key IDs, backups and
adapters until authorized recovery/retention review. No automated deletion.

## Investor-demo acceleration (synthetic dataset confirmed)

The founder confirms the demo data are synthetic. Prefer a new isolated database
over copying historical data; no AWS data migration is required for this demo.
No deletion or reset of any existing resource is implied.

For the first cloud rehearsal, keep the established local envelope provider
in development mode, fetch its independent root secret through **version-pinned
Key Vault secret references** on Azure Container Apps, and select the explicit
demo extraction provider (deterministic synthetic output, NOT Azure Document
Intelligence or live OCR). This is a cost-saving demo profile, not a replacement
for patient-specific KMS cryptographic erasure or a production-equivalent mode.
Never expose development access to public ingress. The application still
requires PostgreSQL/Redis, signed consent, MFA, source scanning and audit.

`python scripts/render_azure_demo_plan.py <metadata-only-spec.json>` renders
an offline review candidate for an already-existing dedicated Azure environment
and user-assigned identity. See the input fixture in
`tests/test_azure_demo_plan.py` for the exact JSON schema. It requires versioned
Key Vault secret references (no actual secret values), a dedicated digest-pinned
ACR image, dedicated storage and internal-only ingress with 0–1 replicas.
It intentionally marks the output NOT DEPLOYABLE: private environment, scanner
sidecar, database/Redis TLS, source-retention and other gates remain outstanding.
Do not remove blockers or deploy the candidate as a shortcut.

Build a candidate API image locally with
`docker build --build-arg INSTALL_AZURE_SDK=true -t nexa-care-azure-demo .`.
No upload or ARM call is part of this step. The optional SDK CI installs SDKs,
checks import/dependency compatibility, and runs offline tests without Azure
credentials. PostgreSQL managed service/Redis endpoint, image registry, vault,
network and storage remain separately cost-approved resources. There is no
guarantee of zero cost from a replica limit; idle services and logs can bill.

The `DOCUMENT_EXTRACTION_PROVIDER=demo` provider intentionally returns only
synthetic fixture data, without real extraction evidence. Do not present that
as a successful report extraction. Azure Document Intelligence mapping and
clinical source-bound benchmarking remain a separate engineering task.
A genuine Azure Key Vault cryptographic wrapping provider remains a separate
security design; the demo's vault-backed secret custody is not equivalent.

References: https://learn.microsoft.com/azure/container-apps/manage-secrets
and https://learn.microsoft.com/azure/container-apps/ingress .
