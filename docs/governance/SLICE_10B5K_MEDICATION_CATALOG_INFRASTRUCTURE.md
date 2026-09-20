# Slice 10B.5k — Medication Catalog Infrastructure

**Status:** IMPLEMENTED / exact-head qualification pending  
**Repository baseline:** `701460cc5223431c8a4167de6c94a22fc14153fd`  
**Migration:** `20260919_medication_catalog`  
**Parent:** `20260919_prescriber_eligibility`

## Scope

This slice implements medication-catalog authority infrastructure only. It creates no production medication release and no canonical Prescription persistence.

`WRITE_PRESCRIPTION PERSISTENCE BLOCKED` remains authoritative.

## Dedicated governance authority

The trust-management vocabulary adds GLOBAL-only:

`MEDICATION_CATALOG_RELEASE_REVIEW`

Catalog mutations require the existing provider organizational identity, the dedicated current GLOBAL grant, strong provider-session authentication, and fresh MFA. Clinical affiliation roles, `PROFESSIONAL_REVIEW`, `PRESCRIBING_ELIGIBILITY_REVIEW`, `TRUST_PERMISSION_MANAGE`, and `PRESCRIBE_MEDICATION` do not imply catalog authority.

## Domain schema

The linear migration creates four global reference/policy objects:

### MedicationCatalogRelease

Stores version, closed lifecycle status, source cutoff, policy and terminology versions, canonical manifest, SHA-256 integrity digest, detached signature/key/algorithm, preparer/qualifier/activator identities, lifecycle timestamps, and previous-release linkage.

Closed states:

`DRAFT | QUALIFIED | ACTIVE | SUPERSEDED | REVOKED`

A PostgreSQL partial unique index enforces at most one ACTIVE release.

### MedicationCatalogEntry

Stores release-bound string medication code, code-system metadata, generic identity/display/form/identity-strength facts, closed classification dimensions, server-derived `v1_universal_allowed`, entry digest, and two digest-bound reviewer slots.

`UNIQUE(release_id, medication_code)`.

### MedicationCatalogEvidence

Stores bounded structural evidence: finding dimension/value, official authority/version/reference, dates, rationale, SHA-256 evidence digest, and preparer. Full source documents are not persisted in normal relational/audit columns.

### MedicationCatalogEmergencyDeny

Append-only per-code events with monotonically increasing version and action `DENY | CLEAR`. CLEAR never grants medication authority; it only removes the emergency overlay and returns control to the ACTIVE catalog.

## Classification policy

The central policy can derive universal v1 authority only when all are true:

- terminology status ACTIVE;
- identity granularity sufficient;
- Schedule `NONE_CONFIRMED`;
- NDPS `NOT_CONTROLLED_CONFIRMED`;
- telemedicine `LIST_O_ANY_MODE`;
- special-recordkeeping `NONE_CONFIRMED`;
- Nexa high-risk `NONE_CONFIRMED`;
- regulatory product status `CURRENT`;
- all required evidence dimensions present;
- two independent positive reviewers;
- neither reviewer is the preparer;
- both review digests equal the exact candidate digest.

Unknown or restricted facts deny. Clients never supply `v1_universal_allowed=true`.

## Separation of duties

One release preparer is recorded. Every otherwise-positive DRAFT entry requires two independent catalog reviewers holding the dedicated GLOBAL permission. Reviewer 1 != reviewer 2 and neither may be the preparer.

Reviews bind the candidate digest. Changing DRAFT entry or evidence content invalidates both reviews.

Qualification and activation actors must differ from the preparer.

## Canonicalization and integrity

The release manifest uses the repository's bounded RFC 8785/JCS implementation for the complete emitted JSON shape. Floats and unsafe integers are forbidden and object ordering uses UTF-16 code-unit ordering.

Entries are sorted by medication code; evidence is sorted by stable structural keys.

`integrity_digest = SHA256(canonical_manifest_bytes)`

Volatile relational surrogate IDs are excluded so independent rebuilds reproduce identical bytes.

## Catalog signing boundary

`MedicationCatalogSigningProvider` is separate from patient encryption.

Production uses a dedicated AWS KMS asymmetric SIGN_VERIFY key constrained to:

- `ECC_NIST_P256`;
- `ECDSA_SHA_256`;
- enabled state.

KMS signing/verifying uses `MessageType=DIGEST`. Nexa sends the 32-byte SHA-256 digest to KMS and receives only the detached signature. The KMS private key is non-exportable and private key bytes never enter the application.

The deterministic in-memory P-256 signer is test-only and must be explicitly injected. Production signer construction rejects test-signer configuration and has no local-key fallback.

No KMS key is created by this slice.

## AWS configuration and IAM boundary

Production-like runtime requires `MEDICATION_CATALOG_SIGNING_KEY_ID`. Startup preflight fails closed unless the configured KMS key is enabled, SIGN_VERIFY, ECC_NIST_P256 and supports ECDSA_SHA_256.

Future runtime IAM is limited to the exact catalog key and only required operations such as `kms:DescribeKey`, `kms:Sign`, and `kms:Verify`. Never `kms:*`, never unrelated patient keys. Do not add `kms:GetPublicKey` unless actual application or runtime requirements justify it.

This slice performs no live AWS mutation. `PRODUCTION SCANNER DEPLOYMENT NOT_RUN` and `LIVE AWS PILOT NOT_RUN` remain unchanged.

## Lifecycle and DB immutability

Allowed release transitions:

- DRAFT -> QUALIFIED;
- QUALIFIED -> ACTIVE;
- QUALIFIED -> REVOKED;
- ACTIVE -> SUPERSEDED;
- ACTIVE -> REVOKED.

Published releases cannot return to DRAFT or be reactivated.

PostgreSQL triggers enforce:

- non-DRAFT release content cannot be arbitrarily rewritten/deleted;
- entries/evidence under non-DRAFT releases cannot be inserted/updated/deleted;
- emergency history cannot be updated/deleted.

DRAFT content remains mutable only through bounded governance services.

## Governance mutations and durable idempotency

The application service uses durable `mutation_idempotency` operations:

- `medication.catalog.release.create.v1`;
- `medication.catalog.entry.upsert.v1`;
- `medication.catalog.evidence.replace.v1`;
- `medication.catalog.entry.review.v1`;
- `medication.catalog.release.qualify.v1`;
- `medication.catalog.release.activate.v1`;
- `medication.catalog.release.revoke.v1`;
- `medication.catalog.emergency.v1`.

Same key + same request replays; changed request conflicts.

## Qualification

Qualification atomically:

1. authorizes and locks the DRAFT release;
2. locks entries/evidence;
3. recomputes candidate digests;
4. requires two bound independent reviews for every otherwise-positive entry;
5. derives all universal decisions server-side;
6. enforces the 20-positive-entry ceiling;
7. builds canonical manifest and SHA-256;
8. signs the exact digest and immediately verifies it;
9. persists manifest/digest/signature metadata;
10. transitions to QUALIFIED;
11. emits structural audit;
12. completes idempotency;
13. commits.

Any failure rolls back.

## Activation and concurrency

Activation locks the QUALIFIED target and all current ACTIVE release rows, re-verifies manifest/digest/key/signature, supersedes the prior ACTIVE release if present, activates the target in the same transaction, audits, and completes idempotency.

The partial unique ACTIVE index is the final database backstop.

Draft edit/review/qualification paths lock release/entry/evidence authority. Emergency events serialize per medication code with a transaction advisory lock and append monotonically versioned events.

## Runtime lookup

`MedicationCatalogRuntimeService.resolve_v1_allowed_medication(code)` accepts only the server-controlled medication code.

Success requires exactly one ACTIVE release, supported policy/schema, matching canonical manifest/stored digest/relational projection, trusted key+algorithm, valid detached signature, an ACTIVE terminology entry with `v1_universal_allowed=true`, and no effective emergency DENY.

Missing/multiple ACTIVE releases, unknown code, denied entry, unsupported versions, projection/digest/signature mismatch, unavailable signer/KMS/store, or emergency DENY fail closed.

The caller cannot choose release ID, catalog version, policy version or allow flag.

## Structural audit

Catalog audit records bounded structural facts such as release version, policy version, counts, medication code, release digest and reason/action codes. It excludes patient/prescription data, credentials, complete source documents and private cryptographic material.

## Migration authority

The sole linear head becomes:

`20260919_medication_catalog`

Active current-head contracts are reconciled in:

- `scripts/run_pilot_migrations.py`;
- `scripts/ci/prepare_ci_shared_db.py`;
- `scripts/validate_pilot_runtime_evidence.py`;
- pilot runtime-evidence template;
- active AWS/deployment/security/API governance;
- migration graph/current-head tests.

Historical documents remain historical.

## Qualification contract

The exact PR head must prove:

- one Alembic head;
- dedicated GLOBAL permission and no role-derived authority;
- PostgreSQL single-ACTIVE enforcement;
- published immutability;
- two-reviewer separation and review-digest invalidation;
- deterministic JCS manifest/digest;
- KMS signing contract and test-signer isolation;
- fail-closed runtime lookup;
- append-only emergency DENY/CLEAR;
- governance idempotency and audit;
- concurrency behavior;
- current migration-head consistency;
- Backend CI with zero skips;
- Clamd integration;
- frontend/Next/workspace/Android/iOS non-regression;
- Vercel under repository policy.

## Explicit non-scope

This slice contains no licensed NRCeS/SNOMED package, no scraped CDSCO list, no real medication allowlist and no production ACTIVE release. Tests use synthetic concepts only.

It does not create:

- Prescription;
- PrescriptionItem;
- WRITE_PRESCRIPTION API/service;
- prescription Timeline write;
- FHIR MedicationRequest export;
- active-medication projection;
- prescribing frontend;
- patient prescription UI.

After merge, STOP. The next separately authorized Task-0 slice is:

**10B.5l — First Qualified Medication Catalog Release**

Until that real release is independently reviewed, signed, activated and qualified:

`WRITE_PRESCRIPTION PERSISTENCE BLOCKED`.
