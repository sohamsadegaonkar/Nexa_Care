# Slice 9A — Patient Registration Recovery Manual Review

Status: **IN PROGRESS / NOT QUALIFIED / NOT MERGE-ELIGIBLE**

Base: `main` `54351f9a55ba94665420961cfe766bdcc84a5398`

## Purpose

Slice 9A closes the cases intentionally rejected by the now-merged patient registration account-recovery workflow. It creates a durable, auditable manual-review lifecycle for account-graph inconsistencies without weakening authentication, device trust, erasure, merge, or consent boundaries.

This slice does **not** turn a phone OTP into authority to resurrect an erased, revoked, merged, ambiguous, or otherwise retired patient identity.

## Authority separation

The following remain distinct:

`phone OTP proof != account session != manual recovery review authority != repair authorization != device authority != consent authority`

A patient may initiate and inspect the status of their own recovery case only after the patient-facing recovery flow has verified the external identity and classified the graph as manual-review-required. The patient cannot choose the repair action.

The existing `identity_reviewer` document-review role is deliberately **not reused as the complete authorization contract**. Registration recovery must work when ordinary account/device authority is unavailable and must not manufacture consent to unlock the review path.

Operator mutation therefore requires the dedicated `registration_recovery_reviewer` authority contract. The implementation binds it to an authenticated current provider identity, exact live provider session, recent MFA, current PostgreSQL affiliation, `ACTIVE` affiliation trust status, current validity window, and the server-owned reviewer role.

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
- assigned reviewer, reviewer authority version, and one-way reviewer session binding;
- terminal outcome and reason codes;
- immutable operation hashes/idempotency keys for mutating actions.

No OTP, access token, refresh token, device private key, raw medical data, national identifier, or full audit payload belongs in the case table.

## Permitted terminal outcomes

The initial closed vocabulary is intentionally conservative:

- `RESTORE_MISSING_RECORD_ANCHOR` — permitted only when the patient and exactly one live matching auth identity remain valid and the erasure registry is clear.
- `REBIND_MERGED_IDENTITY` — permitted only when a single unambiguous tombstone chain resolves to one live canonical patient and no active conflicting identity exists.
- `NO_REPAIR` — investigation confirms the durable graph should remain unchanged.
- `SECURITY_ESCALATION_REQUIRED` — suspected identity collision, tampering, privacy incident, erasure inconsistency, or other state outside safe repair rules.

Forbidden in Slice 9A: clearing erasure state, undeleting unexplained deletion, un-revoking identity, assigning one external identity to two live patients, moving clinical records, generating patient private keys, granting provider access or consent, or bypassing merge/tombstone invariants.

## Mandatory checks before any repair

1. Re-read and lock the relevant patient/auth-identity/tombstone graph.
2. Recompute the graph fingerprint and compare it with the case binding.
3. Verify the case is `IN_REVIEW`, version matches, and the caller is the assigned current reviewer with exact session binding.
4. Verify erasure state is reachable and clear for every patient row that would become authoritative.
5. Re-evaluate the requested outcome against the closed server-side policy.
6. Stage mutation and `PATIENT_REGISTRATION_RECOVERY_REVIEW_RESOLVED` audit event in one database transaction.
7. Commit once; audit or invariant failure rolls back repair.
8. Never issue patient session, device, or consent authority from an operator-review route.

The patient returns through the normal patient-facing recovery/authentication path after an approved repair.

## Audit vocabulary

- `PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_CLAIMED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_RESOLVED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_REJECTED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_SECURITY_ESCALATED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_ACCESS_REJECTED`

Events must not contain phone, OTP, raw provider tokens, device private material, or clinical content.

## Adversarial qualification gates

Before merge, tests must prove duplicate-case prevention, verified-origin case creation, reviewer isolation, stale-version rejection, terminal idempotency, graph-change rejection, erasure and revocation fail-closed behavior, conflicting-identity rejection, deterministic merge handling, transactional audit rollback, patient-status privacy, absence of operator-issued patient authority, and real PostgreSQL linearization of claim/resolution races.

## Current implementation state

- parent patient-facing recovery workflow is merged and qualified on `main`;
- durable case/disposition model and migration are re-established on this exact base;
- reviewer authority version, closed reason vocabulary, and exact reviewer-session binding are persisted;
- dedicated fail-closed reviewer authorization gate and focused security tests are re-established;
- Step 2, verified manual-review classification to idempotent case creation, is next and must be reviewed before Step 3 begins.

No merge or completion claim is valid until final exact-head gates are green.
