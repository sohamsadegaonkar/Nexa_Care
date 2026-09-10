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

## Current review gate

The reconstructed schema and reviewer authorization artifacts must be reviewed against the new parent baseline before Step 2 case creation is implemented. Any new finding is added here first.
