# Nexa Care Identity Evidence Disclosure Policy

[Repository agent contract](../../AGENTS.md) · [Security non-regression standard](SECURITY_NON_REGRESSION.md) · [India regulatory baseline](INDIA_REGULATORY_BASELINE.md) · [Engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md)

Status: Engineering security/privacy boundary
Human privacy/legal approval required before disclosure expansion
Owner: Nexa Care engineering leadership
Security/privacy/clinical reviewer: Human owner required
Last reviewed: 2026-08-11

This document defines the current engineering disclosure boundary.
It is not a legal or regulatory compliance determination. It does not assert compliance with DPDP,
ABDM, CDSCO, hospital privacy obligations, or any other legal or regulatory
framework. Human privacy, legal, security, and clinical review remain required
before identity-evidence disclosure expands.

## 1. Current policy boundary

```text
CURRENT_IDENTITY_REVIEW_DISCLOSURE_POLICY = E0_METADATA_ONLY
```

Phase 1 is `identity-review/1.0`: metadata-only, non-release identity review.
The current `identity_reviewer` may receive only existing case metadata:

- case status and closed identity reason codes;
- opaque patient, job, document, and case identifiers;
- route count and review-assignment state;
- versions and timestamps; and
- contract and policy versions.

No current policy authorizes additional identity evidence.

## 2. Evidence classes

### E0 — Identity-review metadata

Examples are case status, aggregate identity reason, opaque identifiers,
timestamps, route count, assignment state, and version metadata.

```text
AUTHORIZED_NOW
```

### E1 — Aggregate value-free comparison summary

Examples are evaluated-field count, exact count, missing count, nonmatching
count, conflicting count, and aggregate identity state.

```text
NOT_AUTHORIZED_FOR_RUNTIME_DISCLOSURE_YET
CANDIDATE_FOR_FUTURE_POLICY_V2
```

E1 is the only currently identified candidate for a future disclosure
expansion. E1 must contain no raw, canonical, masked, normalized, or hashed
identity value, source text, or document content.

### E2 — Field-specific value-free statuses

Examples include:

```text
patient_name -> NONMATCHING
phone -> EXACT
aadhaar_abha_id -> MISSING
```

```text
NOT_AUTHORIZED
REQUIRES_SEPARATE_PRIVACY_POLICY_DECISION
E2_DISCLOSURE = NOT_AUTHORIZED
```

E2 reveals which personal-identity dimensions are present or inconsistent and
is therefore more disclosive than E1.

### E3 — Masked identity values

```text
PROHIBITED_UNDER_CURRENT_POLICY
E3_MASKED_IDENTITY = PROHIBITED_UNDER_CURRENT_POLICY
```

Masked values remain personal data, can enable guessing, and can expose
another person's identifier fragments.

### E4 — Raw OCR identity assertions

```text
PROHIBITED_UNDER_CURRENT_POLICY
E4_RAW_OCR_IDENTITY = PROHIBITED_UNDER_CURRENT_POLICY
```

This includes OCR name, phone, ABHA/Aadhaar, normalized identity text, source
text, page, bounding box, confidence, and provider provenance when associated
with an identity assertion.

### E5 — Canonical stored identity values

```text
PROHIBITED_FOR_IDENTITY_REVIEWER
E5_CANONICAL_IDENTITY = PROHIBITED_FOR_IDENTITY_REVIEWER
```

Showing canonical and OCR values side by side would turn visual judgment into
an identity-authority workflow.

### E6 — Original source document

```text
PROHIBITED_UNDER_CURRENT_IDENTITY_REVIEW_POLICY
E6_SOURCE_DOCUMENT = PROHIBITED_UNDER_CURRENT_IDENTITY_REVIEW_POLICY
```

A quarantined source may belong to the bound patient, another patient, or
another person entirely.

## 3. Why E0 is sufficient

Current Phase-1 outcomes are:

```text
REJECTED_FOR_BOUND_PATIENT
VERIFIED_IDENTITY_REQUIRED
SECURITY_ESCALATION_REQUIRED
INSUFFICIENT_IDENTITY_EVIDENCE
```

None of these outcomes confirms identity, releases quarantine, rebinds a
patient, creates `SOURCE_ONLY`, creates clinical truth, or performs clinical
commit. E0 metadata is sufficient for current Phase-1 authority. E1 through
E6 are not operationally necessary for the current non-release workflow.

## 4. No-disclosure invariants

Identity-review runtime must not return:

- `patient_name`, `phone`, `aadhaar`, `abha`, or `aadhaar_abha_id`;
- OCR identity text, normalized identity text, or canonical identity values;
- masked identity values, identity-source text, or document content;
- clinical candidate values or clinical source text;
- document bytes, source URLs, or storage references.

```text
IDENTITY_EVIDENCE_VISIBILITY_CREATES_IDENTITY_AUTHORITY = NEVER
```

Identity evidence visibility never creates identity authority. No reviewer-visible
evidence may by itself authorize:

```text
CONFIRM_BOUND_PATIENT
REASSIGN_PATIENT
RELEASE_FROM_QUARANTINE
SOURCE_ONLY
READY_FOR_COMMIT
CLINICAL_COMMIT
```

Verified identity resolution is a separate future security milestone.

## 5. Authorization and consent boundaries

`DocumentProcessingOperation` is not an acceptable location for
identity-review disclosure authority while generic document grants resolve the
whole operation set. Future summary, assertion, and source operations must
not be silently added to the generic document-processing grant; each requires
dedicated policy semantics.

```text
identity-review/1.0 = metadata-only non-release identity review
```

Future disclosure requires a new explicitly versioned policy, expected to be
`identity-review/2.0` or later. The semantics of `identity-review/1.0` must
not be broadened in place.

```text
IDENTITY_EVIDENCE_CONSENT_SCOPE = NOT_EXPRESSIVE_ENOUGH
```

Current generic document-processing consent does not express a sufficiently
specific patient decision for an independent reviewer to inspect raw OCR,
canonical identity, masked identity, or the original source document. Consent
runtime behavior is unchanged by this policy lock.

## 6. Future E1 gate

```text
E1_RUNTIME_IMPLEMENTATION = NOT_AUTHORIZED_BY_THIS_POLICY_LOCK
```

Before E1 implementation, a human privacy/legal owner must approve purpose,
scope, retention, erasure behavior, patient-facing access-history treatment,
review-session conditions, and audit semantics. A truly value-free E1 record
does not require raw-identity encryption-ownership resolution, but must be
verified to contain no raw, canonical, masked, normalized, or hashed identity,
source text, or document content.

The next possible engineering gate is:

```text
IDENTITY_EVIDENCE_SAFE_SUMMARY_PHASE_1
```

If approved, it is limited to future jobs, aggregate E1 only, no E2
field-specific statuses, no raw or masked identity, no canonical identity, no
source document, no re-OCR, no backfill, and no release authority.

## 7. Raw identity, hashes, historical cases, and reprocessing

```text
RAW_IDENTITY_ASSERTION_PERSISTENCE = NOT_AUTHORIZED
DO_NOT_PERSIST_UNKEYED_IDENTITY_VALUE_HASHES
AUTOMATIC_IDENTITY_REEXTRACTION_FOR_REVIEW = PROHIBITED
```

This prohibits OCR name, phone, ABHA, canonical, normalized, and masked values,
as well as `SHA256(phone)`, `SHA256(name)`, and `SHA256(ABHA)` as identity
storage. A hash is not anonymization merely because the raw value is omitted.

Existing identity-review cases created without future summary persistence
cannot be backfilled. A future summary endpoint must return
`IDENTITY_EVIDENCE_SUMMARY_UNAVAILABLE` or an equivalent stable value-free
state for those cases. Backfill must not use `NexaVault`, clinical candidates,
filenames, uploader/provider identity, document metadata, AI inference, or
benchmark expected values.

Missing historical evidence must not trigger Textract, another OCR provider,
LLM extraction, or background reprocessing. Any future reprocessing requires a
separate operation, fresh authorization, current consent, current erasure
check, versioned provider/model/query provenance, and explicit audit.

## 8. Source and patient-ownership boundaries

```text
IDENTITY_REVIEW_SOURCE_ACCESS = PROHIBITED_UNDER_POLICY_V1
```

The ordinary `GET /api/v2/pipeline/jobs/{job_id}/document` endpoint and
`assert_job_authorization_binding(...)` must not be relaxed or bypassed for
`identity_reviewer`. Future source access, if approved, requires a separate
authorization boundary.

`IDENTITY_MISMATCH` and `IDENTITY_CONFLICTING` require conservative treatment
as potentially:

```text
CROSS_PATIENT_SENSITIVE_SOURCE
```

This does not identify another patient; it means ownership is unresolved and
disclosure must be contained. `IDENTITY_UNAVAILABLE` does not establish
wrong-patient ownership and does not broaden source or identity visibility.

```text
IDENTITY_REVIEW_PATIENT_LOOKUP_AUTHORITY = NOT_GRANTED
```

Identity-evidence disclosure authority and patient-search/patient-lookup
authority are separate capabilities. Under policy v1, an `identity_reviewer`
must not use OCR name, OCR phone, OCR ABHA, masked identity, canonical identity,
or document metadata to search another patient, enumerate candidates, find a
likely patient, rank patient matches, or resolve an alternate account.
`CROSS_PATIENT_SENSITIVE_SOURCE` does not mean Nexa Care knows or may search
for the alternate patient. No `SEARCH_PATIENT`, `LOOKUP_PATIENT`, `FIND_MATCH`,
or reassignment/rebinding/ownership-change authority is created.

`SECURITY_ESCALATION_REQUIRED` does not automatically expand reviewer
visibility. A future security/privacy incident workflow may have separate
authority.

## 9. Future session, terminal, separation, and audit policy

Future E1-or-greater disclosure must require a claimed case, current assigned
reviewer, current literal `identity_reviewer` role, valid current review
session binding, dedicated capability, same tenant, clear erasure state, and a
future approved policy. The default status policy is:

```text
PENDING                  -> no evidence view
IN_REVIEW + current assigned reviewer + current literal identity_reviewer role
                         + valid session + dedicated capability + same tenant
                         + erasure clear -> potentially eligible under future policy
RESOLVED_NO_RELEASE      -> deny evidence view
ESCALATED                -> deny ordinary identity-review evidence view
```

Future disclosure must preserve:

```text
reviewer != uploader
reviewer != original authorization provider
```

Candidate future audit events are `IDENTITY_EVIDENCE_SUMMARY_VIEWED`,
`IDENTITY_ASSERTION_EVIDENCE_VIEWED`, `IDENTITY_SOURCE_VIEWED`, and
`IDENTITY_EVIDENCE_ACCESS_DENIED`. They are not registered in runtime code by
this policy lock. Audit metadata must never contain disclosed values.

Sensitive identity/source disclosure must fail closed if required audit
durability cannot be established before disclosure. Audit code is unchanged.

Access-history treatment is separate from internal immutable audit:

```text
E0 patient-facing access history = current Phase-1 policy applies
E1 patient-facing access history = POLICY DECISION REQUIRED
E2/E3/E4/E5/E6 = POLICY DECISION REQUIRED if ever authorized
```

## 10. Retention, erasure, and encryption ownership

```text
IDENTITY_EVIDENCE_RETENTION_POLICY = UNDEFINED
IDENTITY_EVIDENCE_ENCRYPTION_OWNERSHIP = UNRESOLVED_FOR_RAW_IDENTITY_EVIDENCE
```

No retention duration is invented. Future E1 persistence requires an approved
lifecycle and erasure rule. Raw identity persistence cannot proceed until the
wrong-patient encryption question is resolved: a document bound to patient A
may suggest another person while its assertion would otherwise be encrypted
under patient A's key.

## 11. Verified identifiers and legal gate

```text
VERIFIED_ABHA_MRN_SOURCE_VIEW_AUTHORITY = NOT_GRANTED
VERIFIED_ABHA_MRN_RELEASE_AUTHORITY = NOT_GRANTED
VERIFIED_ABHA_MRN_PATIENT_REASSIGNMENT_AUTHORITY = NOT_GRANTED
```

Verified ABHA/MRN is not automatic source-view authority, automatic quarantine
release authority, or automatic patient reassignment authority. Verified
identifier integration may support a later identity-resolution workflow but
does not itself create `CONFIRM_BOUND_PATIENT`, `RELEASE_FROM_QUARANTINE`,
`SOURCE_ONLY`, or `READY_FOR_COMMIT` authority.

This policy is not legal advice or a compliance determination. Human privacy,
legal, security, and clinical approval is required before disclosure expands.

## 12. Governance status and next gate

SEC-034 remains security-invariant unchanged. Its evidence status must
distinguish locally qualified disposable PostgreSQL migration/concurrency and
rollback evidence from still-deferred deployment, production, live provider,
verified ABHA/MRN, source disclosure, OCR identity display, quarantine release,
patient correction, incident workflow, and frontend qualification.

The recommended next action is:

```text
AUDIT_IDENTITY_EVIDENCE_DISCLOSURE_POLICY_V1
```

## 13. Maintenance

Update procedure: follow the governance change procedure in `AGENTS.md`.
Disclosure expansion requires explicit security, privacy/legal, and clinical
review, focused enforcement tests, and a truthful validation report. This
policy does not authorize runtime expansion by documentation alone.

[Repository agent contract](../../AGENTS.md) · [Security non-regression standard](SECURITY_NON_REGRESSION.md) · [India regulatory baseline](INDIA_REGULATORY_BASELINE.md) · [Engineering constitution](NEXA_CARE_ENGINEERING_CONSTITUTION.md)
