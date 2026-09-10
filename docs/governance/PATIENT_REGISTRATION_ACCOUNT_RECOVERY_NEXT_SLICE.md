# Next Slice — Durable Manual Patient Registration-Recovery Cases

Status: **mapped; not implemented in this branch**

This is the immediate follow-on to patient registration-account self-service recovery. It exists because several historical account states are intentionally not repairable from phone OTP possession alone.

## Trigger states

The next slice must provide durable handling for server-classified manual-review conditions including identity revocation, erasure state, missing linked patient rows, unexplained soft deletion, multiple Supabase identities, merge-cycle/depth ambiguity, unavailable canonical patient, canonical erasure state, and canonical identity conflict.

## Required separation

A recovery case is not itself authority to clear revocation, undo erasure, merge identities, create a patient session, or grant device authority. Resolution must be reason-specific and must reuse only existing operator authority that is explicitly appropriate for the action.

## Minimum lifecycle

The slice should define a durable case state machine such as `OPEN -> CLAIMED -> RESOLVED` with explicit terminal outcomes and optimistic/concurrency controls. Case creation occurs only after fresh patient identity proof and server classification `manual_review`.

The patient-facing status surface should expose only coarse state and next action through a non-account-dependent opaque status capability; it must not leak internal graph details or operator notes.

Operator APIs require authenticated privileged context, auditable claim/release/resolution operations, graph revalidation under lock, and reason-specific policy. Erasure-related cases must remain fail-closed and cannot be converted into ordinary account restoration by a generic approval action.

## Qualification

The slice requires its own migration, PostgreSQL concurrency/adversarial tests, operator-auth tests, patient status-capability tests, audit-outbox coverage, route-registry updates, governance review, and exact-head CI before merge.

A separate later slice remains mapped for the patient first-time registration UI. It must not be folded into manual recovery-case authority.
