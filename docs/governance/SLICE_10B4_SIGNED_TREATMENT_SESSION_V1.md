# Slice 10B.4 — Patient-Signed Treatment Session V1

Status: **SIGNED PROTOCOL + ONE-TIME CLAIM/MINT QUALIFIED — 10B.5 GATE QUALIFICATION PENDING**

Qualified signed-protocol checkpoint:
`87ee87e011206f1684e8590500e2584c4ea15c14` on `main`.

Claim/mint implementation checkpoint:
`3891ae72a48da0e4bafbf4f5995a05abd39aebca` on `main`.

Claim/mint qualified tree checkpoint:
`f30a9b47ee6db331d22aba0dbd5862134be282a3`
(tree `8acae69b0b2bbde011d22c2ca024ef4bf6a50df1`).

PR #49's GitHub merge-test commit
`449c59e0bfa47496ae3d7d6482f0dca4ac518feb` has the exact same tree SHA.
Backend CI #674 passed Partition A, PostgreSQL Partition B, and PostgreSQL +
Redis Partition C, including all three zero-skip assertions. The only delta
from `342d25c` is the stale Slice 7B evidence-fixture migration-head marker;
the production validator and treatment authority were unchanged.

This records qualification of the exact tree. It does not claim PR #49 has
been merged to `main`; merge/integration remains a separate governed action.

## Why this protocol exists

Signed Consent V3 remains a read-only authority because its patient-signed bytes bind `purpose` and `scope`, not an explicit clinical write-operation set. It must not be widened in place.

Treatment Session V1 is therefore a distinct cryptographic protocol:

- protocol version: `nexa-treatment-session-v1`;
- domain: `NEXA_CARE_SIGNED_TREATMENT_SESSION`;
- signing operation: `TREATMENT_SESSION_DECISION`;
- policy version: current server-owned `clinical-access-v1`;
- exact patient/provider/hospital/request binding;
- one-way binding to the exact initiating provider session;
- exact challenge nonce and expiry binding;
- exact patient device/key-version binding;
- exact, closed operation-set binding.

## Operation-set contract

The signed operation set is drawn only from the server-owned `ClinicalAccessOperation` vocabulary:

- `READ_CLINICAL_HISTORY`
- `READ_DOCUMENTS`
- `CREATE_ENCOUNTER`
- `WRITE_PRESCRIPTION`
- `WRITE_DIAGNOSIS`
- `WRITE_VITALS`
- `WRITE_CLINICAL_NOTES`
- `ORDER_INVESTIGATION`

Unknown, duplicate, empty, or non-sequence operation sets fail closed. The operation set is semantically unordered and therefore sorted during canonicalization. Adding or removing an operation changes the treatment-context hash and the patient-signed decision bytes.

The durable database contract keeps legacy Signed Consent V3 sessions read-only: `clinical` and `full` scopes still permit exactly `READ_CLINICAL_HISTORY`. Only `scope='treatment'` may carry the closed Treatment Session V1 operation set.

## Exact provider-session binding

The server hashes the initiating provider's authenticated session binding before it enters the patient-signed treatment context. The raw session binding is never placed in signed bytes, persistence, logs, or API payloads.

The resulting lowercase SHA-256 value is signed by the patient. Claim compares the current provider session against this signed hash in constant time before authority is minted, preventing approval created for provider session A from being rebound to a later provider session B.

## Canonical signed context

The server-created treatment context binds:

```text
request_id
patient_id
provider_id
hospital_id
provider_session_binding_hash
challenge_nonce
purpose
allowed_operations
access_duration
issued_at
expires_at
policy_version
protocol_version
domain
signing operation
```

The patient decision additionally binds:

```text
decision
treatment_context_hash
device_id
key_id
key_version
public_key_fingerprint
```

The verifier requires the exact currently-active P-256 patient device-key row, matching the existing Signed Consent V3 key-lifecycle security model while remaining cryptographically domain-separated from V3.

## Implemented request/signature lifecycle

The registered Treatment Session V1 lifecycle now includes:

- `POST /api/v2/treatment-session/v1/request`;
- `GET /api/v2/treatment-session/v1/challenge/{request_id}`;
- `POST /api/v2/treatment-session/v1/approve-signed`;
- `POST /api/v2/treatment-session/v1/{request_id}/claim`.

The request/challenge/approval lifecycle preserves provider/patient/device trust checks and one-time replay protection. The claim boundary revalidates the exact provider/hospital/session binding, recomputes the patient-signed context hash, rechecks live provider eligibility, rechecks the exact approving patient device key, and rejects expired approval evidence.

## One-time claim/mint boundary

A successful claim creates two matching authority representations from the exact signed operation set:

1. an opaque, expiring Redis capability addressed only by a SHA-256 token digest; and
2. a durable PostgreSQL `ClinicalAccessSessionRecord` plus `ConsentGrantLog` in the same database transaction.

The raw bearer token and raw provider-session binding are not persisted. Claim is one-time per signed request. A failed durable finalization invalidates the live Redis capability; a post-commit audit failure invalidates Redis and revokes both durable authority records with `CLAIM_FINALIZATION_FAILED`.

The claim response is `Cache-Control: no-store` and returns the opaque treatment token, clinical session ID, exact signed operations, purpose, patient binding, and expiry.

## Current authority boundary

Minting authority does **not** by itself enable any clinical write endpoint.

No existing record-write route has been changed to trust the new token. Existing Signed Consent V3 remains `READ_CLINICAL_HISTORY` only. The new treatment capability is intentionally inert until the central clinical-session operation gate is implemented and separately qualified.

Therefore the next security boundary is not another widening of consent. It is a server-owned operation gate that must prove, for each attempted operation, that the live Redis capability and durable session/grant agree on patient, provider, hospital, exact provider session, expiry, revocation state, policy version, and the requested `ClinicalAccessOperation`.

## Qualification gates for this increment

Before this claim/mint increment is called qualified, the exact target commit must pass:

- Backend Partition A and zero-skip assertion;
- PostgreSQL Partition B and zero-skip assertion;
- PostgreSQL + Redis Partition C and zero-skip assertion;
- migration graph and PostgreSQL constraint qualification for `20260917_treatment_session_operations`;
- route-governance and focused Treatment Session V1 mint/claim tests.

Focused staging qualification before landing on `main` passed Ruff and 60 focused unit/contract tests. That focused evidence is not a substitute for the exact-head A/B/C gates above.

## Next implementation step

Claim/mint qualification is now closed on the exact tree recorded above.
Slice 10B.5a implements the central `require_clinical_session(operation)`
authority gate plus a server-owned durable encounter-correlation primitive.

The 10B.5a gate must qualify independently before any existing clinical write
route is changed to consume the treatment token. Prescription, diagnosis,
vitals, notes, investigation, document, allergy, or encounter write behavior
must remain unchanged until that boundary has its own fail-closed adversarial
qualification.
