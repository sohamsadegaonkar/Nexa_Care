# Slice 10B — Bounded Clinical Access Session

Status: **OPEN — BACKEND SECURITY FOUNDATION STARTED**

Authoritative baseline before this slice started:
`f68bad3d157e7dcdf7716a9bf0b74fc0b7991f25` on `main`.

This label is the implementation name for the next repository-roadmap step after
Slice 10A: the bounded clinical treatment/access-session layer.

## Core authority separation

The slice must preserve this separation end to end:

```text
patient discovery
!= patient authentication
!= signed patient consent
!= bounded clinical access session
!= encounter
!= clinical record
!= provider authentication
!= provider clinical eligibility
```

A patient match, consent request, signed approval, patient UUID, provider role,
legacy consent token, or client-side route state must not independently grant a
clinical operation.

## Existing secure foundation

The repository already has important primitives that this slice must reuse
rather than bypass:

- Signed Consent V3 consumes a server-bound discovery handle;
- patient approval is cryptographically signed by an active enrolled device key;
- provider professional/facility/affiliation/capability trust is re-evaluated;
- the provider makes a one-time approved-access claim;
- the live Redis capability is patient/provider/hospital/request/expiry bound;
- durable consent-grant evidence stores only a hash of the bearer token;
- patient revocation invalidates live approved-access capability;
- current record reads are separately capability- and consent-gated.

## Security correction introduced by this slice

The product model requires a first-class bounded clinical session with an
explicit, closed operation vocabulary.  The server-owned vocabulary is:

```text
READ_CLINICAL_HISTORY
READ_DOCUMENTS
CREATE_ENCOUNTER
WRITE_PRESCRIPTION
WRITE_DIAGNOSIS
WRITE_VITALS
WRITE_CLINICAL_NOTES
ORDER_INVESTIGATION
```

However, **existing Signed Consent V3 does not sign an explicit write-operation
set**.  Therefore Slice 10B must not silently reinterpret an existing V3
`clinical`/`full` approval as permission to write clinical data.

The first implementation increment consequently maps current routine V3
approvals to `READ_CLINICAL_HISTORY` only.  Write operations are vocabulary only
until a patient-signed context explicitly binds them.  The separate
`document_processing`/`documents` grant remains outside the treatment-session
contract.

## Provider-session binding

A clinical session must be bound to the exact authenticated provider session,
not merely the stable provider identity and hospital.  The raw provider session
binding must never be persisted or logged.  Only a one-way hash may be retained,
and validation must compare the live session binding in constant time.

A provider re-login/new authenticated session must not automatically inherit a
previous bearer capability merely because the provider UUID and hospital match.

## Session lifecycle target

The authoritative session model must ultimately contain at least:

```text
session_id
patient_id
provider_id
hospital_id
consent_request_id
purpose
scope
allowed_operations
provider_session_binding_hash
issued_at
expires_at
status / revocation state
encounter_id (once bound)
policy_version
```

The session must be:

- server-created;
- patient-bound;
- provider-bound;
- hospital/organization-bound;
- exact-provider-session-bound;
- purpose-bound;
- operation-bound;
- expiring;
- revocable;
- canonical-patient aware;
- erasure/deletion aware;
- auditable;
- fail-closed when the authority store is unavailable or inconsistent.

## Slice sequence

### 10B.1 — authority vocabulary and pure constructor

- closed server-owned operation vocabulary;
- fail-closed Signed Consent V3 -> bounded read-session mapping;
- exact provider-session binding hash primitive;
- no raw session-binding persistence;
- no write-authority escalation;
- adversarial unit qualification.

### 10B.2 — live capability binding

- bind the one-time V3 claim to an opaque `session_id`;
- bind the live capability to the exact provider session;
- require the same provider session on every routine read validation;
- preserve one-time claim and existing patient/provider/hospital/expiry checks;
- preserve document-processing authority as a distinct grant type.

### 10B.3 — durable session lifecycle

- add one linear Alembic migration and a durable session row;
- persist token hash/reference only, never a bearer token;
- transactional issuance/audit evidence;
- patient revocation, provider trust loss, canonical merge, deletion, erasure,
  and expiry terminate authority fail closed;
- define idempotent/atomic revocation behavior.

### 10B.4 — signed write-authority protocol

Do not add writes to existing Signed Consent V3 bytes in place.

Before `CREATE_ENCOUNTER` or any `WRITE_*` operation can be issued, the patient
must sign a versioned context that explicitly includes the exact operation set
(or an equivalently precise server-owned treatment-session policy digest).  The
protocol must prevent operation widening after signature.

### 10B.5 — clinical gates and encounter binding

- central `require_clinical_session(operation)` gate;
- no client-selected patient UUID as an independent authority source;
- encounter creation binds the encounter to the session's canonical patient,
  provider and organization;
- subsequent structured writes require both the operation and matching
  encounter/session binding.

### 10B.6 — qualification

Backend release gates must include:

- pure security/unit tests;
- PostgreSQL lifecycle/migration tests with zero skips;
- PostgreSQL + Redis concurrency/replay tests with zero skips;
- wrong provider/session/hospital/patient tests;
- expiry/revocation/merge/deletion/erasure tests;
- duplicate/concurrent claim tests;
- audit-failure rollback tests;
- migration graph single-head assertion.

Frontend work is not the first step.  Any later UI integration must preserve the
bounded session in memory/approved state and must not place bearer authority or
patient identifiers in URLs.

## Out of scope for the opening increment

The opening 10B.1 work does **not**:

- expose a new endpoint;
- create a new bearer token;
- mint any write operation;
- create encounters;
- change prescription/diagnosis/vitals/note persistence;
- weaken document-processing consent;
- modify physical-device assurance claims;
- claim the overall product is fully secure.

The next concrete engineering step after 10B.1 qualification is wiring the
existing one-time V3 approved-access claim into this session authority without
widening permissions.
