# First Qualified Medication Catalog Release — Operator Readiness Runbook

**Slice:** 10B.5l  
**Scope:** package-independent readiness and controlled first-release execution  
**Prescription boundary:** `WRITE_PRESCRIPTION PERSISTENCE BLOCKED`

This runbook prepares the first real medication-catalog release without
embedding licensed terminology in Git and without bypassing the catalog
application-service authority model.

It does not authorize AWS resource mutation, SQL insertion, synthetic
activation, or Prescription persistence.

## 1. External prerequisites

Do not begin a real release until all of these exist:

1. approved NRCeS/SNOMED Affiliate Licence / organizational usage authority;
2. exact current CDCI/India Drug Extension package obtained through the
   authorized distribution channel;
3. local SHA-256 of that exact package;
4. authoritative regulatory evidence for every candidate classification;
5. real preparer, reviewer 1, reviewer 2, qualifier, and activator identities;
6. current GLOBAL `MEDICATION_CATALOG_RELEASE_REVIEW` grants for every actor
   who performs a governed catalog operation;
7. fresh MFA/session assurance for every governed mutation;
8. authorized PostgreSQL target at the exact repository Alembic head;
9. for pilot/staging/preview/production readiness DB access, the explicit
   `DATABASE_SSL_CA_PATH` must point to the approved CA bundle used by Nexa's
   shared database TLS contract;
10. dedicated medication-catalog KMS signing key configured through
   `MEDICATION_CATALOG_SIGNING_KEY_ID`;
11. IAM limited to `kms:DescribeKey`, `kms:Sign`, and `kms:Verify` on that
    exact signing key.

No item above may be simulated for a production release.

## 2. Source metadata file

Copy:

`docs/runbooks/medication-catalog-source-metadata.template.json`

to a protected operator working directory outside Git.

Populate only release-level provenance:

- exact package name;
- exact package version;
- official package release date;
- SHA-256 of the locally held licensed package;
- official NRCeS/MLDS source reference;
- opaque internal licence-governance reference;
- checked timestamp.

Do not copy terminology rows, RF2 content, medicine names, source documents,
credentials, or licence text into this file.

The readiness parser rejects unknown fields so the metadata file cannot quietly
become a carrier for licensed terminology content.

## 3. Verify the protected package without copying it

Run from the repository root:

```bash
python scripts/check_medication_catalog_release_readiness.py \
  --source-only \
  --source-metadata-file /protected/catalog/source-metadata.json \
  --terminology-package-file /protected/catalog/<licensed-package>
```

The checker streams the package locally and compares its SHA-256 with the
declared metadata. It never writes or uploads the package.

Expected result:

```text
status = READY
package_digest_verified = true
catalog_state_mutated = false
aws_mutated = false
```

A missing package, malformed metadata, wrong authority, stale/mismatched
version, or digest mismatch blocks the release.

## 4. Candidate identity/evidence binding

For every proposed positive entry, the exact IDENTITY evidence row used by the
catalog application service must bind:

- NRCeS or SNOMED identity authority;
- the exact declared terminology package version;
- the exact package SHA-256.

This makes package provenance part of the candidate-entry digest and therefore
part of both reviewer approvals and the final release manifest.

A candidate with a different package version or package digest is not ready.

The package digest proves byte identity of the local distribution. It does not
replace licence approval, clinical/regulatory review, or concept-specific
classification evidence.

## 5. Build the DRAFT only through governed application services

No direct SQL is permitted.

Use `MedicationCatalogApplicationService` for:

- `create_release`;
- `upsert_draft_entry`;
- `replace_draft_evidence`;
- `review_positive_entry`;
- `qualify_release`;
- `activate_release`.

The current repository does not expose a public catalog-governance API.

Do not invent an actor/MFA object in an operator script. A real mutating
operator entrypoint must receive a principal derived from existing trusted
provider-session state and must preserve all application-service permission,
MFA, idempotency, audit, and locking behavior.

Until such an approved session-bound operator path is available in the target
runtime, release mutations remain operationally blocked rather than being
performed through ad-hoc Python or psql.

## 6. Review requirements

Every potentially positive entry requires two exact-digest reviews.

Required invariant:

```text
preparer != reviewer_1
preparer != reviewer_2
reviewer_1 != reviewer_2
```

All reviewers must hold current GLOBAL
`MEDICATION_CATALOG_RELEASE_REVIEW` authority and fresh MFA.

Any entry or evidence edit invalidates both reviews.

Do not use role membership, affiliation, `PRESCRIBE_MEDICATION`,
`PROFESSIONAL_REVIEW`, `PRESCRIBING_ELIGIBILITY_REVIEW`, or
`TRUST_PERMISSION_MANAGE` as catalog-release authority.

## 7. First-release positive ceiling

The first real release must contain at least one proven positive concept and no
more than 20 `v1_universal_allowed=true` entries.

Twenty is a cap, not a target.

The readiness checker independently blocks:

- zero positive candidates;
- more than 20 positive candidates;
- UNKNOWN/restricted positive classifications;
- missing review binding;
- package-version/digest identity mismatch.

## 8. Read-only database readiness check

Set the dedicated operator database credential through:

`NEXA_MEDICATION_CATALOG_DATABASE_URL`

Do not pass the database URL on the command line.

For `pilot`, `staging`, `preview`, and `production`, also set
`DATABASE_SSL_CA_PATH` to the approved CA bundle. The readiness CLI passes a
`DatabaseConfig` through the same `build_database_connect_args(...)` helper
used by the application and Alembic, so certificate verification remains
`CERT_REQUIRED` with hostname checking enabled.

Do not add `ssl`, `sslmode`, `sslrootcert`, `sslcert`, or `sslkey`
query parameters to `NEXA_MEDICATION_CATALOG_DATABASE_URL`. Those values are
rejected because TLS policy is server-owned through `DATABASE_SSL_CA_PATH`.

`--source-only` performs no PostgreSQL connection and therefore does not
require database TLS configuration merely to validate/hash a protected local
terminology package.

Then run:

```bash
python scripts/check_medication_catalog_release_readiness.py \
  --source-metadata-file /protected/catalog/source-metadata.json \
  --terminology-package-file /protected/catalog/<licensed-package> \
  --release-id <release-uuid> \
  --expected-database-name <database-name>
```

This is read-only. It verifies:

- repository has exactly one Alembic head;
- target database revision equals that head;
- release source terminology version equals package version;
- package release date is not later than source cutoff;
- positive count is 1..20;
- positive candidates satisfy current closed policy and exact review digests;
- positive IDENTITY evidence binds package version + package digest;
- persisted positive flags agree with server policy after qualification;
- no effective emergency DENY affects a positive code;
- no multiple-ACTIVE inconsistency exists;
- published manifest/projection digest agrees when the release is published.

The report intentionally omits medication display/name, evidence references, and
source text.

## 9. Optional real KMS read/verify preflight

Only in an authorized AWS/runtime environment, add:

`--live-kms-preflight`

The checker constructs the existing production
`AwsKmsMedicationCatalogSigningProvider`.

For a DRAFT release it performs read-only key readiness through
`DescribeKey`.

For a QUALIFIED/ACTIVE release it additionally verifies the already persisted
detached release signature.

The readiness checker never calls KMS `Sign` and never mutates AWS.

The real `qualify_release` operation remains responsible for the actual
release signing call.

Required key metadata remains:

```text
KeyState = Enabled
KeyUsage = SIGN_VERIFY
KeySpec = ECC_NIST_P256
SigningAlgorithms contains ECDSA_SHA_256
```

There is no local production-key fallback.

## 10. Pre-qualification checklist

Before `qualify_release`:

- exact package digest verified locally;
- licence/governance evidence approved outside the repository;
- source cutoff current;
- every proposed positive has all seven required evidence dimensions;
- every positive classification is the exact safe v1 state;
- exact terminology identity/formulation is active and sufficiently granular;
- both reviews are current and digest-bound;
- positive count is between 1 and 20;
- no emergency DENY affects a proposed positive;
- real catalog signing key passes readiness;
- target DB is at the exact repository head.

If any check is false, do not qualify manually.

## 11. Qualification and activation

Qualification and activation must use the normal application service.

Never update release status, digest, manifest, reviewer state, signature, or
allow flags directly in SQL.

After `qualify_release`, rerun the checker with
`--live-kms-preflight`.

Activation requires:

```text
release_status = QUALIFIED
manifest_projection_ok = true
signature_verified = true
positive_limit_ok = true
positive_present = true
reviews_and_policy_ok = true
identity_package_binding_ok = true
package_digest_verified = true
emergency_compatibility_ok = true
active_uniqueness_ok = true
activation_ready = true
```

Only then may the authorized activator invoke `activate_release`.

## 12. Post-activation runtime proof

After activation, execute the existing
`MedicationCatalogRuntimeService.resolve_v1_allowed_medication` boundary in
the authorized runtime.

Prove structurally:

- one known allowed code resolves;
- unknown code denies;
- real restricted negative controls deny;
- an isolated emergency-deny qualification case denies;
- isolated tamper/projection/signature cases deny.

Do not tamper with the production ACTIVE release to manufacture failure cases.

## 13. Sanitized release evidence

Permitted release evidence includes:

- release ID/version/status;
- source terminology version;
- package SHA-256;
- policy version;
- source cutoff;
- entry count and universal-allowed count;
- release manifest digest;
- signing key identifier;
- signature algorithm;
- reviewer/qualifier/activator approved internal IDs;
- activation timestamp;
- boolean readiness/verification results.

Do not publish:

- licensed package bytes;
- RF2/terminology files;
- full medicine lists;
- credentials/session tokens;
- MFA secrets;
- database URLs;
- private cryptographic material;
- unrestricted regulatory document text.

## 14. Exit/status behavior

The checker returns:

- exit 0 only when the requested source/readiness phase is READY;
- exit 2 when valid evidence is present but an external/readiness prerequisite
  is incomplete;
- exit 1 for invalid input, integrity failure, schema mismatch, or unavailable
  required dependency.

No checker result grants clinical prescribing authority.

## 15. Current Task-0 boundary

Until a real first release is independently prepared, reviewed, signed,
qualified, activated, and runtime-qualified:

`WRITE_PRESCRIPTION PERSISTENCE BLOCKED`

Do not implement Prescription, PrescriptionItem, treatment.write_prescription,
medication Timeline writes, active-medication projection, FHIR
MedicationRequest, or prescription issuance UI as part of this runbook.
