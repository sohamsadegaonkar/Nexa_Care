# Slice 10B.5b — Canonical Encounter Binding

Status: **QUALIFIED — GOVERNANCE FINALIZATION TIP REQUIRES EXACT-HEAD REQUALIFICATION BEFORE MERGE**

## Repository checkpoint

- Starting main: `337c8229de2267aaa1a07410833a57eaf040411c`
- Branch: `task0/10b5b-canonical-encounter`
- PR: #54, `security(task0): add canonical Treatment Session encounter boundary`
- Original exact qualified implementation head: `77eb7ce4cdc0f53d7626e5a8ba60515425a8b56a`
- Canonical Encounter before this slice: **MISSING**
- Alembic head produced by this slice: `20260918_canonical_encounter`
- Migration parent: `20260916_patient_external_record_import`

## Bounded objective

Materialize the already-qualified server-generated Treatment Session V1 encounter correlation as one canonical durable Encounter, without enabling prescription, diagnosis, vitals, notes, laboratory, allergy, investigation, document, or other legacy clinical writes.

## Qualified runtime boundary

The implementation at `77eb7ce4cdc0f53d7626e5a8ba60515425a8b56a` is qualified to provide exactly this boundary:

```text
authenticated provider + current hospital/session
        ↓
X-Treatment-Token
        ↓
require_clinical_session(CREATE_ENCOUNTER)
        ↓
durable ClinicalAccessSession revalidation + row lock
        ↓
durable ConsentGrant revalidation + row lock
        ↓
server-reserved ClinicalAccessSession.encounter_id
        ↓
one canonical clinical_encounters row
        ↓
value-free durable audit outbox in the same transaction
```

The client supplies no independent patient/provider/hospital/Encounter authority.

## Security invariants preserved

- default deny and exact Treatment Session V1 operation membership;
- exact `CREATE_ENCOUNTER` authority is required;
- server-derived patient/provider/hospital identity;
- current provider-session binding;
- Redis + PostgreSQL fail-closed treatment authority;
- ClinicalAccessSession durable revalidation;
- ConsentGrant durable revalidation;
- expiry and revocation checks remain active;
- no client-selected Encounter identifier;
- one Encounter per ClinicalAccessSession, database-enforced;
- transactional structural/value-free audit;
- one linear Alembic head;
- Signed Consent V3 remains read-only;
- no clinical facts are fabricated by Encounter creation.

## Data / consent / access / audit impact

- Patient data: structural Encounter authority binding only; no clinical payload.
- Consent: no consent protocol change and no Signed Consent V3 widening.
- Access: requires `ClinicalAccessOperation.CREATE_ENCOUNTER`.
- Audit: `CLINICAL_ENCOUNTER_CREATED` is structural/value-free and staged in the same transaction as Encounter creation.
- Identity disclosure: no new identity-evidence disclosure.
- AI/extraction/storage/erasure/emergency behavior: unchanged.
- Database: adds the minimal `clinical_encounters` authority container.

## Migration position

Current Task-0 Alembic head:

```text
20260918_canonical_encounter
```

Parent:

```text
20260916_patient_external_record_import
```

The migration remains a single linear child. No sibling head or Alembic merge revision is introduced.

## Exact qualification evidence for implementation head

Qualified implementation head:

```text
77eb7ce4cdc0f53d7626e5a8ba60515425a8b56a
```

Backend CI:

- Workflow run: `35374510275` / run #709
- Ruff: **PASS**
- Partition A: **PASS**
- Partition A zero-skip assertion: **PASS**
- Partition B: **PASS**
- Partition B zero-skip assertion: **PASS**
- Partition C: **PASS**
- Partition C zero-skip assertion: **PASS**

Frontend/native CI:

- Workflow run: `35374510190` / run #658
- web/frontend tests: **PASS**
- Next production build: **PASS**
- workspace build: **PASS**
- Android native compile: **PASS**
- iOS native compile: **PASS**

Vercel:

- exact-head status: **SUCCESS**

Focused repository coverage included canonical Encounter contracts, Treatment Session gate coverage, migration graph/current-head assertions, route registration, and audit-event coverage within the repository qualification matrix.

## Exact non-wiring boundary

Slice 10B.5b authorizes **only canonical Encounter creation**.

It does **not** wire Treatment Session V1 authority into:

- prescription writes;
- diagnosis writes;
- vitals writes;
- clinical notes;
- laboratory writes;
- allergy writes;
- investigation orders;
- document writes;
- any other legacy clinical mutation route.

A canonical Encounter is an authority container/correlation boundary. It is **not** generic clinical write authority.

A valid Treatment Session token is **not** generic mutation authority.

Signed Consent V3 remains read-only.

## Concurrent workstreams at governance finalization

Current `origin/main` at the time of this governance correction:

```text
337c8229de2267aaa1a07410833a57eaf040411c
```

Concurrent PRs:

- PR #53 — Task 1 external-record D3, current head `21be606ac4e18c786cd4b2424afc3bd4ced04e87`, **OPEN / DRAFT / qualified on its own pre-#54 base**.
- PR #55 — Task 2 Slice 11D patient-records UX, current head `8171d261d1fd557c1dbe67f71bdc9fbf35c328af`, **OPEN**.

PR #53 and PR #54 both touch the integration-controlled files:

- `tests/test_route_registration.py`
- `tests/test_audit_event_coverage.py`

After #54 merges, Task 1 must reconcile those files from the new main and preserve both the canonical Encounter assertions and D3 retry/cancel assertions before Task 1 can be merged.

## Future Treatment Session write rule

No future write operation inherits qualification merely because 10B.5b is qualified.

Each future operation must be designed, implemented, and **independently qualified** against:

- authenticated provider identity;
- live provider trust;
- exact patient-signed Treatment Session operation;
- current hospital/session binding;
- the same ClinicalAccessSession;
- the canonical Encounter bound to that session;
- durable authority revalidation;
- transactional audit;
- typed clinical validation;
- exact-head CI evidence.

Operation names do not justify inventing missing clinical entities.

## Governance-finalization note

This documentation correction intentionally changes no runtime, model, migration, route, test, or CI code.

Because this governance-only commit changes the PR head SHA, the new exact branch tip must still pass the full required qualification matrix before PR #54 is merged. The exact commit merged into `main` must be green.
