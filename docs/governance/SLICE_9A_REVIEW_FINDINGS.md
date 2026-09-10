# Slice 9A — Rolling Review Findings

Status: **ACTIVE REVIEW LOG**

Base: `54351f9a55ba94665420961cfe766bdcc84a5398`

This file is updated whenever implementation review discovers a material authority, schema, concurrency, privacy, migration, or qualification finding. Findings are recorded before proceeding to dependent work.

## Finding 001 — reviewer authority version not persisted

The initial case schema persisted reviewer identity/role but not the exact server-owned reviewer authority version.

**Decision / status: RESOLVED.** `reviewer_authority_version` is required for claimed and terminal cases and is also recorded on terminal disposition rows.

## Finding 002 — reason-code vocabulary not closed in PostgreSQL

The Python model defined a closed reason enum while the initial database constraint only required a non-empty array.

**Decision / status: RESOLVED.** PostgreSQL case/disposition constraints now require non-empty arrays whose values are subsets of the closed server-owned recovery-review reason vocabulary.

## Finding 003 — claimed case was not bound to the reviewer session

Reviewer identity alone would allow another concurrent session for the same provider identity to attempt terminal mutation.

**Decision / status: RESOLVED.** Claimed and terminal cases persist a one-way SHA-256 `review_session_binding`, derived only from the current authenticated provider session. Terminal mutation must match it; explicit reviewer-session recovery may rotate it only under dedicated high-risk semantics.

## Finding 004 — Slice 9A depended on an unmerged patient-facing recovery workflow

Step 2 requires the patient-facing flow to verify the external identity and classify the registration graph as manual-review-required.

**Decision / status: RESOLVED AS A DEPENDENCY.** PR #40 was independently reviewed, qualified, and exact-head merged. `main` now contains that workflow at merge commit `54351f9a55ba94665420961cfe766bdcc84a5398`. This Slice 9A branch was recreated directly from that verified `main`; the stale branch was not merged into the new lineage.

The dependency order is now satisfied:

`explicit recovery boundary -> verified patient recovery/classification -> durable manual-review cases -> reviewer repair authority`.

## Finding 005 — parent classifier reasons do not equal the durable review vocabulary

The merged patient registration-recovery classifier emits fine-grained internal reason codes such as `LINKED_PATIENT_MISSING`, `MULTIPLE_SOURCE_IDENTITIES`, merge-cycle/depth/tombstone failures, canonical-patient unavailability, canonical erasure, and canonical identity conflict. The durable Slice 9A schema intentionally accepts a smaller closed review vocabulary.

Directly persisting the classifier string would either violate the database contract or tempt the review schema to grow around implementation-specific reason text.

**Decision / status: REQUIRED FOR STEP 2.** Add one server-owned normalization function at the case-creation boundary. Exact mappings are:

- `ERASURE_STATE_PRESENT`, `CANONICAL_ERASURE_STATE_PRESENT` -> `ERASURE_STATE_PRESENT`;
- `IDENTITY_REVOKED` -> `IDENTITY_REVOKED`;
- `MULTIPLE_SOURCE_IDENTITIES`, `CANONICAL_IDENTITY_CONFLICT` -> `MULTIPLE_IDENTITIES`;
- `DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE` -> `PATIENT_DELETED_WITHOUT_MERGE`;
- `MERGE_TOMBSTONE_CYCLE`, `MERGE_TOMBSTONE_CHAIN_TOO_DEEP`, `CANONICAL_PATIENT_UNAVAILABLE` -> `MERGE_AMBIGUOUS`;
- `LINKED_PATIENT_MISSING` and any unknown future internal manual-review reason -> `SECURITY_CONCERN`.

The patient-facing API must expose only the durable case reference/status and must not expose the provider subject, graph fingerprint, or internal classifier reason.

## Finding 006 — provider-subject hash alone is not a sufficient future graph anchor

The initial case schema stored a privacy-safe `provider_subject_hash` plus an optional candidate `patient_id`. That is not enough to deterministically re-lock and recompute the same external-identity graph at terminal review time. In particular, the candidate patient may be missing, retired by merge, or changed after case creation, while the raw provider subject is deliberately not persisted in the case.

Scanning all auth identities and comparing hashes would be inefficient, creates an unnecessary privacy surface, and would make the repair path depend on global-table enumeration. Trusting the stale candidate patient UUID would violate the requirement to recompute authority from current durable state.

**Decision / status: REQUIRED BEFORE STEP 2.** Persist the stable non-secret `PatientAuthIdentity.identity_id` as a non-null foreign-key anchor on every review case. Case creation resolves the identity from the already-verified provider subject, verifies its provider/subject binding, stores only the UUID identity anchor plus the provider-subject hash, and never stores the raw provider subject. Future review/repair starts from this identity row under lock and recomputes the graph from current state.

## Current review gate

Reconstruction review is clean: the new branch is based directly on the verified post-PR-40 `main`, is 0 commits behind, and changes only the intended Slice 9A artifacts. Step 2 may proceed only after Findings 005 and 006 are implemented with focused tests; Step 2 itself must be reviewed before Step 3 begins.
