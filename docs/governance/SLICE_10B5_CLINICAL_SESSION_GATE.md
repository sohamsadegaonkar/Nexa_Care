# Slice 10B.5a — Central Treatment Session Gate and Encounter Correlation

Status: **QUALIFIED AND MERGED INTO MAIN**

Parent qualified claim/mint tree:
`f30a9b47ee6db331d22aba0dbd5862134be282a3`
(tree `8acae69b0b2bbde011d22c2ca024ef4bf6a50df1`).

Qualified implementation head:
`22799e682602b3bf39fe53174f57c802db6725ca`.

Merged by PR #50 into main at:
`a92033cfc32629584c3ce0b50987a65bc5b10445`.

The former implementation branch is historical after consolidation and must not be reused.

## Purpose

Treatment Session V1 can already mint an opaque, operation-bound authority,
but that authority remains intentionally inert. This slice introduces the
central server-owned validation boundary that future treatment operations must
use before any clinical mutation can be enabled.

The gate is deliberately implemented and qualified before it is connected to
legacy clinical write routes.

## Authority contract

The central dependency is:

```text
require_clinical_session(ClinicalAccessOperation.<OPERATION>)
```

The opaque bearer is transported only through:

```text
X-Treatment-Token
```

The token is never accepted through a URL, route parameter, query string, or
client-selected patient identifier.

The gate requires all of the following to agree:

1. the SHA-256-addressed live Redis Treatment Session V1 capability;
2. the durable PostgreSQL `ClinicalAccessSessionRecord`;
3. the durable `ConsentGrantLog`;
4. the authenticated current provider identity;
5. the authenticated current hospital;
6. the exact current provider-session binding;
7. protocol version `nexa-treatment-session-v1`;
8. scope `treatment`;
9. policy `clinical-access-v1`;
10. purpose;
11. patient, provider, hospital, request and session identifiers;
12. issuance/expiry and active/revocation state;
13. exact normalized patient-signed operation set; and
14. the requested `ClinicalAccessOperation`.

Missing, malformed, expired, revoked, widened, rebound, cross-patient,
cross-provider, cross-hospital, wrong-session, or store-inconsistent authority
fails closed.

Redis unavailability and durable-store unavailability are not converted into
authorization denials or fallback authority; they surface as security-store
unavailability.

## Live provider trust

The FastAPI dependency is composed with the existing server-owned
`require_clinical_capability(ClinicalCapability.RECORD_READ)` provider gate.
The treatment bearer therefore cannot replace provider authentication,
facility/affiliation trust, or the current server-owned clinical capability
boundary.

The treatment session then adds the exact patient-signed operation authority.

## Encounter correlation boundary

The current repository has no canonical clinical `Encounter` table.

Therefore this slice does **not** claim to create a clinical encounter.

The existing nullable `ClinicalAccessSessionRecord.encounter_id` field is used
only to reserve one server-generated UUID correlation under a row lock:

```text
stage_server_encounter_binding(db, authority)
```

Properties:

- requires a gate result specifically qualified for `CREATE_ENCOUNTER`;
- takes no client-supplied encounter identifier;
- generates the UUID server-side;
- is idempotent for an already-bound session;
- writes no clinical encounter payload;
- commits nothing itself; and
- does not authorize any existing patient-record mutation.

A later bounded increment must introduce the canonical encounter transaction
and bind that record to this correlation before structured treatment writes are
enabled.

## Durable-only encounter state

The encounter correlation is durable PostgreSQL session state. It is not
inserted into the minted Redis capability.

Redis remains authoritative for the live bearer/session/operation envelope.
PostgreSQL remains authoritative for the later encounter correlation.

A Redis payload containing an injected `encounter_id` is rejected.

## Explicit non-wiring boundary

At this checkpoint the following existing clinical write routes remain on their
pre-10B.5 authorization path and do not consume `X-Treatment-Token`:

- vitals writes;
- medication/prescription-like writes;
- lab writes;
- allergy writes; and
- document-reference writes.

No diagnosis, clinical-note, investigation-order, or canonical encounter write
is introduced here.

Signed Consent V3 remains read-only and is not widened.

## Database / migration status

No Alembic migration is introduced.

The repository head remains:

```text
20260917_treatment_session_operations
```

The existing `clinical_access_sessions.encounter_id` column is reused as the
reserved correlation field. This slice does not create a second migration head.

## Security invariants affected

This slice changes the treatment-access authorization boundary but does not
change patient data, consent-signature bytes, identity disclosure, document
processing, erasure, emergency access, or clinical truth.

Applicable invariants:

- default deny;
- exact server-derived patient/provider/hospital authority;
- exact provider-session binding;
- bearer secrecy;
- Redis + PostgreSQL fail-closed agreement;
- explicit operation membership;
- expiry and revocation;
- current provider trust;
- no Signed Consent V3 widening;
- no client-selected encounter authority;
- no clinical mutation before independent gate qualification.

## Required adversarial qualification

The exact final target must cover at least:

- correct authority success;
- missing/invalid token;
- operation not signed by the patient;
- provider mismatch;
- hospital mismatch;
- provider-session mismatch;
- patient rebinding;
- Redis/durable operation disagreement;
- revoked durable grant/session;
- expiry;
- injected Redis encounter binding;
- Redis unavailable;
- durable store unavailable;
- server-generated encounter correlation;
- encounter binding idempotency;
- non-`CREATE_ENCOUNTER` binding rejection;
- no client-selected encounter ID; and
- static proof that legacy write routes still do not consume the gate.

Repository release qualification still requires the exact final head to pass
Partition A, PostgreSQL Partition B, and PostgreSQL + Redis Partition C with
all zero-skip assertions.

## Nonclaims

This slice does not claim:

- a canonical Encounter entity exists;
- treatment writes are enabled;
- a prescription is created by Treatment Session V1;
- diagnoses, vitals, notes, labs, allergies, investigations, or documents are
  writable through the treatment token;
- production deployment;
- physical-device qualification;
- legal/regulatory compliance; or
- production/pilot qualification beyond the repository evidence recorded for this slice.
