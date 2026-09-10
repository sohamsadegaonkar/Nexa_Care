# Slice 9A — Reverified Baseline and Execution Plan

Status: **ACTIVE / NOT QUALIFIED / NOT MERGE-ELIGIBLE**

Reverified at: 2026-09-11

Authoritative repository: `sohamsadegaonkar/Nexa_Care`

Authoritative base branch: `main`

Verified base SHA: `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`

Active implementation branch: `slice-9a-registration-recovery-review`

Pre-execution branch head: `7746f9b54fedcb94bdc0b9d864393da3eb92148f`

Draft PR: `#41`

## Documentation rule for future Nexa Care slices

Whenever a new authoritative base is established, a material security/architecture finding is discovered, or a new dependent slice/sub-plan becomes necessary, record it in a committed Markdown governance/review artifact at that point. Do not wait until final attestation to make the reasoning durable.

## Reverified findings

1. `main` has not moved from the reviewed registration-recovery semantics merge.
2. PR #41 remains draft and its current code scope is only the Slice 9A governance contract, durable case/disposition model, and migration.
3. The existing document `identity_reviewer` authorization cannot be reused unchanged for registration recovery because it requires an active patient/hospital document-processing capability. Registration recovery must not manufacture consent merely to authorize account-recovery review.
4. The existing provider authentication dependency is a suitable authentication primitive: it resolves current server-side provider credentials/session plus active hospital affiliation. It is necessary but not sufficient for registration-recovery review.
5. A dedicated `registration_recovery_reviewer` authorization layer must therefore sit *after* provider authentication and must require a server-owned role/permission from the current affiliation or another explicit organizational grant. No caller-supplied provider ID, hospital ID, role string, frontend claim, patient OTP, patient JWT, or consent token may substitute for that reviewer authority.
6. Reviewer routes must never mint patient session, device, consent, or provider-clinical authority. A successful operator repair only repairs the durable registration graph; the patient must authenticate again through the normal patient-facing recovery/login path.

## Execution sequence

1. Implement the dedicated reviewer authorization gate and tests.
2. Connect verified patient manual-review classification to idempotent durable case creation.
3. Add a patient-visible, capability-bound case-status API that exposes no reviewer internals or authority material.
4. Add reviewer claim/session-recovery semantics with optimistic versioning and assigned-reviewer checks.
5. Add terminal disposition/repair policy and service.
6. Lock and recompute the registration graph before every repair; reject stale or ambiguous state.
7. Couple graph mutation and the terminal recovery-review audit event transactionally.
8. Add real PostgreSQL concurrency/adversarial qualification, including duplicate-open, claim races, stale versions, terminal replay, erasure/revocation/merge ambiguity, and audit rollback.
9. Update migration-head contracts, deployment head references, route registry, and current-state documentation.
10. Freeze one exact head, run the complete backend and frontend/native CI matrix, review the exact diff and review state, reverify `main`, and merge only through an exact-head guard if all required gates are genuinely green.

## Nonclaims

This baseline/plan commit does not qualify Slice 9A and does not authorize merge. No manual recovery case can yet be claimed or resolved through a production route solely because this document exists.
