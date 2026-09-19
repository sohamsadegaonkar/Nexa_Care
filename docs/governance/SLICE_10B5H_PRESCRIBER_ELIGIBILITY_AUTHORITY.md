# Slice 10B.5h — Prescriber Eligibility Authority Implementation

## Status

Implementation branch:

```text
task0/10b5h-prescriber-eligibility-authority
```

This slice implements the prescriber-authority layer approved by Slice 10B.5g.

It does **not** implement canonical Prescription persistence.

## Frozen authority separation

The implementation preserves:

```text
WRITE_PRESCRIPTION != professional entitlement to prescribe
provider authentication != professional verification
professional verification != prescribing entitlement
patient consent != prescribing entitlement
facility affiliation != prescribing entitlement
Medication != canonical Prescription
Prescription issuance != active medication state
```

`PRESCRIBE_MEDICATION` is deliberately excluded from legacy role-derived
clinical capabilities. A `clinician` affiliation role cannot create
prescribing authority.

## Durable authority object

`PrescribingEligibilityDecision` is a separate append-only, versioned,
provider-bound and ProfessionalVerification-bound authority record.

The decision stores only bounded structural facts:

- provider and exact ProfessionalVerification identity/version;
- closed lifecycle state;
- closed practitioner class;
- controlled source class;
- snapshots of registration authority/number;
- bounded source reference;
- SHA-256 evidence binding;
- server-owned checked time and validity;
- separately authorized reviewer;
- closed decision/restriction codes;
- policy version;
- append-only predecessor lineage.

PostgreSQL prevents UPDATE and DELETE through an immutable trigger. Provider
version and predecessor uniqueness plus provider-row/professional-row locking
serialize concurrent reviews.

## Supported positive class

Only:

```text
FULL_RMP_MODERN_MEDICINE
```

may be positive, and only with a source class of:

- NMR;
- SMR; or
- competent medical council.

HPR cannot produce an `ELIGIBLE` decision.

The controlled lifecycle is:

- PENDING;
- ELIGIBLE;
- RECHECK_DUE;
- RESTRICTED;
- SUSPENDED;
- REVOKED;
- EXPIRED;
- SOURCE_UNAVAILABLE.

Only a currently valid `ELIGIBLE` row contributes prescribing authority.

## Validity policy

Human-attested positive eligibility is server-bounded to at most 30 days.

The server clips the positive decision earlier when required by current
ProfessionalVerification boundaries, including registration validity and
next-review time. The caller cannot supply `valid_until`.

A stored `ELIGIBLE` row whose validity has expired is denied without mutating
history.

General ProfessionalVerification recheck grace does not create prescribing
grace: prescribing requires current `VERIFIED` professional trust.

## Professional identity binding

A positive decision must remain bound to the exact current:

- provider;
- ProfessionalVerification row;
- ProfessionalVerification version;
- registration authority code;
- normalized registration number.

Professional suspension, revocation, rejection, expiry, staleness, registration
expiry, adverse signal, identity mismatch, or review expiry immediately prevents
current prescribing authority even when an older eligibility decision remains
historically `ELIGIBLE`.

## Reviewer authority

The slice adds the dedicated GLOBAL organizational permission:

```text
PRESCRIBING_ELIGIBILITY_REVIEW
```

It is separate from affiliation roles and from generic
`PROFESSIONAL_REVIEW`.

Self-review is prohibited. The reviewer workflow requires:

- authenticated provider session;
- active reviewer account and credential;
- contact assurance;
- MFA enrollment and current session assurance;
- fresh MFA for the high-risk mutation;
- current GLOBAL prescribing-review permission;
- exact target provider;
- exact expected ProfessionalVerification version;
- exact expected prior decision version;
- bounded closed request;
- durable idempotency;
- one database transaction;
- structural audit outbox event.

## Evidence contract

The human-attestation mutation requires:

- controlled source type;
- bounded source reference;
- lowercase 64-character SHA-256 evidence binding;
- exact current registration authority/number from ProfessionalVerification.

Raw registry pages, credentials, tokens and unrestricted source payloads are not
persisted in the decision or audit event.

Audit metadata is structural and contains no patient or medication data.

## Clinical capability derivation

`ClinicalCapability.PRESCRIBE_MEDICATION` exists as a typed server capability
but is **not role-derived**.

For this capability, `ClinicalEligibilityService` still requires all normal
provider/facility/affiliation/contact/session/MFA trust, and additionally
requires the latest prescribing decision to pass the prescribing-specific
current-state policy.

Other clinical capabilities retain their existing role-derived behavior.

## Treatment Session integration

When a Treatment Session requests `WRITE_PRESCRIPTION`:

1. request creation requires current `PRESCRIBE_MEDICATION` before consuming
   the patient discovery handle;
2. signed **approval** revalidates current provider trust and prescribing
   eligibility;
3. claim revalidates it again before minting durable/live Treatment Session
   authority;
4. the reusable clinical-session mutation gate maps `WRITE_PRESCRIPTION` to
   `PRESCRIBE_MEDICATION` and revalidates current eligibility while locking
   the ProfessionalVerification row.

A patient signed **denial** grants no provider authority and therefore remains
recordable even if provider eligibility changed after the challenge was issued.

No `WRITE_PRESCRIPTION` mutation route is added in this slice.

## Concurrency and fail-closed behavior

Reviewer decisions lock stable provider/professional authority rows before
selecting the latest immutable decision.

Two reviewers presenting the same expected predecessor cannot both create the
same next authoritative version.

A future prescription mutation must use the current clinical-session gate so a
patient signature or already-claimed session cannot override a newly known
professional suspension/revocation/expiry/restriction.

Authoritative-store failures fail closed.

## Migration

This slice adds:

```text
20260919_prescriber_eligibility
```

with parent:

```text
20260918_treatment_vitals_encounter
```

The migration:

- extends the trust-permission constraint for the dedicated review permission;
- creates only `prescribing_eligibility_decision`;
- creates the append-only trigger;
- performs no data backfill;
- creates no Prescription/PrescriptionItem storage.

The final migration parent/head must be revalidated after the one required
current-main reconciliation.

## Explicit non-scope

This slice creates no:

- Prescription table;
- PrescriptionItem table;
- medication write route;
- provider prescribing form;
- drug/formulary authorization engine;
- automatic NMR/SMR scraping or connector;
- automatic HPR-based prescribing grant.

## Qualification requirements

Final exact-head qualification must include:

- one linear Alembic head and disposable PostgreSQL upgrade;
- append-only UPDATE/DELETE rejection;
- concurrent-review race behavior;
- idempotent replay/conflict behavior;
- rollback/audit atomicity;
- self-review and missing-permission denial;
- HPR-positive denial;
- 30-day/earlier-bound validity;
- ProfessionalVerification/adverse-state invalidation;
- role-derived-capability non-regression;
- Treatment Session request, approval, claim and future mutation-boundary
  revalidation;
- existing Backend CI Partitions A/B/C with zero skips/failures;
- frontend/native non-regression.

## Release boundary

Completion of 10B.5h means Nexa can answer, server-side and durably:

> Is this provider currently professionally eligible for the bounded Nexa
> prescribing capability?

It does not yet mean Nexa accepts a Prescription mutation.

Canonical Prescription persistence remains a separate later authorization.
