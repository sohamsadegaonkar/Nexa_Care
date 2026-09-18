# Slice 10B — Bounded Clinical Access Session

Status: **10B.3 QUALIFIED — 10B.4 SIGNED PROTOCOL QUALIFIED / CLAIM-MINT IMPLEMENTED, QUALIFICATION PENDING**

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
explicit, closed operation vocabulary. The server-owned vocabulary is:

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
set**. Therefore Slice 10B must not silently reinterpret an existing V3
`clinical`/`full` approval as permission to write clinical data.

The current implementation consequently maps routine V3 approvals to
`READ_CLINICAL_HISTORY` only. The separate
`document_processing`/`documents` grant remains outside the treatment-session
contract.

Slice 10B.4 now has a separate `nexa-treatment-session-v1` signature protocol
that cryptographically binds an exact operation set. That protocol is not yet a
clinical-session minting mechanism, and its existence alone does not authorize
any clinical write.

## Provider-session binding

A clinical session is bound to the exact authenticated provider session, not
merely the stable provider identity and hospital. The raw provider session
binding is never persisted or logged. Only a one-way hash is retained, and
validation compares the live session binding in constant time.

A provider re-login/new authenticated session does not automatically inherit a
previous bearer capability merely because the provider UUID and hospital match.

## Session lifecycle target

The authoritative session model contains the bounded authority fields required
for the current read-only phase:

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
encounter_id (reserved for later binding)
policy_version
```

The session is server-created, patient/provider/hospital/exact-session bound,
purpose- and operation-bound, expiring, revocable, audited through the existing
consent boundary, and fail-closed when Redis/PostgreSQL authority disagrees.

## Slice sequence

### 10B.1 — authority vocabulary and pure constructor

Implemented:

- closed server-owned operation vocabulary;
- fail-closed Signed Consent V3 -> bounded read-session mapping;
- exact provider-session binding hash primitive;
- no raw session-binding persistence;
- no write-authority escalation.

### 10B.2 — live capability binding

Implemented:

- one-time V3 routine claim binds an opaque `session_id`;
- live routine capability binds the exact provider session;
- routine reads require the same provider session;
- patient/provider/hospital/request/expiry checks remain mandatory;
- document-processing authority remains a distinct grant type.

### 10B.3 — durable session lifecycle

Implemented and qualified for the current read-only authority:

- linear Alembic migration `20260916_clinical_access_sessions`;
- durable session row stores token/session-binding hashes only;
- routine V3 claim stages `ConsentGrantLog` and `ClinicalAccessSession` in the
  same PostgreSQL transaction;
- routine read validation requires Redis capability + exact provider session +
  durable active session + matching unrevoked/unexpired consent grant;
- patient revocation invalidates Redis capability, revokes the durable grant,
  and explicitly marks the matching durable clinical session
  `REVOKED / PATIENT_REVOKED` in the same database transaction;
- duplicate patient revocation remains idempotent;
- current provider trust remains independently re-evaluated at protected
  clinical boundaries;
- expiry fails closed without requiring authority resurrection or mutation.

Canonical merge/deletion/erasure integration remains governed by the existing
patient lifecycle boundaries and must continue to fail closed. Additional
explicit lifecycle metadata reconciliation can be added only without weakening
the current validation gate.

### 10B.4 — signed write-authority protocol

**SIGNED PROTOCOL IMPLEMENTED; AUTHORITY MINT / WRITE ENABLEMENT NOT STARTED.**

The repository now has a separate versioned protocol:

```text
nexa-treatment-session-v1
```

It does not modify Signed Consent V3 bytes in place. The V1 treatment signature
binds the canonical patient, provider, hospital, provider-session-binding hash,
challenge nonce, purpose, exact normalized operation set, bounded access
duration, issue/expiry timestamps, policy version, treatment-context digest,
and exact active patient device-key identity.

Implemented protocol/lifecycle properties include:

- treatment-session request consumes the opaque provider/hospital/session-bound
  discovery handle;
- server captures current clinical initiation assurance before request issue;
- patient challenge is available only to the authenticated matching patient;
- the context hash fails closed on operation/context substitution;
- signature verification uses the exact active enrolled P-256 patient device
  key, including key id/version/fingerprint;
- provider professional/facility/affiliation/capability trust is re-evaluated
  again when the signed decision is accepted;
- nonce/request resolution is replay-protected and same-response idempotency is
  fingerprint-bound;
- approved and denied decisions are audit-recorded;
- provider-session binding is retained only as a one-way hash.

The currently registered V1 routes are:

```text
POST /api/v2/treatment-session/v1/request
GET  /api/v2/treatment-session/v1/challenge/{request_id}
POST /api/v2/treatment-session/v1/approve-signed
```

The one-time provider claim/mint boundary is now implemented at
`POST /api/v2/treatment-session/v1/{request_id}/claim`. It creates matching live
Redis and durable PostgreSQL authority from the exact signed operation set, but
no clinical write endpoint consumes that authority yet.

Before `CREATE_ENCOUNTER` or any `WRITE_*` operation can become executable, the
next increment must design and qualify a one-time provider claim/mint that:

- revalidates exact provider/hospital/session binding and live provider trust;
- consumes only the patient-signed exact operation set without widening it;
- persists only bearer hashes, never raw bearer authority;
- atomically coordinates replay protection and durable/live session state;
- extends the durable allowed-operation database policy from the current
  read-only constraint through an explicit linear migration;
- remains incapable of authorizing an encounter/write until the central
  operation gate is present.

### 10B.5 — clinical gates and encounter binding

Future work after treatment-session claim/mint exists:

- central `require_clinical_session(operation)` gate;
- no client-selected patient UUID as an independent authority source;
- encounter creation binds the encounter to the session's canonical patient,
  provider and organization;
- subsequent structured writes require both the operation and matching
  encounter/session binding.

### 10B.6 — qualification

Backend release gates include:

- pure security/unit tests;
- PostgreSQL lifecycle/migration tests with zero skips;
- PostgreSQL + Redis concurrency/replay tests with zero skips;
- wrong provider/session/hospital/patient tests;
- expiry/revocation tests;
- duplicate/concurrent claim tests;
- audit/failure rollback tests;
- migration graph single-head assertion.

Frontend work is not the first step. Any later UI integration must preserve the
bounded session in memory/approved state and must not place bearer authority or
patient identifiers in URLs.

## 10B.3 qualification checkpoints — 2026-09-16

The current qualified 10B.3 migration head is
`20260916_clinical_access_sessions`.

Backend CI run #654 on parent head
`be5a08c0fdb60b2aa511a5e634d526f79f0eb1d5` exposed two stale qualification
markers only: the security non-regression migration-head declaration and the
historical Slice-4 disposable database target. Commit
`17a6058ce106bd61c530d0d2e2ef4d3dd34c7b30` reconciled exactly those markers.

The next direct checkpoint, `0e32854a43cd6e2d675a8047b406f874fd4ede2f`,
then passed Backend CI #656 across Partition A, PostgreSQL Partition B, and
PostgreSQL + Redis Partition C, including all three zero-skip assertions. Its
frontend test/Next/workspace job also passed. Those results were intermediate
evidence only because subsequent patient-revocation lifecycle hardening changed
code.

Commit `55bb0a25ecd71b70d5212052cfde184d018ad50c` added explicit durable-session
revocation to the patient consent-revocation transaction and focused
idempotency/call-contract regression assertions.

The final 10B.3 qualification target is
`238c59b7fe94cc04213063f55750f3d727a062c7`.

On that exact SHA:

- Backend CI #657 completed successfully across Partition A, PostgreSQL
  Partition B, and PostgreSQL + Redis Partition C, including all zero-skip
  assertions;
- Frontend CI #606 completed successfully;
- the frontend test job, Next production build, and workspace builds completed
  successfully;
- Android native generation and native-source compilation completed
  successfully;
- iOS native generation, CocoaPods installation, and native-source compilation
  completed successfully.

This freezes 10B.3 as the qualified read-only durable-session checkpoint.
Subsequent 10B.4 commits require their own exact-head qualification and must not
reuse 10B.3 evidence.

## 10B.4 qualification state — 2026-09-17

The signed treatment-session V1 implementation reached
`ddef230b60493a20aec5a71e05b35f0ecb772a21`. Backend CI #666 showed:

- lint: success;
- PostgreSQL Partition B: success + zero skips;
- PostgreSQL + Redis Partition C: success + zero skips;
- Partition A: one failure only, because the three intentional V1 routes had
  not yet been added to the explicit route-governance allowlist.

Commit `e5c3acff2c575f87604b1913c5df1bd25e2148c0` reconciles that exact route
contract. This documentation commit is the next direct exact-head qualification
target. No 10B.4 qualification claim is valid until its own A/B/C and zero-skip
results are green.

## Nonclaims

The current Slice 10B state does **not**:

- mint a write-capable treatment ClinicalAccessSession from V1 approval;
- expose a V1 claim/mint endpoint;
- authorize `CREATE_ENCOUNTER` or any `WRITE_*` operation;
- create encounters;
- change prescription/diagnosis/vitals/note persistence authority;
- broaden Signed Consent V3;
- weaken document-processing consent;
- modify physical-device assurance claims;
- claim production deployment or that Nexa Care is fully secure.

The next engineering increment after claim/mint qualification is the central
`require_clinical_session(operation)` gate and encounter binding. No write endpoint
may consume treatment authority before that gate is separately qualified.
