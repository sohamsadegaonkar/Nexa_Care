# Slice 10B.5c — First Bounded Treatment Session V1 Clinical Write

Status: **POST-#53 ROUTE INTEGRATED — DIAGNOSTIC / WAITING ON TASK-2 RELEASE RECONCILIATION / NOT FINAL**

## Repository checkpoint

- Starting main: `a9b221031256393602b42d3cf93f2af5ec402b5f`
- Branch: `task0/10b5c-first-clinical-write`
- Starting Alembic head: `20260918_canonical_encounter`
- Task 0 10B.5b canonical Encounter: **MERGED**
- Task 1 PR #53: **MERGED** as main commit `0afc5d24e7383fb91cb20ba5dd67c739bb9de40f`
- Task 0 post-#53 reconciliation merge: `26aab84b2f062649e8cd59c1a552952dd74f0dd7`
- Task 2 PR #55: open; final exact-head release qualification remains deferred until its integration state is resolved

The post-#53 integration gate is open. Task 0 may now mount the bounded vitals router and update the shared route catalog. `PATIENT_RECORD_APPEND_SUCCESS` is already present in `tests/test_audit_event_coverage.py`, so no new audit vocabulary or redundant audit-catalog edit is required.

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

While PR #53 remains frozen but unmerged:

Allowed Task 0 work:

- this design/audit document;
- a dedicated migration/model change on the Task 0 branch;
- dedicated Treatment Session vitals authority/service code;
- dedicated unit/PostgreSQL tests;
- other non-overlapping Task 0 runtime code.

Deferred until Task 1 is merged:

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

## Pre-route contract hardening checkpoint

Diagnostic checkpoint head:

```text
1c672fd9e2de7d69ebdb74a33bf17d9ff701ced6
```

This checkpoint remains **diagnostic only**. It does not authorize route exposure
or merge.

Backend CI `35380405329` / run #718:

- Ruff: **PASS**
- Partition A: **4050 passed / 435 deselected / 0 skipped**
- Partition B: **305 passed / 4180 deselected / 0 skipped**
- Partition C: **130 passed / 4355 deselected / 0 skipped**
- all zero-skip assertions: **PASS**

Frontend/native CI `35380405536` / run #667:

- frontend/web: **44 files / 277 tests PASS**
- Next production build: **PASS**
- workspace build: **PASS**
- Android native compile: **PASS**
- iOS native compile: **PASS**

### Vercel diagnostic evidence

PR #56 currently has two different Vercel signals:

- a deployment failure caused by the free-tier deployment-rate limit
  (`api-deployments-free-per-day`);
- a separate PR bot event showing a Ready preview deployment.

Those signals are **ambiguous for exact-head release qualification**. They are
recorded as diagnostic context only. Final 10B.5c qualification requires one
unambiguous successful Vercel result tied to the exact intended merge head.

### Audit allow-list

The Treatment Session vitals success audit must remain structural/value-free.

Permitted metadata is exactly:

```text
clinical_session_id
encounter_id
operation
record_type
```

Clinical values, units, BP components, heart rate, temperature, SpO2, timeline
summary, raw request/payload, treatment token, and raw clinical idempotency
payload are prohibited from audit metadata.

The canonical idempotency request hash may cryptographically bind clinical
semantics. The raw clinical values themselves still do not enter audit metadata.

### Idempotency HTTP contract

The existing repository mutation-idempotency convention persists completed
success as HTTP status `200`. The 10B.5c route must therefore use one stable
logical success contract:

```text
first successful WRITE_VITALS request -> HTTP 200
same key + same canonical request     -> HTTP 200 + original logical result
same key + changed canonical request  -> conflict / fail closed
```

The canonical request hash binds at least:

- ClinicalAccessSession;
- canonical Encounter;
- patient;
- provider;
- hospital;
- vital type;
- canonical value;
- server-owned unit;
- recorded time.

The route must not return a different nominal success status from the status
stored in `mutation_idempotency`.

### recorded_at semantics

Repository evidence defines `patient_vitals.recorded_at` as the **timestamp of
observation** and `TimelineEvent.occurred_at` as a historical occurrence
timestamp. Existing manual vitals entry accepts a caller-supplied observation
time.

The canonical Encounter currently has `created_at` but no authoritative
started-at / ended-at clinical window contract. Therefore 10B.5c must not invent
an Encounter-time cutoff or silently reinterpret `recorded_at` as server time.

For this slice:

- `recorded_at` remains the clinician-supplied observation timestamp;
- it must be timezone-aware and canonically serialized;
- it is cryptographically bound into idempotency semantics;
- no claim is made that it occurred after Encounter creation;
- a stricter historical-entry/window policy is deferred until the repository has
  an explicit clinical policy contract.

### Numeric validation semantics

10B.5c separates representation safety from clinical interpretation.

Current bounded validation proves:

- finite numeric representation;
- canonical encoding;
- server-owned units;
- BP and HR positive bounded integer representation;
- SpO2 percentage representation constrained to 0..100.

The repository does not currently define authoritative manual-entry clinical
normal/abnormal thresholds for these Treatment Session writes. 10B.5c therefore
must not manufacture diagnostic ranges, normality labels, or treatment
interpretation.

Temperature remains finite/canonical under the current broad representation
envelope. A stricter clinical plausibility range is explicitly deferred unless
an authoritative repository/domain policy is added.

### Provenance contract

Treatment Session clinician-entered vitals remain:

```text
source = manual
confidence = null
source_document_id = null
```

No AI confidence, document provenance, clinician-verification score, or
normality interpretation is fabricated.

The existing `risk_level` column remains a legacy schema-compatibility field;
10B.5c does not expose that value as a new clinical interpretation.

### Final provider-trust recheck

The repository-established primitive is:

```text
enforce_current_clinical_capability(...)
```

It reauthenticates the provider session and reevaluates current clinical
eligibility. The future WRITE_VITALS route must use the repository's normal
admission dependency and invoke this same primitive again **immediately before
commit**.

If the final recheck fails, the route must roll back the Vitals row,
TimelineEvent, audit-outbox event, and idempotency reservation/completion in the
same transaction.

No parallel or shortcut trust model is permitted.

### Post-Task-1 integration gate

PR #53 merged at `0afc5d24e7383fb91cb20ba5dd67c739bb9de40f`.
Task 0 then reconciled current main into this branch at
`26aab84b2f062649e8cd59c1a552952dd74f0dd7` before touching shared
integration files.

The WRITE_VITALS router is now mounted in `app.main`, and the shared route
registration catalog contains exactly one
`POST /api/v2/treatment-session/v1/vitals` entry while preserving the
Task-1 external-record retry/cancel entries.

The success audit continues to reuse the existing
`PATIENT_RECORD_APPEND_SUCCESS` event. That event was already present in the
shared audit catalog, so 10B.5c does not create or add a new audit event type.

Final release qualification and merge remain deferred while Task 2 PR #55 is
still open and until the then-current exact head completes the required
backend/frontend/native/Vercel evidence.

## Isolated HTTP runtime checkpoint

Starting implementation head:

```text
4c1862e149b14529b6373081dab488117b889a31
```

The isolated Task-0 router implements exactly one new surface:

```text
POST /api/v2/treatment-session/v1/vitals
```

At this historical checkpoint the router was intentionally not mounted. That gate is now superseded by the post-Task-1 integration section above: the router is mounted in `app.main`, the shared route catalog is updated, and the existing audit vocabulary is reused.

### Request contract

One request represents one typed observation through a strict discriminated
union:

- `blood_pressure`: `systolic_bp`, `diastolic_bp`, `recorded_at`;
- `heart_rate`: `beats_per_minute`, `recorded_at`;
- `temperature`: `celsius`, `recorded_at`;
- `spo2`: `percentage`, `recorded_at`.

Unknown fields are rejected. The request cannot carry patient, provider,
hospital, Encounter, ClinicalAccessSession, operation, source, confidence, or
risk authority. Canonical Vitals type/unit and manual provenance remain
server-owned.

### recorded_at implementation

The route preserves the repository-backed historical-observation meaning:
`recorded_at` is clinician-supplied observation time, not server receipt time
and not implicitly Encounter creation time.

For this isolated route:

- the timestamp must be timezone-aware;
- it is normalized to UTC before the canonical observation is created;
- the normalized timestamp is therefore the value bound into durable
  idempotency semantics;
- no Encounter-time cutoff or clinical plausibility window is invented because
  the repository still has no approved manual-entry policy for one.

### Provider trust and transaction order

Route admission reuses:

```text
require_clinical_capability(ClinicalCapability.RECORD_READ)
+
require_clinical_session(ClinicalAccessOperation.WRITE_VITALS)
```

The Treatment Session dependency independently binds the exact operation,
provider/hospital session, ClinicalAccessSession, and treatment bearer.

After the already-qualified staging service has locked/revalidated durable
session, grant, and canonical Encounter and staged Vitals + TimelineEvent +
audit outbox + idempotency completion, the route immediately re-runs:

```text
enforce_current_clinical_capability(...)
```

It also rechecks the provider, hospital, and provider-session binding against
the TreatmentSessionV1Authority. Only then does the route commit exactly once.

Any staging, final-trust, binding, or commit failure rolls back the request
transaction. Stable value-free HTTP errors expose only error codes; raw clinical
values, treatment tokens, SQL, Redis keys, audit internals, and secret hashes
are not returned.

### HTTP idempotency

- first successful mutation: HTTP 200;
- same key + same semantic request: HTTP 200 with the original logical result
  and `idempotent_replay=true`;
- same key + changed semantic request: HTTP 409;
- persisted mutation-idempotency success remains `response_status = 200`.

### Dedicated isolated tests

The Task-0-specific route test file covers:

- exact router path/method and post-Task-1 registration in `app.main`;
- all four typed observations and UTC time normalization;
- caller authority/provenance injection denial;
- naive timestamp denial;
- entry provider-trust denial;
- missing treatment token;
- wrong operation;
- successful stage -> final trust -> single commit ordering;
- idempotent replay;
- invalid idempotency key;
- semantic idempotency conflict;
- staging failure rollback;
- trust revocation before commit rollback;
- final provider-session binding mismatch rollback;
- commit failure rollback.

Diagnostic CI for the implementation head remains required before this
checkpoint can be described as green.

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

## Post-#53 integration checkpoint

- Reconciled authoritative main `0afc5d24e7383fb91cb20ba5dd67c739bb9de40f` into the Task-0 branch through integration PR #58.
- Reconciliation commit: `26aab84b2f062649e8cd59c1a552952dd74f0dd7`.
- The reconciliation had no overlapping changed files between Task 1 and the Task-0 feature delta.
- `app.main` now mounts `treatment_session_v1_vitals_router`.
- `tests/test_route_registration.py` now records `POST /api/v2/treatment-session/v1/vitals` while preserving Task-1 external-record routes.
- `tests/test_audit_event_coverage.py` remains intentionally unchanged because `PATIENT_RECORD_APPEND_SUCCESS` is already an approved catalog event.
- The dedicated vitals route contract now asserts the route is mounted.
- No second Treatment Session clinical write family is activated.
- PR #55 subsequently merged and this historical checkpoint is superseded by the final post-Task-2 consolidation section below.


## Final post-Task-2 consolidation gate

Task 2 PR #55 merged into authoritative main at:

```text
a5d16346e859a873c6736996a276d1eb579e6a63
```

with Task-2 head:

```text
99562a1a5558ebef0f80746b3f14dfcc206c9a9b
```

Task 0 reconciled that consolidated main into
`task0/10b5c-first-clinical-write` through integration PR #60.

Reconciliation commit:

```text
3a6640eee22f941c78db7d853ff5fef63559a930
```

The #55 delta is limited to the Slice 11D patient longitudinal-records UX
handoff and patient frontend screens. It introduces no Alembic migration and
does not modify the WRITE_VITALS backend authority/service/route files.

### Final migration lineage

The intended linear migration chain remains:

```text
20260918_canonical_encounter
    ->
20260918_treatment_vitals_encounter
```

The Task-0 migration remains the only child introduced by this slice. No
Alembic merge revision is authorized unless actual graph evidence creates a
second head.

### Final bounded route

Exactly one new Treatment Session clinical mutation surface is authorized:

```text
POST /api/v2/treatment-session/v1/vitals
operation = WRITE_VITALS
```

Supported typed observations remain blood pressure, heart rate, temperature,
and SpO2. No prescription, diagnosis, note, investigation, allergy, or document
mutation is part of 10B.5c.

### Final authority and trust-race contract

A successful mutation requires all of:

```text
authenticated provider
+ current server-owned provider clinical eligibility
+ exact X-Treatment-Token WRITE_VITALS authority
+ current provider/hospital/provider-session binding
+ durable ClinicalAccessSession
+ durable ConsentGrant
+ same canonical Encounter
+ strict typed observation
+ durable idempotency
+ transactional structural audit
```

The route order is:

```text
entry provider trust
-> exact Treatment Session authority
-> stage mutation
-> durable session/grant/Encounter locks and revalidation
-> final enforce_current_clinical_capability(...)
-> final provider/hospital/provider-session binding check
-> commit exactly once
```

Failure of staging or final trust/binding rolls back Vitals, TimelineEvent,
audit outbox state, and idempotency state in the same request transaction.

The caller cannot select patient, provider, hospital, ClinicalAccessSession,
canonical Encounter, operation, source, confidence, or document provenance.

### Shared catalog reconciliation

The route catalog preserves canonical Encounter, patient external-record D3
retry/cancel, and the single WRITE_VITALS route.

The audit catalog preserves:

```text
CLINICAL_ENCOUNTER_CREATED
PATIENT_EXTERNAL_RECORD_CANCELLED
PATIENT_RECORD_APPEND_SUCCESS
```

WRITE_VITALS continues to reuse `PATIENT_RECORD_APPEND_SUCCESS`; no redundant
vitals audit event is introduced.

### Final qualification evidence policy

Final merge qualification must come from GitHub Actions and commit status tied
to the exact intended merge head after this governance update. Required
evidence is Ruff; focused WRITE_VITALS, Treatment Session, Encounter, migration,
route, audit, PostgreSQL, concurrency, and rollback coverage; Partitions A/B/C
with zero skips; frontend tests; Next production build; workspace build;
Android; and iOS.

Historical green runs remain diagnostic only and are not substituted for the
exact final head.

Vercel success on the exact head is preferred. If the only failure is the
documented free-tier deployment-rate quota, the backend-only infrastructure
exception may be used only when Task 0 has no `nexa-client/**` feature delta,
all exact-head frontend/native/Next qualification is green, the consolidated
Task-2 frontend already had valid qualification, and the quota-only cause is
recorded truthfully. Production/release qualification remains deferred under
that exception.


### Final release-candidate evidence and Vercel exception

The post-Task-2 code candidate `32018f1cb2f6be29b5d9692b657d279e9b934ee2`
completed the full repository qualification matrix before this documentation-only
record was added:

- Ruff: PASS.
- Partition A: 4144 passed / 436 deselected / 0 failed / 0 errors / 0 skipped.
- Partition B: 306 passed / 4274 deselected / 0 failed / 0 errors / 0 skipped.
- Partition C: 130 passed / 4450 deselected / 0 failed / 0 errors / 0 skipped.
- frontend/Next tests: 2 files / 6 tests PASS.
- app/web tests after same-SHA rerun of one transient unrelated recovery-screen
  test failure: 44 files / 290 tests PASS.
- Next production build: PASS.
- workspace package build: PASS.
- Android native compile: PASS.
- iOS native compile: PASS.

The transient first-attempt frontend failure was in
`PatientRegistrationRecoveryScreen.test.tsx`, outside both the Task-0 delta and
the Task-2 changed-file set. No product code was changed; rerunning that failed
job on the same SHA passed all 290 app tests and all build checks.

Exact commit status for that candidate reports Vercel failure only at the
documented free-tier deployment-rate limit URL
(`upgradeToPro=build-rate-limit`). There is no Task-0 `nexa-client/**` feature
delta versus consolidated main.

The backend-only Vercel quota exception prerequisites are independently
established:

- Task-2 commit `122c2fd2a91cd0523ee171a78fe223a85260cde4`
  has exact GitHub Vercel status SUCCESS.
- From that commit to final Task-2 head
  `99562a1a5558ebef0f80746b3f14dfcc206c9a9b`, the changed-file set contains
  no `nexa-client/**` file, so the qualified frontend tree was not changed by
  the final Task-2 main reconciliation/governance commits.
- consolidated Task-2 frontend/native GitHub CI is green.
- Task 0 adds no frontend feature file.
- the current Vercel failure cause is quota-only, not an application build
  failure.

Therefore the documented backend-only Vercel infrastructure exception may be
used for this merge candidate. Production/release qualification remains
deferred until Vercel can provide normal deployment evidence again.

This evidence does not waive exact-head qualification for the documentation-only
final candidate. The final branch head created by this governance update must
still complete the same GitHub Actions matrix successfully before PR #56 may be
marked ready and merged.
