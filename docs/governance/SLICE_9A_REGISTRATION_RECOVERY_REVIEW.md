# Slice 9A — Patient Registration Recovery Manual Review

Status: **IN PROGRESS / NOT QUALIFIED / NOT MERGE-ELIGIBLE**

Base: `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`

## Purpose

Slice 9A closes the cases intentionally rejected by automatic patient registration recovery. It creates a durable, auditable manual-review lifecycle for account-graph inconsistencies without weakening authentication, device trust, erasure, merge, or consent boundaries.

This slice does **not** turn a phone OTP into authority to resurrect an erased, revoked, merged, ambiguous, or otherwise retired patient identity.

## Authority separation

The following remain distinct:

`phone OTP proof != account session != manual recovery review authority != repair authorization != device authority != consent authority`

A patient may initiate and inspect the status of their own recovery case only after the patient-facing recovery flow has verified the external identity and classified the graph as manual-review-required. The patient cannot choose the repair action.

The existing `identity_reviewer` document-review role is deliberately **not reused as the complete authorization contract**. That role requires a patient/hospital document-processing capability and active consent. Registration recovery must work when ordinary account/device authority is unavailable and must not manufacture consent to unlock the review path.

Operator mutation therefore requires a dedicated `registration_recovery_reviewer` authority contract. The implementation must bind it to an authenticated, current server-side reviewer identity and must not accept a role string, provider UUID, hospital UUID, or frontend claim as sufficient authority.

## Case states

Closed lifecycle:

- `PENDING`
- `IN_REVIEW`
- `RESOLVED`
- `REJECTED`
- `SECURITY_ESCALATED`

Terminal states are immutable except through a separately designed security-administration correction mechanism; this slice does not introduce one.

## Durable case binding

Every case records only the minimum metadata necessary to investigate the registration graph:

- case UUID and opaque patient-visible case reference;
- external provider (`supabase`) and provider-subject hash, never phone or OTP;
- candidate patient UUID when known;
- graph fingerprint captured when the case was opened;
- closed reason codes that caused manual review;
- status, version, creation/claim/resolution timestamps;
- assigned reviewer and reviewer authority version;
- terminal outcome and reason codes;
- immutable operation hashes/idempotency keys for mutating actions.

No OTP, access token, refresh token, device private key, raw medical data, national identifier, or full audit payload belongs in the case table.

## Permitted terminal outcomes

The initial closed vocabulary is intentionally conservative:

- `RESTORE_MISSING_RECORD_ANCHOR` — permitted only when the patient and exactly one live matching auth identity remain valid and the erasure registry is clear.
- `REBIND_MERGED_IDENTITY` — permitted only when a single unambiguous tombstone chain resolves to one live canonical patient and no active conflicting identity exists.
- `NO_REPAIR` — investigation confirms the durable graph should remain unchanged.
- `SECURITY_ESCALATION_REQUIRED` — suspected identity collision, tampering, privacy incident, erasure inconsistency, or other state outside safe repair rules.

The following are forbidden in Slice 9A:

- clearing an erasure tombstone;
- undeleting an unexplained deleted patient;
- un-revoking a provider identity;
- assigning one external identity to two active patients;
- moving clinical records between patients;
- generating or replacing patient private keys;
- granting provider access or patient consent;
- bypassing merge/tombstone invariants.

## Mandatory checks before any repair

1. Re-read and lock the relevant patient/auth-identity/tombstone graph.
2. Recompute the graph fingerprint and compare it with the case binding.
3. Verify the case is `IN_REVIEW`, version matches, and the caller is the assigned current reviewer.
4. Verify the erasure registry is reachable and clear for every patient record that would become authoritative.
5. Re-evaluate the requested outcome against the closed server-side policy; do not trust client-submitted reason text.
6. Stage mutation and `PATIENT_REGISTRATION_RECOVERY_REVIEW_RESOLVED` audit event in one database transaction.
7. Commit once. If audit enqueue or any invariant fails, roll back the repair.
8. Never issue a patient session, device grant, or consent token from the operator-review route.

The patient must return through the normal patient-facing recovery/authentication path after an approved repair.

## Audit vocabulary

Required events:

- `PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_CLAIMED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_RESOLVED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_REJECTED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_SECURITY_ESCALATED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_ACCESS_REJECTED`

Events must not contain phone, OTP, raw provider tokens, device private material, or clinical content.

## Adversarial qualification gates

Before merge, tests must prove at minimum:

- one verified identity cannot open duplicate active cases for the same graph fingerprint;
- anonymous/unverified callers cannot create a case;
- one reviewer cannot steal a case claimed by another reviewer;
- stale case versions cannot mutate state;
- terminal case replay is idempotent and cannot create a second repair;
- graph mutation after case opening causes fail-closed stale-state rejection;
- erased patients cannot be repaired;
- revoked identities cannot be silently restored;
- conflicting live identities cannot be rebound;
- merge chains are followed deterministically and cycles/ambiguity fail closed;
- audit enqueue failure rolls the repair back;
- patient status responses contain no internal reviewer notes or authority material;
- no operator route returns patient access/session/device/consent authority;
- real PostgreSQL concurrency qualification linearizes claim and resolution transitions.

## Review findings at commencement

1. `main` already has explicit `REGISTRATION_RECOVERY_REQUIRED` semantics and tests for corrupted historical registration graphs.
2. The existing document `identity_reviewer` gate is consent/document-capability bound and therefore cannot be reused unchanged for account recovery.
3. `Patient.is_deleted` is not sufficient evidence that an account may be restored because merge tombstones also retire patient rows.
4. The erasure registry is an independent fail-closed authority and must be checked before any repair.
5. The existing identity-review subsystem provides useful patterns—closed status/outcome vocabularies, optimistic versions, reviewer assignment, operation hashes, idempotency, `SELECT ... FOR UPDATE`, and transactional audit outbox—but its document-specific authorization and data model must not be copied wholesale.

## Next implementation steps

1. Add dedicated durable case/disposition models and migration from current head `20260909_device_trust_lifecycle`.
2. Add server-side review-policy and reviewer-authorization modules.
3. Connect the patient-facing manual-review classification to case creation without exposing provider subject or internal graph details.
4. Add patient status endpoint and operator claim/resolve endpoints.
5. Add PostgreSQL concurrency/adversarial tests and route-authorization tests.
6. Update migration-head contracts, route registry, current-state documentation, and exact-head CI evidence.

No merge or completion claim is valid until these gates are green on one frozen head.