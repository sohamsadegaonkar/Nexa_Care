# Slice 10B.5b — Canonical Encounter Binding

Status: **IMPLEMENTATION IN PROGRESS**

## Repository checkpoint

- Starting main: `337c8229de2267aaa1a07410833a57eaf040411c`
- Branch: `task0/10b5b-canonical-encounter`
- Current Alembic head at start: `20260916_patient_external_record_import`
- Canonical Encounter before this slice: **MISSING**
- Active parallel PRs/branches observed at start: none; only `main` existed remotely.

## Bounded objective

Materialize the already-qualified server-generated Treatment Session V1 encounter correlation as one canonical durable Encounter, without enabling prescription, diagnosis, vitals, notes, laboratory, allergy, investigation, document, or other legacy clinical writes.

## Affected security invariants

- default deny and exact Treatment Session V1 operation membership;
- server-derived patient/provider/hospital authority;
- provider-session binding;
- Redis + PostgreSQL fail-closed treatment authority;
- expiry and revocation;
- no client-selected encounter identifier;
- one canonical encounter per ClinicalAccessSession;
- transactional clinical mutation + durable audit outbox;
- one linear Alembic head;
- Signed Consent V3 remains read-only;
- no clinical facts are fabricated by encounter creation.

## Data / consent / access / audit impact

- Patient data: adds only structural encounter authority binding; no clinical payload.
- Consent: no consent protocol change and no Signed Consent V3 widening.
- Access: requires `ClinicalAccessOperation.CREATE_ENCOUNTER`.
- Audit: canonical encounter creation requires a structural, value-free durable audit event in the same transaction.
- Identity disclosure: no new identity evidence disclosure.
- AI/extraction/storage/erasure/emergency: unchanged.
- Database: one minimal canonical Encounter table is expected because no canonical Encounter persistence currently exists.

## Intended authority graph

```text
authenticated provider + current hospital/session
        ↓
X-Treatment-Token
        ↓
require_clinical_session(CREATE_ENCOUNTER)
        ↓
server-reserved ClinicalAccessSession.encounter_id
        ↓
canonical Encounter with the same UUID
```

The client supplies no patient/provider/hospital/encounter authority.

## Migration position

If implemented, the migration must be the single linear child of:

```text
20260916_patient_external_record_import
```

No sibling or Alembic merge revision is permitted.

## Explicit non-scope

- prescription writes;
- diagnoses;
- vitals;
- clinical notes;
- investigation orders;
- lab/allergy/document writes;
- frontend treatment workflow;
- Task 1 patient external-record lifecycle;
- Task 2 patient longitudinal UX;
- Signed Consent V3 changes;
- Treatment Session V1 cryptographic protocol changes.

## Required qualification

Focused treatment-session gate and canonical-encounter tests, migration graph and PostgreSQL migration qualification, audit-event coverage, static proof that legacy clinical writes remain unwired, then exact-head repository Partitions A/B/C with zero skips plus frontend/native/Vercel checks required by repository CI.

## Open design decisions resolved

1. Canonical Encounter entity: **MISSING** on starting main; implement the smallest authority container.
2. Encounter identifier: reuse the already-qualified server-generated correlation UUID.
3. Idempotency: one ClinicalAccessSession maps to one Encounter; serialize on the session row and enforce a unique database constraint.
4. Clinical content: none in this slice.
