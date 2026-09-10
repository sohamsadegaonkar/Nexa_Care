# Slice 9A — Rolling Review Findings

Status: **ACTIVE REVIEW LOG**

Current verified base: `54351f9a55ba94665420961cfe766bdcc84a5398`

This file is updated whenever implementation review discovers a material authority, schema, concurrency, privacy, dependency, or qualification finding. Findings are recorded before proceeding to the dependent step.

## Finding 001 — reviewer authority version not persisted

The Slice 9A governance contract requires the assigned reviewer authority version to be durable, but the initial case model/migration only persisted `assigned_reviewer_id` and `assigned_reviewer_role`.

**Decision:** add `reviewer_authority_version` to `IN_REVIEW` and terminal cases, require it to be null while `PENDING`, and persist the exact server-owned authorization policy version used at claim time. Terminal resolution must verify the current reviewer authorization remains valid; the stored version records the authority contract under which the claim occurred.

## Finding 002 — reason-code vocabulary not closed in PostgreSQL

The Python model defines a closed `RegistrationRecoveryReviewReason` enum, but the initial migration only requires `cardinality(reason_codes) > 0`. Direct SQL could therefore persist unknown reason strings.

**Decision:** add PostgreSQL CHECK constraints restricting case and disposition reason arrays to the closed server-owned vocabulary and requiring them to be non-empty. Application validation remains in addition to the database constraint.

## Finding 003 — claimed case is not bound to the reviewer session

The initial case model records reviewer identity but not the authenticated reviewer session. A second concurrent/stolen session belonging to the same provider identity could otherwise attempt terminal mutation without proving continuity with the session that claimed the case.

**Decision:** add a one-way `review_session_binding` SHA-256 value to `IN_REVIEW` and terminal cases. It is derived only from the current server-authenticated provider session, never supplied as trusted input by the client. Claim stores it; session recovery may rotate it only through an explicit high-risk operation by the same authorized reviewer; terminal disposition requires an exact constant-time match.

## Finding 004 — Slice 9A depended on an unmerged patient-facing recovery workflow

Step 2 requires connecting a verified patient manual-review classification to idempotent case creation. That classifier originally lived only on `security/patient-registration-recovery-workflow`.

**Resolution:** the parent workflow was independently reviewed and qualified, then merged to `main` as `54351f9a55ba94665420961cfe766bdcc84a5398` from exact reviewed head `309d85b7a79474cc3ee62cc63d4f707d7d2ae59c` after successful Backend CI #487 and Frontend CI #436. Slice 9A was reconciled onto that `main` with a two-parent merge commit; the old branch head is preserved at `slice-9a-registration-recovery-review-backup-b47ed48c`.

## Finding 005 — classifier reason vocabulary and durable review vocabulary differ

The verified patient-facing classifier emits concrete graph-state reason codes such as `LINKED_PATIENT_MISSING`, `PATIENT_RECORD_ANCHOR_MISSING`, `MULTIPLE_SOURCE_IDENTITIES`, `MERGE_TOMBSTONE_CYCLE`, `MERGE_TOMBSTONE_CHAIN_TOO_DEEP`, `DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE`, `CANONICAL_PATIENT_UNAVAILABLE`, `CANONICAL_ERASURE_STATE_PRESENT`, and `CANONICAL_IDENTITY_CONFLICT`. The durable review schema intentionally uses a smaller normalized closed vocabulary.

Persisting raw classifier strings would violate the database CHECK constraint and would couple the durable review contract to implementation-specific classifier detail.

**Decision:** Step 2 must use an explicit server-owned normalization map from classifier reason -> durable `RegistrationRecoveryReviewReason`. Unknown classifier reasons fail closed as `SECURITY_CONCERN`; they must never be persisted as arbitrary strings. The original concrete reason may appear only in value-bounded server audit metadata if separately approved; it is not part of the durable case reason array.

## Finding 006 — case creation idempotency must be graph-bound, not attempt-bound

A patient may perform more than one successful OTP recovery attempt while the underlying registration graph remains unchanged. If case creation idempotency were derived from the transient recovery attempt ID, each new OTP attempt could create another durable case for the same graph.

**Decision:** the durable creation idempotency key and uniqueness semantics must bind to provider + one-way provider-subject digest + graph fingerprint, not to the transient OTP attempt. Repeated verified classification of the same graph returns the existing case. A genuinely changed graph fingerprint may create a new review case after server-side classification.

## Finding 007 — Step 2 exact-head qualification stopped at reviewer-gate lint

Backend CI #506 on Step 2 implementation/test head `64a1d17a9a48a9a16123581017b39e1c10757c91` stopped in Partition A before tests because Ruff reported one `F401`: `datetime.timedelta` is imported but unused in `app/core/registration_recovery_review_gate.py`. This is isolated to the Slice 9A reviewer gate and is not a parent-branch or unrelated baseline failure.

**Decision:** remove only the unused `timedelta` import, then require a fresh exact-head lint/pure-unit qualification before starting Step 3. Do not use skipped Partition A tests from CI #506 as evidence. PostgreSQL/Redis jobs from that run may provide diagnostic information but do not override the failed Step 2 gate.

## Review rule before Step 3

Findings 001–003 remain enforced in ORM/migration. Finding 004 is resolved. Findings 005–006 govern Step 2 semantics. Finding 007 blocks Step 3 until the targeted lint fix is committed and a fresh exact-head lint/pure-unit run succeeds without Step 2 regression.
