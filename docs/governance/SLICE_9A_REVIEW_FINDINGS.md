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

**Resolution:** the unused import was removed at `9a6badd944b373b7740d3cfe1e3e9e0bc6994c3a`. Fresh Backend CI #509 confirmed Ruff passes and Partition A proceeds to tests.

## Finding 008 — new migration makes deferred head-contract work a qualification prerequisite

Backend CI #509 real PostgreSQL+Redis Partition C failed two existing provider-trust CLI qualification tests with `SCHEMA_REVISION_MISMATCH`. The CI shared-database preparation still migrated to `20260909_device_trust_lifecycle`, while this Slice legitimately adds `20260910_registration_recovery_review`. Consequently any runtime/CLI guard that requires the repository's canonical Alembic head saw the shared CI database as stale.

**Partial resolution:** the CI shared DB target, production migration runner, migration-graph contract, pilot operations runbook, and current-authority documentation test were advanced to `20260910_registration_recovery_review`. Backend CI #515 then confirmed the shared CI database preparation itself succeeds against the new migration.

## Finding 009 — Step 2 contract qualification exposed one audit-registry gap and three stale current-head assertions

Backend CI #515 on head `a1e17ba3bfbc9390f9bba3ccffb3cac90c013966` passed Ruff but Partition A reported exactly **4 failures / 3770 passes / 415 deselected**:

1. `test_no_undocumented_audit_events` found `PATIENT_REGISTRATION_RECOVERY_REVIEW_ACCESS_REJECTED` in code but not in the canonical documented audit-event vocabulary.
2. `test_governance_contract_names_current_migration_head` found the Engineering Constitution still names the old migration head.
3. `test_trust_authorization_migration_is_single_head_and_forward_only` still treats `20260909_device_trust_lifecycle` as the repository head rather than as the provider/device-trust revision whose lineage must remain asserted.
4. `test_provider_trust_migration_is_current_single_head` has the same stale repository-head assumption.

These are contract-maintenance failures caused by adding the Slice 9A review migration and reviewer rejection audit event. They are not justification to remove the audit event, weaken reviewer fail-closed behavior, or rewrite historical revision IDs.

**Resolution:** the canonical audit-event coverage now registers both `PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED` and `PATIENT_REGISTRATION_RECOVERY_REVIEW_ACCESS_REJECTED`; the Engineering Constitution names `20260910_registration_recovery_review`; and the two provider-trust migration tests retain their feature ancestry checks while comparing the repository head against the Slice 9A revision.

## Finding 010 — Slice 4 end-to-end qualification fixture still migrates its private database to the old head

Backend CI #521 on exact Step 2 head `0544c3128c1e3c7a6e0db5f6cb49a17e7c1da5c3` confirmed that the shared CI database itself now upgrades successfully to `20260910_registration_recovery_review`, but PostgreSQL+Redis Partition C reported exactly **2 failures / 128 passes / 4061 deselected**. Both failures are in `tests/integration/test_provider_trust_slice4_e2e_postgres_redis.py` and occur when the provider-trust root governance CLI dynamically verifies the repository head against that test module's separately created disposable database.

The test module still declares `HEAD = "20260909_device_trust_lifecycle"` and therefore intentionally migrates its own private database one revision behind the current repository. The resulting `SCHEMA_REVISION_MISMATCH` is correct fail-closed behavior by the governance CLI, not a Slice 9A Redis or reviewer-authority regression.

**Decision:** update only that fixture's current repository-head constant to `20260910_registration_recovery_review`. Preserve every Slice 4 provider-trust authority, CLI, concurrency and adversarial assertion unchanged. Then rerun exact-head Backend CI and require Partition A, PostgreSQL B, and PostgreSQL+Redis C all green before Step 3.

## Review rule before Step 3

Findings 001–003 remain enforced in ORM/migration. Finding 004 is resolved. Findings 005–006 govern Step 2 semantics. Finding 007 is resolved. Findings 008–009 are resolved in the branch contracts. Finding 010 now blocks Step 3 until the private Slice 4 qualification database is migrated to the same repository head and fresh exact-head Backend CI is green across all partitions.
