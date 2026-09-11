# Slice 9A — Rolling Review Findings

Status: **ACTIVE REVIEW LOG / CLOSURE GATED ON EXACT-HEAD CI**

Base: `54351f9a55ba94665420961cfe766bdcc84a5398`

This file records material authority, schema, concurrency, privacy, migration, and qualification findings discovered while implementing Slice 9A. Findings are resolved before dependent work proceeds; historical findings remain here as evidence.

## Finding 001 — reviewer authority version not persisted

The initial case schema persisted reviewer identity/role but not the exact server-owned reviewer authority version.

**Decision / status: RESOLVED.** `reviewer_authority_version` is required for claimed and terminal cases and is recorded on terminal disposition rows.

## Finding 002 — reason-code vocabulary not closed in PostgreSQL

The Python model defined a closed reason enum while the initial database constraint only required a non-empty array.

**Decision / status: RESOLVED.** PostgreSQL case/disposition constraints require non-empty arrays whose values are subsets of the closed server-owned recovery-review reason vocabulary.

## Finding 003 — claimed case was not bound to the reviewer session

Reviewer identity alone would allow another concurrent session for the same provider identity to attempt terminal mutation.

**Decision / status: RESOLVED.** Claimed and terminal cases persist a one-way SHA-256 `review_session_binding`, derived from the current authenticated provider session. Terminal mutation must match it; explicit reviewer-session recovery may rotate it only under the dedicated high-risk reviewer authority gate.

## Finding 004 — Slice 9A depended on an unmerged patient-facing recovery workflow

Durable manual review requires the patient-facing flow to verify the external identity and classify the registration graph as manual-review-required.

**Decision / status: RESOLVED AS A DEPENDENCY.** PR #40 was independently reviewed, qualified, and merged. `main` contains that workflow at merge commit `54351f9a55ba94665420961cfe766bdcc84a5398`. PR #43 was recreated directly from that verified parent; the stale pre-parent Slice 9A branch is not merged into this lineage.

## Finding 005 — parent classifier reasons do not equal the durable review vocabulary

The patient registration-recovery classifier emits finer-grained internal reasons than the durable Slice 9A schema.

**Decision / status: RESOLVED.** Case creation owns a server-side normalization boundary. Erasure, revocation, identity conflict, unexplained deletion and merge ambiguity map into the closed durable vocabulary; unknown future internal manual-review reasons fail closed to `SECURITY_CONCERN`. Patient responses expose the durable case reference and stable public error code, not provider subject, graph fingerprint or classifier internals.

## Finding 006 — provider-subject hash alone is not a sufficient graph anchor

A privacy-safe provider-subject hash plus optional patient UUID does not deterministically identify the exact external-auth identity row after graph movement.

**Decision / status: RESOLVED.** Review cases persist the stable non-secret `PatientAuthIdentity.identity_id` as a non-null foreign-key anchor. Case opening locks and verifies that row from the already-verified provider subject; the raw provider subject is not stored on the review case.

## Finding 007 — identity movement during case opening is stale graph state, not a retryable outage

Treating every durable case-open failure as retryable could preserve a stale mental model after the identity graph had changed.

**Decision / status: RESOLVED at `ca135e1ae4d32db8f06a769814b73e78f77fee2b`.** A durable review conflict/invalid origin rolls back the case/audit transaction, consumes the exact verified recovery attempt, and returns `REGISTRATION_RECOVERY_STATE_CHANGED` with HTTP 409. Unexpected infrastructure failure remains retryable and releases the verifier claim when safe.

## Finding 008 — reviewer list/detail could expose another reviewer's claimed case metadata

A global reviewer listing would unnecessarily disclose claimed or terminal case metadata across reviewers even though mutation authority was assignment-bound.

**Decision / status: RESOLVED IN CLOSURE IMPLEMENTATION.** Reviewer listing returns pending work plus cases assigned to the current reviewer. Reviewer detail denies claimed/terminal cases assigned to another reviewer. Patient status remains a separate minimal opaque-handle surface.

## Finding 009 — terminal manual repair must not become a second, weaker repair engine

A bespoke operator repair implementation could bypass the constraints already established by automatic registration recovery.

**Decision / status: RESOLVED IN CLOSURE IMPLEMENTATION.** Terminal resolution acquires the same PostgreSQL advisory-lock domain used by automatic registration recovery, resolves the stable auth-identity anchor, recomputes the live registration graph and requires the exact stored fingerprint. Repair outcomes map only to the already-bounded automatic repair kinds. Revocation, erasure, ambiguity and security concerns are not silently repaired. Reviewer routes never issue patient session, device or consent authority.

## Finding 010 — migration-head and audit contracts lagged the Slice 9A schema

Backend CI #500 showed seven pure-unit failures after the new migration became the actual repository head. Ruff was green; the failures were stale migration-head, audit-event and pre-durable-reference assertions.

**Decision / status: RESOLVED IN CLOSURE CHANGESET; FINAL CI RECEIPT IN PR #43.** CI preparation/release tooling, migration graph tests, provider-trust ancestry tests, audit vocabulary, recovery-review contract guards, pilot operations documentation and current-state documentation are reconciled to `20260910_registration_recovery_review`. Historical security-governance policy text is not silently rewritten merely to chase a head string; the deployment test continues enforcing its substantive no-auto-migration invariant while current operational head authority lives in the current-state/constitution/release tooling.

## Finding 011 — terminal audit key exceeds the durable outbox limit

Backend CI #529 and a local fresh PostgreSQL reproduction failed because the
terminal key repeated a case UUID already bound into its full operation digest,
producing 139 characters for a `VARCHAR(128)` column.

**Decision / status: RESOLVED IN CODE AND FOCUSED QUALIFICATION; FINAL CI RECEIPT IN PR #43.** Keep the existing
schema and full SHA-256, using `registration-recovery-review:terminal:<digest>`
(102 characters). Regression coverage asserts the unchanged database limit,
full digest, one disposition/audit across concurrent retries and 192-character
input idempotency keys. A real PostgreSQL NOT NULL audit failure proves rollback.

## Finding 012 — exact terminal replay bypassed the claimed-session check

The durable replay return preceded assignment/session validation, allowing a
second session for the same reviewer to retrieve the original terminal result.

**Decision / status: RESOLVED IN CODE AND FOCUSED QUALIFICATION; FINAL CI RECEIPT IN PR #43.** Check assignment
and session before replay lookup. PostgreSQL tests deny the other reviewer and
another session, preserve exact replay, and exercise nonterminal session recovery.

## Finding 013 — mutation schemas silently ignored unknown fields

**Decision / status: RESOLVED IN CODE AND FOCUSED QUALIFICATION; FINAL CI RECEIPT IN PR #43.** Mutation schemas
now forbid extra fields; eight parameterized regressions reject client-supplied
patient, reviewer, session and authority-version fields on both request models.

## Final merge gate

The implementation includes patient status, reviewer list/detail, claim, reviewer-session recovery, terminal resolution, transactional audit coupling, and disposable PostgreSQL race/rollback qualification. No completion or merge claim is valid until Backend and Frontend CI are both green on one frozen PR #43 head and that exact reviewed head is merged.
