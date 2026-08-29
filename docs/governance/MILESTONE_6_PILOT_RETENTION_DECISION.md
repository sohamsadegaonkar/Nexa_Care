# DRAFT — NOT APPROVED — NOT IN EFFECT

> **DO NOT IMPLEMENT AS AN S3 LIFECYCLE POLICY.**
>
> This document records an unresolved Milestone 6 pilot retention decision.
> It is not an approved retention policy and establishes no production,
> hospital-record, clinical, or statutory retention requirement.

## Decision status

Status: **PENDING APPROVAL**

Milestone: Milestone 6 physical Android / Textract qualification

Environment: Isolated pilot qualification environment

Real patient PHI: **PROHIBITED**

## Scope

This decision applies only to objects created for the isolated Nexa Care
Milestone 6 qualification environment.

It does not establish or imply:

- production retention requirements;
- hospital medical-record retention requirements;
- statutory or regulatory retention periods;
- clinical-record retention requirements;
- retention policy for any future customer or facility.

Production and hospital retention require separately approved legal,
privacy, security, clinical, and facility-specific decisions as applicable.

## Authority

### Operational / pilot owner

Name: Soham Sadegaonkar

Authority for this record:
Operational ownership of the Milestone 6 qualification and determination
of the qualification/evidence review window.

Operational approval status: **APPROVED — 14-DAY EVIDENCE-REVIEW WINDOW**

Operational decision date: **2026-08-29**

This approval covers ONLY the operational evidence-review window.

It does NOT approve the S3 lifecycle policy.

It does NOT constitute security approval.

It does NOT constitute privacy/legal approval.

### Security reviewer

Name: **UNASSIGNED**

Approval status: **PENDING**

A named human security reviewer must approve the storage-security
implications before this decision can become effective.

### Privacy / legal reviewer

Name: **UNASSIGNED**

Approval status: **PENDING**

A qualified or otherwise authorized privacy/legal reviewer must approve
the retention boundary and confirm that this pilot-only decision is not
represented as a production, hospital, clinical, or statutory retention
precedent.

## Qualification evidence window

Required evidence-review duration: **14 days**

The operational owner must determine how long qualification artifacts may
reasonably need to remain available for:

- Milestone 6 sign-off;
- failed-run investigation;
- reruns;
- security review;
- audit/evidence review;
- challenge or reproduction of qualification results.

The lifecycle expiration period is not approved merely because the operational
evidence window is known.

## Proposed lifecycle configuration

The following values are proposals only and MUST NOT be implemented while
this document remains PENDING:

- Current object expiration: **30 days — PROPOSED**
- Noncurrent version expiration: **7 days — PROPOSED**
- Incomplete multipart upload cleanup: **7 days — PROPOSED**

The 14-day evidence-review window is the operational requirement. The proposed
30-day current-object expiration would provide a 16-day buffer beyond that
operational window. That observation does NOT constitute security,
privacy/legal, or final lifecycle approval. These values are engineering
proposals, not statutory or legal retention requirements.

## Interim accumulation control

While lifecycle approval remains pending:

1. Pilot traffic remains disabled.
2. Routine document ingestion remains disabled.
3. No real patient PHI may be placed in the qualification bucket.
4. Any manually created qualification objects must remain identifiable as
   qualification data.
5. Existing object accumulation must be reviewed before enabling pilot traffic.
6. Pending approval must not be interpreted as authorization for indefinite
   retention.
7. When an approved lifecycle policy is established, existing qualification
   objects must either fall under that policy or be explicitly dispositioned.

## Approval gates

This decision becomes effective only when all of the following are true:

- [x] Operational owner has approved the evidence-review window.
- [ ] Final lifecycle durations have been selected.
- [ ] Named security reviewer has approved.
- [ ] Named privacy/legal reviewer has approved.
- [ ] Pilot-only / no-production-precedent boundary has been approved.
- [ ] Document status changed from DRAFT / PENDING to APPROVED.
- [ ] S3 lifecycle configuration has been applied.
- [ ] Applied lifecycle configuration has been read back and verified.

Until every applicable approval above is complete:

**DO NOT CONFIGURE THE S3 LIFECYCLE RULE.**

## Production applicability

**NONE.**

This Milestone 6 qualification decision establishes no production,
hospital-record, clinical-record, contractual, statutory, or regulatory
retention precedent.
