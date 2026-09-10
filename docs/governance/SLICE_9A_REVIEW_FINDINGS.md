# Slice 9A — Rolling Review Findings

Status: **ACTIVE REVIEW LOG**

Base: `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`

This file is updated whenever implementation review discovers a material authority, schema, concurrency, privacy, or qualification finding. Findings are recorded before proceeding to the dependent step.

## Finding 001 — reviewer authority version not persisted

The Slice 9A governance contract requires the assigned reviewer authority version to be durable, but the initial case model/migration only persisted `assigned_reviewer_id` and `assigned_reviewer_role`.

**Decision:** add `reviewer_authority_version` to `IN_REVIEW` and terminal cases, require it to be null while `PENDING`, and persist the exact server-owned authorization policy version used at claim time. Terminal resolution must verify the current reviewer authorization remains valid; the stored version records the authority contract under which the claim occurred.

## Finding 002 — reason-code vocabulary not closed in PostgreSQL

The Python model defines a closed `RegistrationRecoveryReviewReason` enum, but the initial migration only requires `cardinality(reason_codes) > 0`. Direct SQL could therefore persist unknown reason strings.

**Decision:** add PostgreSQL CHECK constraints restricting case and disposition reason arrays to the closed server-owned vocabulary and requiring them to be non-empty. Application validation will remain in addition to the database constraint.

## Finding 003 — claimed case is not bound to the reviewer session

The initial case model records reviewer identity but not the authenticated reviewer session. A second concurrent/stolen session belonging to the same provider identity could otherwise attempt terminal mutation without proving continuity with the session that claimed the case.

**Decision:** add a one-way `review_session_binding` SHA-256 value to `IN_REVIEW` and terminal cases. It is derived only from the current server-authenticated provider session, never supplied as trusted input by the client. Claim stores it; session recovery may rotate it only through an explicit high-risk operation by the same authorized reviewer; terminal disposition requires an exact constant-time match.

## Review rule

Do not proceed to reviewer mutation routes until Findings 001–003 are fixed in both ORM model and migration and the resulting diff is reviewed.
