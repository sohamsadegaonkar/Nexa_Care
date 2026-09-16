# Slice 10B — Bounded Clinical Access Session

Status: **IN PROGRESS — 10B.3 DURABLE READ AUTHORITY IMPLEMENTED; EXACT-HEAD QUALIFICATION RESTARTED**

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

## Current 10B.3 implementation checkpoint — 2026-09-16

The durable read-authority increment now has these repository invariants:

- the current single repository migration head is
  `20260916_clinical_access_sessions`;
- canonical Signed Consent V3 routine `clinical` / `full` claims still map only
  to `READ_CLINICAL_HISTORY`;
- a successful routine V3 claim stages the hashed `ConsentGrantLog` and the
  bounded `ClinicalAccessSession` row in the same PostgreSQL transaction;
- the durable session stores only token/session-binding digests, never the raw
  bearer or raw provider-session binding;
- routine read validation requires the Redis capability, exact live provider
  session binding, matching active durable session, and matching unrevoked,
  unexpired durable consent grant to agree;
- revoking the durable grant therefore denies subsequent access even if stale
  Redis/session material remains, preserving fail-closed authority during the
  remaining lifecycle-metadata reconciliation work;
- document-processing authority remains a separate grant type and does not
  acquire the routine clinical-session contract;
- no `CREATE_ENCOUNTER`, `WRITE_*`, or `ORDER_INVESTIGATION` authority is
  enabled by this checkpoint.

Backend CI run #654 on parent head
`be5a08c0fdb60b2aa511a5e634d526f79f0eb1d5` proved Partition B green but exposed
only two qualification-contract mismatches: the security non-regression current
migration-head marker and the historical Slice-4 private qualification database
head.  Commit `17a6058ce106bd61c530d0d2e2ef4d3dd34c7b30` updates exactly those stale
markers to `20260916_clinical_access_sessions`.  That maintenance commit was
created through a SHA-pinned one-shot branch which deleted itself after the
fast-forward; it is not itself claimed as qualified because workflow-token
pushes do not provide the ordinary exact-head push qualification signal.

The next direct `main` head created by this checkpoint must therefore pass the
full backend A/B/C zero-skip gates and the applicable frontend/deployment checks
before 10B.3 can be called qualified.  Qualification evidence from an older SHA
must not be reused.

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
