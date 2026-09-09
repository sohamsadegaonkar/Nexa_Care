# Slice 8E — Operational Audit and Retention Evidence

Status: **OPERATIONAL EVIDENCE GATE IMPLEMENTED; LIVE DATABASE VERIFICATION BLOCKED BY DATABASE WIRING; RETENTION APPROVALS PENDING**

Authoritative base: `bc37cd6233f8297e9dacc2925c7caa44d67ed733`

Qualification branch: `slice-8e-operational-audit-retention-evidence`

## Purpose

Slice 8E makes the already-qualified audit-integrity verifier executable against
an authorized operational/pilot database without changing audit-chain state or
exposing protected ledger material. It does not automate human retention
approval and does not configure object-storage lifecycle policy.

## Permanent operational evidence gate

`.github/workflows/operational-audit-integrity.yml` runs in the protected
`pilot` GitHub environment and accepts the first configured database secret from:

1. `NEXA_PILOT_AUDIT_DATABASE_URL`
2. `NEXA_PILOT_DATABASE_URL`
3. `DATABASE_URL`

The workflow fails closed unless the URL uses the repository's asynchronous
PostgreSQL driver shape `postgresql+asyncpg://...`.

When authorized wiring exists it invokes only:

`python scripts/verify_audit_integrity_evidence.py`

That wrapper calls the canonical partition-aware verifier with `dry_run=True`.
The workflow additionally enforces the exact sanitized evidence schema and
rejects any evidence shape that could contain raw audit payloads, hashes, or
event identifiers.

## Real operational attempt

The first same-repository attempt was executed rather than inferred:

- workflow: `Operational Audit Integrity Qualification`
- run: `34399872307`
- source head: `b5a100b23c362a76dfa9311c97330cef1d77d54b`
- result: fail-closed blocker
- observed marker:
  `OPERATIONAL_AUDIT_QUALIFICATION=BLOCKED_MISSING_DATABASE_WIRING`
- observed `DATABASE_URL`: empty
- verifier connection attempted: **NO**
- database mutation performed: **NO**
- audit-chain health mutation performed: **NO**
- retention mutation performed: **NO**

The run therefore provides concrete evidence that the current blocker is
protected-environment database wiring, not an unexecuted repository assumption.
It is not an audit-integrity PASS.

## Retention boundary

`docs/governance/MILESTONE_6_PILOT_RETENTION_DECISION.md` remains authoritative:

- `DRAFT — NOT APPROVED — NOT IN EFFECT`
- security reviewer approval: pending
- privacy/legal reviewer approval: pending
- final retention durations: not approved
- S3 lifecycle application/read-back: NOT_RUN
- `DO NOT CONFIGURE THE S3 LIFECYCLE RULE`

The 8E workflow asserts those non-mutation markers. It cannot and must not mint
human approvals.

## Completion conditions

Operational audit evidence becomes qualified only after an authorized database
secret is wired into the protected `pilot` environment and the workflow emits:

- evidence schema `nexa-slice-7e-audit-integrity-evidence-v1`;
- `status=PASS`;
- `dry_run=true`;
- `scope=all-partitions`;
- zero raw audit payload inclusion;
- zero raw hash/event-ID inclusion.

Retention deployment can proceed only after genuine named security and
privacy/legal approvals establish final durations. Only then may a separate
controlled lifecycle application/read-back exercise be executed.

## Nonclaims

This slice does not claim:

- operational database integrity PASS;
- production database access;
- retention approval;
- S3 lifecycle configuration;
- legal or privacy sign-off;
- production deployment.
