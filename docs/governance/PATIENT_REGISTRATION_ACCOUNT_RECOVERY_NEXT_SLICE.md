# Historical Map — Durable Manual Patient Registration-Recovery Cases

Status: **SUPERSEDED BY SLICE 9A IMPLEMENTATION IN PR #43**

This document originally mapped the immediate follow-on to patient registration-account self-service recovery. The implemented authority contract now lives in `SLICE_9A_REGISTRATION_RECOVERY_REVIEW.md`; this file remains only as historical planning context.

The original trigger states remain intentionally outside phone-OTP-only self-service repair: identity revocation, erasure state, missing linked patient rows, unexplained soft deletion, multiple Supabase identities, merge-cycle/depth ambiguity, unavailable canonical patient, canonical erasure state, and canonical identity conflict.

The permanent separation remains:

`phone OTP proof != recovery case != reviewer authority != repair authorization != patient session != device authority != consent authority`

Slice 9A implements a durable closed case lifecycle, opaque patient status, dedicated reviewer authorization, reviewer/session assignment, optimistic versions, idempotent terminal dispositions, graph revalidation under the shared registration-recovery PostgreSQL lock domain, transactional audit coupling, route-registry coverage and disposable PostgreSQL concurrency qualification.

Erasure, revocation, ambiguity and security concerns remain fail-closed; a generic reviewer approval cannot silently resurrect them. Reviewer routes do not issue patient login, device or consent authority.

Any future patient first-time registration UI remains a separate product/UI concern and must consume the server authority contracts rather than redefining them.
