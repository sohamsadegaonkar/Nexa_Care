# Slice 9A — Patient Registration Recovery Manual Review

Status: **IMPLEMENTED / MERGE GATED ON EXACT-HEAD QUALIFICATION**

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
- stable auth-identity UUID plus external provider (`supabase`) and provider-subject hash, never phone or OTP;
- candidate patient UUID when known;
- graph fingerprint captured when the case was opened;
- closed reason codes that caused manual review;
- status, version, creation/claim/resolution timestamps;
- assigned reviewer, reviewer authority version, and one-way reviewer session binding;
- terminal outcome and reason codes;
- immutable operation hashes/idempotency keys for mutating actions.

No OTP, access token, refresh token, device private key, raw medical data, national identifier, or full audit payload belongs in the case table.

## Patient-safe and reviewer API surfaces

Patient status:

- `GET /api/v2/auth/registration-recovery/review/cases/{case_reference}`

The response is intentionally limited to the opaque case reference, public case status, terminal flag, next action and timestamps. Provider subject, graph fingerprint, reason internals, reviewer identity and session binding are not exposed.

Reviewer operations require the dedicated reviewer gate:

- `GET /api/v2/auth/registration-recovery/review/reviewer/cases`
- `GET /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}`
- `POST /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}/claim`
- `POST /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}/recover-session`
- `POST /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}/resolve`

A reviewer sees pending work plus cases assigned to that reviewer; claimed or terminal cases assigned to another reviewer are not exposed through the reviewer detail surface.

## Permitted terminal outcomes

The initial closed vocabulary is intentionally conservative:

- `RESTORE_MISSING_RECORD_ANCHOR` — only when the current locked graph still exactly satisfies the already-bounded automatic missing-record repair policy.
- `REBIND_MERGED_IDENTITY` — only when the current locked graph still exactly satisfies the already-bounded automatic deterministic merge-rebind policy.
- `NO_REPAIR` — investigation confirms the durable graph should remain unchanged.
- `SECURITY_ESCALATION_REQUIRED` — suspected identity collision, tampering, privacy incident, erasure inconsistency, or other state outside safe repair rules.

Forbidden in Slice 9A: clearing erasure state, undeleting unexplained deletion, un-revoking identity, assigning one external identity to two live patients, moving clinical records, generating patient private keys, granting provider access or consent, or bypassing merge/tombstone invariants.

## Mandatory checks before terminal resolution

1. Lock the durable case and verify optimistic version.
2. Verify `IN_REVIEW`, assigned reviewer identity and exact reviewer-session binding.
3. Resolve the stable auth-identity anchor and verify the provider-subject hash.
4. Acquire the same PostgreSQL advisory-lock domain used by automatic registration recovery.
5. Re-run the server-side registration graph classifier and require the exact stored graph fingerprint.
6. Re-evaluate any repair request against the closed server-side automatic-repair vocabulary.
7. Persist the one terminal disposition row, case transition and required audit-outbox event in the same database transaction.
8. Roll back the complete transition if audit insertion or any invariant fails.
9. Never issue patient session, device, or consent authority from an operator-review route.

After an approved repair, the patient returns through the normal patient-facing recovery/authentication path.

## Audit vocabulary

- `PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_CLAIMED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_RESOLVED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_REJECTED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_SECURITY_ESCALATED`
- `PATIENT_REGISTRATION_RECOVERY_REVIEW_ACCESS_REJECTED`

Events must not contain phone, OTP, raw provider tokens, device private material, or clinical content.

## Qualification gates

Merge requires exact-head qualification proving, at minimum:

- duplicate durable case prevention;
- verified-origin case creation and patient-safe case reference transport;
- dedicated reviewer authorization with session, recent MFA, live affiliation and server-owned role;
- reviewer claim isolation and stale-version rejection;
- terminal idempotency with one disposition and one terminal audit event;
- graph-fingerprint change rejection under the shared recovery lock domain;
- erasure/revocation/ambiguity remaining non-repairable;
- audit failure rolling terminal state back;
- patient status responses containing no internal authority material;
- no reviewer route issuing patient session/device/consent authority; and
- real PostgreSQL concurrency linearizing claim and terminal resolution races.

## Current implementation state

Implemented on PR #43:

- durable case/disposition schema and migration `20260910_registration_recovery_review`;
- verified patient manual-review classification to idempotent durable case creation;
- stable auth-identity UUID anchoring and closed reason normalization;
- dedicated fail-closed `registration_recovery_reviewer` gate;
- patient-safe status API;
- reviewer list/detail, claim, session recovery and terminal resolution APIs;
- reviewer visibility isolation;
- graph revalidation using the automatic-recovery advisory-lock domain;
- closed bounded repair mapping, terminal no-repair and security escalation;
- transactional terminal disposition/audit coupling;
- audit vocabulary registration and migration-head reconciliation; and
- disposable PostgreSQL claim-race, terminal replay and audit-rollback qualification coverage.

Implementation alone is not a completion claim. Slice 9A is merge-eligible only when Backend and Frontend CI are green on the exact frozen PR head and that exact head is merged.
