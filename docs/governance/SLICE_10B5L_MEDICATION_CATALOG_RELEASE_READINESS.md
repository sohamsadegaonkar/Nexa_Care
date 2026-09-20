# Slice 10B.5l — First Qualified Medication Catalog Release Readiness

**Status:** PACKAGE-INDEPENDENT READINESS IMPLEMENTED / REAL RELEASE EXTERNALLY AND HUMAN BLOCKED  
**Authoritative base:** `1b59751f02a4ea593f5e2915a38dccf646a200d5`  
**Alembic head:** `20260919_medication_catalog`  
**Prescription boundary:** `WRITE_PRESCRIPTION PERSISTENCE BLOCKED`

This slice advances only internal prerequisites that can be completed without a
licensed terminology package, real medication classification, real AWS signing
authority, live catalog database mutation, or fabricated human review.

It does not create or activate a real medication entry.

## Applicable repository invariants

The change preserves:

- one Alembic head and no migration change;
- catalog authority remains GLOBAL
  `MEDICATION_CATALOG_RELEASE_REVIEW`;
- roles, affiliations, `PRESCRIBE_MEDICATION`,
  `PROFESSIONAL_REVIEW`, `PRESCRIBING_ELIGIBILITY_REVIEW`, and
  `TRUST_PERMISSION_MANAGE` do not create catalog release authority;
- unknown/restricted medication facts deny;
- positive candidates require all closed v1 classifications and all required
  evidence;
- positive candidates require two independent exact-digest reviewers, both
  different from the preparer and from each other;
- first real release positive count is 1..20;
- production signing remains dedicated AWS KMS `SIGN_VERIFY`,
  `ECC_NIST_P256`, `ECDSA_SHA_256`;
- no local production signing key fallback;
- runtime release authority remains signed, immutable and fail closed;
- no Prescription persistence or clinical issuance functionality is enabled.

## Mandatory governance review

This slice was reviewed against:

- `AGENTS.md`;
- `SECURITY_NON_REGRESSION.md`;
- `INDIA_REGULATORY_BASELINE.md`;
- `IDENTITY_EVIDENCE_DISCLOSURE_POLICY.md`;
- `NEXA_CARE_ENGINEERING_CONSTITUTION.md`;
- 10B.5i medication/persistence contract;
- 10B.5j catalog contract;
- 10B.5k catalog infrastructure contract.

The Identity Evidence Disclosure Policy is unaffected because medication catalog
reference/evidence data contains no patient identity-review disclosure and this
slice creates no patient lookup/disclosure capability.

## Baseline required-check state

At the authoritative base `1b59751f02a4ea593f5e2915a38dccf646a200d5`,
the previously failing Frontend CI push run `35522676987` was successfully
rerun. The authoritative main baseline is now green:

- CI: SUCCESS;
- Frontend CI: SUCCESS;
- Clamd Integration: SUCCESS;
- Vercel: SUCCESS.

The medication-catalog readiness branch does not claim to have fixed the prior
frontend failure and contains no frontend change.

## 10B.5l readiness matrix

### READY before this slice

- catalog release/entry/evidence/emergency schema;
- one-ACTIVE PostgreSQL invariant;
- published immutability;
- closed classification policy;
- 20-positive ceiling at qualification;
- digest-bound two-reviewer separation;
- RFC 8785/JCS manifest;
- SHA-256 release digest;
- dedicated KMS signing provider;
- production test-signer prohibition;
- create/edit/review/qualify/activate/revoke application services;
- durable catalog mutation idempotency/audit;
- fail-closed runtime lookup;
- emergency DENY/CLEAR overlay;
- migration-head/deployment contract at
  `20260919_medication_catalog`.

### MISSING INTERNAL implementation found by audit

The catalog services existed but there was no package-independent first-release
readiness boundary that could prove, before a real mutation:

- exact terminology package version + SHA-256 provenance;
- local package byte-digest agreement without copying the package;
- positive-entry IDENTITY evidence binding to that exact package digest;
- first-release requirement of at least one positive entry;
- independent read-only release projection/digest/signature readiness;
- emergency-deny compatibility;
- one-ACTIVE consistency;
- sanitized first-release activation-readiness reporting;
- a dedicated first-release operator runbook.

This slice implements those read-only/readiness gaps.

### Remaining MISSING INTERNAL boundary

There is still no medication-catalog **mutating** CLI that safely derives
`TrustManagementAuthentication` from existing trusted provider-session state.

That absence is intentional rather than bypassed here.

A future operational entrypoint, if required, must reuse trusted provider
session/MFA state. It must not accept caller-invented actor IDs or MFA timestamps
as authority and must still invoke `MedicationCatalogApplicationService`.

Until that session-bound mutation path is approved/available in the target
runtime, do not use psql, ad-hoc Python, or a synthetic authentication object to
create/review/qualify/activate a real release.

### EXTERNAL blockers

Real 10B.5l execution still requires:

- active organizational NRCeS/SNOMED Affiliate Licence / approved usage basis;
- access to the exact licensed CDCI/India Drug Extension package;
- exact package version/date and local package SHA-256;
- authoritative current regulatory evidence;
- authorized target PostgreSQL catalog runtime;
- real dedicated medication-catalog KMS signing key;
- exact-key IAM limited to `kms:DescribeKey`, `kms:Sign`, `kms:Verify`.

No licensed package or AWS resource is available merely because repository code
supports it.

### HUMAN REVIEW blockers

A real release additionally requires actual organizational identities for:

- preparer;
- reviewer 1;
- reviewer 2;
- qualifier;
- activator.

Every governed actor must have current GLOBAL
`MEDICATION_CATALOG_RELEASE_REVIEW` authority and fresh required session/MFA
assurance.

Medication classifications and licence standing require real human
clinical/regulatory/governance review. Tests cannot substitute for those
approvals.

## Current terminology public-source checkpoint

At execution time NRCeS publicly lists a Common Drug Codes for India release
dated **2026-08-31**, including the terminology-integrated package / India Drug
Extension.

Official references:

- https://www.nrces.in/news
- https://www.nrces.in/services/national-releases
- https://www.nrces.in/faqs

NRCeS states that SNOMED CT usage in India requires an Affiliate Licence and
that licensed code/package access is provided through MLDS.

These public facts do not prove Nexa's organizational licence status and do not
prove that the protected package is present in this execution environment.

Therefore:

`10B.5l REAL TERMINOLOGY INGESTION = EXTERNALLY BLOCKED`

## Source metadata contract

The new strict metadata contract is:

`nexa-medication-catalog-source-metadata/v1`

Allowed fields only:

- schema;
- terminology authority;
- package name;
- package version;
- package release date;
- package SHA-256;
- official source reference;
- opaque licence-governance reference;
- checked timestamp.

Unknown fields are rejected.

The metadata contract intentionally cannot carry:

- RF2 rows;
- licensed terminology content;
- medication entries;
- medicine lists;
- credentials;
- licence text.

The package itself remains outside Git.

## Exact package binding

For every proposed positive entry, its IDENTITY evidence must bind both:

- the exact declared package version;
- the exact declared package SHA-256.

The IDENTITY evidence authority must be NRCeS or
`SNOMED_IDENTITY_ONLY`.

This makes terminology package provenance part of the reviewed candidate digest
and therefore part of the qualified release manifest.

A package digest proves exact source bytes; it does not by itself prove legal
licensing or medication safety.

## Read-only readiness checker

`scripts/check_medication_catalog_release_readiness.py` is read-only.

It can:

1. validate source metadata;
2. locally stream and SHA-256 the protected terminology package;
3. verify repository/database Alembic head agreement;
4. inspect a release projection;
5. recompute current policy decisions;
6. verify positive count 1..20;
7. verify review/policy consistency;
8. verify exact terminology package binding;
9. verify published manifest/projection digest agreement;
10. optionally call the existing production KMS provider for read-only
    `DescribeKey` readiness and stored-signature `Verify`;
11. check emergency-deny compatibility;
12. report ACTIVE release count/uniqueness.

It never:

- calls `qualify_release`;
- calls `activate_release`;
- signs a new release;
- calls KMS `Sign`;
- inserts/updates/deletes catalog rows;
- changes AWS resources;
- emits medication names/displays/evidence source text in its report.

### Database TLS contract

Database-backed readiness uses the same server-owned transport contract as the
application engine and Alembic. The CLI constructs a `DatabaseConfig` and
passes it to `build_database_connect_args(...)`; it does not construct its own
`SSLContext`.

For `pilot`, `staging`, `preview`, and `production`:

- `DATABASE_SSL_CA_PATH` is mandatory;
- a missing CA path fails closed with `DATABASE_TLS_CA_REQUIRED`;
- a missing/corrupt CA or another shared TLS configuration error fails closed
  with `DATABASE_TLS_CONFIGURATION_INVALID`;
- the resulting context remains `CERT_REQUIRED` with hostname checking enabled;
- URL query parameters `ssl`, `sslmode`, `sslrootcert`, `sslcert`, and
  `sslkey` are rejected by the shared helper;
- no unverified fallback is permitted.

`--source-only` returns before database configuration is evaluated and remains
usable for protected local package hashing/metadata validation without a DB CA.

## First-release stricter gate

The general catalog infrastructure can represent a release with zero positive
entries.

The **first qualified production release** must not activate merely to claim
completion.

10B.5l readiness therefore requires:

```text
1 <= candidate_positive_count <= 20
```

Every positive candidate must pass the exact existing closed policy, independent
reviews, package binding, and emergency-deny checks.

## KMS boundary

This slice does not call AWS.

The optional future `--live-kms-preflight` path reuses the existing production
signer. It can only describe key readiness and verify an already persisted
signature.

Actual release signing remains owned by the real `qualify_release` operation.

No additional IAM permission is introduced.

## Real medication entries

`REAL MEDICATION ENTRIES CREATED = NO`

All new tests use synthetic concepts only.

No CDCI package, SNOMED distribution, real concept ID, drug list, classification
or real allowlist is committed.

## Prescription boundary

Still prohibited:

- Prescription;
- PrescriptionItem;
- WRITE_PRESCRIPTION persistence;
- `treatment.write_prescription.v1`;
- medication Timeline writes;
- active-medication projection;
- FHIR MedicationRequest;
- provider prescribing UI;
- patient prescription UI.

`WRITE_PRESCRIPTION PERSISTENCE BLOCKED`

## Qualification requirements for this readiness patch

Changed behavior must be covered by:

- source metadata strictness;
- package SHA-256 verification and mismatch denial;
- safe positive DRAFT readiness;
- H/UNKNOWN denial;
- missing review denial;
- exact package-digest IDENTITY binding;
- published manifest/signature agreement;
- emergency DENY compatibility;
- tamper/projection denial;
- multiple-ACTIVE denial;
- source-only CLI no-mutation evidence;
- source-only operation without DB TLS configuration;
- pilot/staging/preview/production explicit CA requirement;
- valid CA propagation through shared `build_database_connect_args(...)`;
- `CERT_REQUIRED` and hostname-checking verification;
- conflicting DB URL TLS parameter rejection;
- invalid CA failure;
- package-content non-disclosure.

The existing medication-catalog policy, manifest, signing, migration and
PostgreSQL suites remain required non-regression coverage.

## Next Task-0 approval gate

Do not merge this readiness branch until:

1. authoritative main remains green;
2. the Task-0 readiness PR exact head is fully qualified after the database TLS
   correction;
3. an explicit merge authorization is given.

After merge, the next real 10B.5l execution gate requires the external and human
prerequisites above.

Only after a real catalog release is independently prepared, reviewed, signed,
qualified, activated, and runtime-qualified may an orchestrator consider the
separate Prescription-readiness review.

This document does not grant that later approval.
