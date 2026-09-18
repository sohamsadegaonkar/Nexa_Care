# Slice 10B.5c — First Bounded Treatment Session V1 Clinical Write

Status: **DESIGN FROZEN / IMPLEMENTATION STARTING — NOT QUALIFIED / NOT MERGEABLE YET**

## Repository checkpoint

- Starting main: `a9b221031256393602b42d3cf93f2af5ec402b5f`
- Branch: `task0/10b5c-first-clinical-write`
- Starting Alembic head: `20260918_canonical_encounter`
- Task 0 10B.5b canonical Encounter: **MERGED**
- Task 1 PR #53: open/draft and reconciling after #54
- Task 2 PR #55: open and must reconcile current main before merge

Task 0 must not edit these integration-controlled files while PR #53 reconciles:

- `tests/test_route_registration.py`
- `tests/test_audit_event_coverage.py`

## Objective

Enable exactly one bounded Treatment Session V1 clinical mutation domain after the
canonical Encounter boundary. Do not enable multiple write families.

The first selected operation is:

```text
WRITE_VITALS
```

The first API contract will write **one typed vital observation per request**.
It will not reuse the legacy multi-field vitals request unchanged.

## Repository audit

### Vitals — SELECTED

Existing typed model:

```text
patient_vitals
- id
- patient_id
- type
- value
- unit
- recorded_at
- source
- confidence
- risk_level
- source_document_id
```

Existing operation vocabulary contains the exact semantic operation:

```text
WRITE_VITALS
```

Existing longitudinal-record and timeline projections already understand Vitals.

Gap: the model currently has no canonical Encounter FK, so a Treatment Session
write cannot yet prove durable same-session Encounter binding from the stored
clinical row.

### Medication — DEFER

A `Medication` model and legacy append route exist, but the Treatment Session
operation is `WRITE_PRESCRIPTION`.

The repository does not yet prove that every Medication row is a canonical
prescription or that the existing model captures the authority/authorship
semantics required for a prescription. Do not equate these concepts merely to
satisfy an operation name.

### LabResult — DEFER

A typed `LabResult` model exists.

The Treatment Session vocabulary contains `ORDER_INVESTIGATION`, not a generic
write-lab-result operation. An investigation order and a laboratory result are
different clinical facts. Do not wire one as the other.

### Allergy — DEFER

A typed `Allergy` model exists, but there is no Treatment Session operation
whose existing semantics explicitly authorize an allergy mutation.

### DocumentReference — DEFER

DocumentReference exists, but document upload/extraction/review already has a
separate authorization and provenance architecture. It is not a generic
Treatment Session clinical write.

### Diagnosis / Clinical Note — MISSING

No canonical dedicated Diagnosis or ClinicalNote typed entity was found during
the starting-main audit. `WRITE_DIAGNOSIS` and `WRITE_CLINICAL_NOTES` operation
names do not authorize inventing persistence models merely to make those
operations executable.

## Legacy vitals route audit

Current legacy provider routes:

```text
POST /api/v2/patient/{id}/record/vitals
POST /api/v2/patient/{id}/records/vitals
```

They currently:

- accept a caller-selected patient path identifier;
- depend on legacy `X-Consent-Token` / `clinical_append` consent;
- accept `payload.encounter_id` but do not use it as canonical authority;
- accept several measurements in one request;
- persist only one BP `Vitals` row;
- place heart rate / temperature / SpO2 only in a TimelineEvent summary;
- are not wired to Treatment Session V1.

Therefore 10B.5c must not convert this legacy route in place or treat it as the
new Treatment Session write boundary.

## Frontend audit

The current frontend client method:

```text
appendVitals(patientId, payload, consentToken)
```

posts to the legacy patient-ID route and sends `X-Consent-Token`.

It also requires a client `encounter_id`.

This is not ready for Treatment Session V1 and must not be treated as proof of
frontend readiness. 10B.5c backend authority is independent of that legacy
client flow.

## Required authority graph

The first Treatment Session vitals write must require:

```text
authenticated provider
+ current live provider trust / clinical eligibility
+ X-Treatment-Token
+ exact patient-signed WRITE_VITALS operation
+ matching current hospital/provider session
+ exact ClinicalAccessSession
+ canonical Encounter from that exact session
+ durable ClinicalAccessSession revalidation
+ durable ConsentGrant revalidation
+ typed vital validation
+ durable idempotency
+ timeline projection
+ transactional structural audit
```

No client-selected:

- patient ID;
- provider ID;
- hospital ID;
- clinical session ID;
- canonical Encounter ID.

A canonical Encounter is not itself write authority.

A valid Treatment Session token is not generic mutation authority.

Signed Consent V3 remains read-only.

## Persistence design

The existing `Vitals` model remains the canonical vitals source of truth.

10B.5c should add a nullable `encounter_id` FK to `patient_vitals`:

- UUID;
- FK to `clinical_encounters.encounter_id`;
- `ON DELETE RESTRICT`;
- indexed;
- nullable for historical / legacy rows;
- no backfill.

Every new Treatment Session V1 vitals write must populate it.

This avoids creating a duplicate treatment-vitals table while preserving
historical legacy rows.

## Request semantics

One request creates one typed observation.

The request must not accept patient/provider/hospital/session/Encounter
authority or provenance fields.

Initial supported typed observations should be limited to the existing Vitals
domain with server-owned unit/type mapping. Blood glucose requires separate
unit/context semantics and should remain deferred unless the repository
establishes an unambiguous contract.

No request may label a value normal/abnormal, diagnose, recommend treatment, or
infer clinical meaning.

## Provenance

Treatment Session provider entry should use the repository's existing manual
clinician-entry provenance rather than inventing a new provenance vocabulary in
this slice:

```text
source = manual
confidence = null
source_document_id = null
```

No AI confidence is fabricated.

## Durable idempotency

Use the existing `public.mutation_idempotency` boundary.

Suggested operation name:

```text
treatment.write_vitals.v1
```

Scope the durable key to the server-derived hospital / operation / idempotency
key and include the exact session, patient, Encounter, observation type, typed
value, and recorded time in the canonical request hash.

Same key + same canonical request returns the original result.

Same key + different semantic request fails closed.

The clinical mutation, timeline event, success audit outbox row, and completed
idempotency result must share one database transaction.

## Audit

Prefer the existing structural `PATIENT_RECORD_APPEND_SUCCESS` event unless a
new operation-specific event is proven necessary.

Audit metadata must remain value-free. Do not put the vital value, BP numbers,
temperature, SpO2, clinical interpretation, token, or raw request in audit
metadata.

## Timeline

A successful write should create a timeline event referring to the actual Vitals
row through `event_ref_id`.

Timeline clinical summary may contain the clinical observation because it is
patient clinical data, not an audit/log payload. It must not imply diagnosis or
normality.

## Migration plan

Expected next revision, subject to final post-Task-1 reconciliation:

```text
20260918_treatment_vitals_encounter
```

Expected parent:

```text
20260918_canonical_encounter
```

Before final migration qualification, re-check the actual single head from the
post-PR-#53 main. Never create a sibling head or Alembic merge revision.

## Concurrency plan

While PR #53 reconciles:

Allowed Task 0 work:

- this design/audit document;
- a dedicated migration/model change on the Task 0 branch;
- dedicated Treatment Session vitals authority/service code;
- dedicated unit/PostgreSQL tests;
- other non-overlapping Task 0 runtime code.

Deferred until Task 1 is reconciled/merged:

- edits to `tests/test_route_registration.py`;
- edits to `tests/test_audit_event_coverage.py`;
- final exact-head integration/qualification;
- final merge.

Before final 10B.5c integration, update from the post-#53 authoritative main and
semantically reconcile any shared changes.

## Non-scope

10B.5c does not authorize:

- prescriptions;
- diagnoses;
- clinical notes;
- labs / investigation results;
- investigation orders;
- allergy changes;
- document mutation;
- multiple write-operation families;
- treatment recommendations;
- dosage recommendations;
- autonomous interpretation;
- Signed Consent V3 write authority.

## Qualification required before merge

The final exact head must prove, at minimum:

- wrong operation denied;
- missing/expired/revoked/tampered Treatment Session denied;
- wrong provider/session/hospital denied;
- cross-patient authority impossible;
- client Encounter injection impossible;
- missing/mismatched canonical Encounter denied;
- durable session/grant disagreement denied;
- Encounter/session/patient/provider/hospital mismatch denied;
- same idempotency key + same request returns original result;
- same key + changed request denied;
- concurrent duplicate request cannot duplicate a vital;
- audit staging failure rolls back the clinical row, timeline, and idempotency completion;
- legacy Signed Consent write route semantics remain separate;
- no other Treatment Session write becomes active.

Then run exact-head Ruff, focused tests, migration graph, PostgreSQL qualification,
Partitions A/B/C with zero skips, frontend/web/Next/workspace, Android, iOS, and
Vercel as required by repository CI.
