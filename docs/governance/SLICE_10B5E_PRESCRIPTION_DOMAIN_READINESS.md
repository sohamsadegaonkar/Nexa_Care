# Slice 10B.5e — Prescription Domain Readiness / WRITE_PRESCRIPTION Authority Design Audit

## Status

Classification: **AUDIT + DESIGN ONLY**

Starting authoritative main:

```text
e3505baa6d8a9591ccfef7fb10c43012b1c80f30
```

Current Alembic head:

```text
20260918_treatment_vitals_encounter
```

Current executable Treatment Session clinical write:

```text
WRITE_VITALS
```

This slice does **not** create a Prescription model, migration, route, service, provider form, or any new executable Treatment Session write.

## Executive conclusion

The existing `patient_medications` / `Medication` model is **not a canonical prescription authority**.

Repository evidence shows that it is a mixed legacy medication concept used for:

- manually appended medication/prescription-like rows;
- active/historical medication display;
- patient "Prescriptions & Medications" UX;
- emergency "active medications" projection;
- FHIR `MedicationRequest` fallback/export;
- historical/document-derived medication provenance;
- legacy timeline projection.

It does not durably prove:

- the prescribing provider;
- the facility at issuance;
- the canonical Encounter;
- the Treatment Session;
- an exact `WRITE_PRESCRIPTION` grant;
- prescription aggregate boundaries;
- multi-item prescription grouping;
- lifecycle status;
- cancellation/supersession semantics;
- administration route;
- duration;
- a canonical instruction contract.

Therefore `Medication != Prescription` remains a hard invariant.

The recommended canonical prescription domain is a new **Prescription aggregate** consisting of:

1. one `Prescription` header representing the clinician-authored issuance event and authority/authorship context; and
2. one-or-more `PrescriptionItem` children representing individual medication directives.

This is a design recommendation only. Implementation remains gated.

---

# 1. Repository evidence

## 1.1 Existing Medication model

Authoritative model:

```text
app/models/patient_records.py
class Medication
table: patient_medications
```

Current fields are:

- `id`;
- `patient_id`;
- `name`;
- `strength`;
- `frequency`;
- `prescribed_at`;
- provenance:
  - `source`;
  - `confidence`;
  - `risk_level`;
  - `source_document_id`.

The model docstring calls the rows "Active or historical pharmaceutical prescriptions."

That wording is itself evidence of mixed semantics: **active medication state**, **historical medication state**, and **prescription-like history** are not the same clinical fact.

The model has no:

- `provider_id`;
- `hospital_id`;
- `encounter_id`;
- `clinical_session_id`;
- prescription aggregate/group ID;
- lifecycle state;
- route;
- duration;
- explicit instruction field.

## 1.2 Legacy medication append boundary

Authoritative route:

```text
POST /api/v2/patient/{id}/records/medications
POST /api/v2/patient/{id}/record/medications
```

Implementation:

```text
app/api/v2/patient_record_routes.py
append_medications(...)
```

The legacy route:

- accepts caller-selected patient path ID;
- uses the legacy `clinical_append` consent gate;
- accepts `name`, `strength`, `frequency`, and caller-supplied `prescribed_at`;
- persists one `Medication` row;
- writes a `TimelineEvent(event_type="MEDICATION")`;
- uses generic `PATIENT_RECORD_APPEND_ATTEMPT` / `PATIENT_RECORD_APPEND_SUCCESS`;
- does not bind a canonical Encounter;
- does not persist provider authorship;
- does not persist hospital/facility context;
- does not bind Treatment Session `WRITE_PRESCRIPTION`;
- does not define cancellation/supersession lifecycle.

This route is therefore a **legacy medication append route**, not the future Treatment Session prescription boundary.

It must not be converted in place merely to satisfy the operation name.

## 1.3 Provider UX

The provider record viewer exposes a "prescriptions" tab through the existing clinical read surface, but the data shape is `active_medications` / medication entries.

There is no current Treatment Session provider `WRITE_PRESCRIPTION` form.

The provider UI therefore supplies no evidence that a canonical prescription write contract already exists.

## 1.4 Patient prescription UX

Authoritative patient screen:

```text
nexa-client/packages/app/features/patient/PatientPrescriptionsScreen.tsx
```

The screen intentionally presents:

```text
Prescriptions & Medications
Active and historical pharmaceutical treatments with clinical provenance.
```

The patient endpoint:

```text
GET /api/v2/patient/me/prescriptions
```

currently:

- queries `Medication`;
- aliases each Medication row as `prescription_id`;
- labels `source == "manual"` as `Clinician Prescribed`;
- merges external prescription `DocumentReference` objects into the same response;
- displays patient-uploaded prescription documents as external prescription entries.

This endpoint is a useful **longitudinal treatment presentation**, but it is not proof that each returned row is a canonical provider-authored order.

In particular, the current "Clinician Prescribed" presentation can be stronger than the underlying `Medication` row because that row has no durable provider FK.

## 1.5 Patient health summary and emergency projection

Patient health summary and emergency summary both treat Medication rows as "active medications."

The emergency builder:

```text
app/services/emergency_summary_service.py
_build_active_medications(...)
```

selects Medication rows by patient and orders by `prescribed_at`, but there is no lifecycle/status predicate because the Medication schema has no lifecycle field.

Therefore "active" is currently a presentation/category label, **not a proven prescription lifecycle state**.

The canonical prescription design must not perpetuate that ambiguity.

## 1.6 FHIR evidence

FHIR conversion already recognizes the legacy semantic gap.

`app/services/fhir_converter.py` maps medication data to `MedicationRequest`, but:

- emits `status = "unknown"` because Nexa does not store a reliable lifecycle status;
- emits `intent = "order"`;
- emits `requester` only when the source explicitly contains an authoritative provider UUID;
- explicitly refuses to infer requester from the provider performing a later export.

This fail-closed export behavior is strong evidence that a future canonical Prescription needs durable provider authorship.

## 1.7 AI / extraction evidence

The extraction stack recognizes:

- medication / prescription / drug;
- strength;
- frequency;
- route;
- duration.

Medication/prescription fields are treated as high-risk and require human review.

However, current canonical ingestion explicitly refuses to persist generic extracted medication/prescription values when it cannot prove structured strength/frequency:

```text
Medication extraction requires structured strength and frequency adjudication
```

Patient-owned external prescription imports currently finalize to:

- `DocumentReference`;
- a `TimelineEvent(event_type="DOCUMENT")`;
- patient-owned external-record provenance.

They do **not** fabricate a clinician-authored Prescription or Medication row from lossy reviewed text.

That separation must remain.

## 1.8 External prescription documents

Patient external record import supports category:

```text
PRESCRIPTION
```

but the final canonical target is currently `DocumentReference`.

The external-record model explicitly states that patient-owned external sources must never be interpreted as clinician-created or clinician-verified records.

Therefore:

```text
Patient-uploaded prescription document != Nexa clinician-authored Prescription
```

## 1.9 Treatment Session authority

Treatment Session V1 already has the closed operation:

```text
WRITE_PRESCRIPTION
```

but operation vocabulary does not create domain semantics by itself.

The existing gate already supports exact-operation validation and durable binding of:

- patient;
- provider;
- hospital;
- ClinicalAccessSession;
- ConsentGrant;
- canonical Encounter.

The current `WRITE_VITALS` path proves the required write pattern:

```text
authenticated provider
+ current clinical eligibility
+ exact Treatment Session operation
+ durable session/grant revalidation
+ canonical Encounter lock
+ idempotency
+ clinical mutation
+ TimelineEvent
+ transactional structural audit
+ final live trust/binding check
+ commit once
```

A future `WRITE_PRESCRIPTION` implementation should reuse this authority shape without weakening it.

---

# 2. Medication semantics classification

## Classification

`patient_medications` is a **mixed legacy medication concept**.

It currently combines aspects of:

- historical medication state;
- prescription-like medication items;
- manual medication entry;
- document-derived medication provenance;
- presentation-layer "active medications";
- export-layer prescription/order approximation.

It is **not safe** to classify the table globally as:

- canonical prescription;
- proven active medication;
- proven historical medication;
- provider-authored order.

The row semantics depend partly on provenance and the write path that created the row.

## Consequence

No future Treatment Session write should create a stronger clinical/legal meaning merely by inserting another `Medication` row.

---

# 3. Prescription vs medication — frozen distinction

## Canonical Prescription

A canonical Nexa Care Prescription is:

> A durable clinician-authored medication directive issued under a specific, patient-approved Treatment Session and canonical Encounter, with verifiable provider authorship and one-or-more exact medication instruction items.

It is an **order/directive fact**.

## Medication record

A Medication record is:

> A statement about medication information in the patient's longitudinal record, which may represent current state, historical state, imported evidence, or legacy prescription-like data.

It is a **medication state/history fact** unless stronger authority is separately proven.

## Hard invariant

```text
Prescription issuance does not automatically prove current medication activity.
Current medication activity does not prove a canonical prescription.
```

The two domains may later be related, but they must not be collapsed.

---

# 4. Recommended prescription aggregate

## Decision

Use a two-level aggregate:

```text
Prescription
  1 ─── N PrescriptionItem
```

A Prescription must contain at least one item.

## Why a header + child item model is justified

Repository evidence shows both:

1. existing Medication rows are one medication item each; and
2. prescription documents/benchmark inputs may contain several medications in one prescription.

A single clinician issuance event can therefore contain multiple medication directives that share:

- one patient;
- one author;
- one Encounter;
- one Treatment Session authority;
- one issued-at event;
- one idempotency intent;
- one structural audit event.

Duplicating those authority facts on each medication item would weaken the aggregate boundary and make partial-write behavior harder to reason about.

## Prescription header — recommended fields

### `prescription_id`

Required.

Repository justification:

- stable canonical identity;
- TimelineEvent target;
- idempotency response identity;
- patient detail/deep-link identity;
- future lifecycle reference.

### `patient_id`

Required server-derived FK to the canonical patient.

Repository justification:

- all structured clinical records are patient-bound;
- Treatment Session authority already binds patient;
- caller must never choose the patient for the mutation.

### `provider_id`

Required server-derived FK:

```text
provider_identity.id
```

Repository justification:

- provider authorship is intrinsic to a prescription;
- FHIR/ABDM requester cannot be fabricated;
- existing Medication lacks this fact;
- display name, role, provider_uid text, or client-supplied provider ID are not durable prescription authority.

This must be the same provider as the locked canonical Encounter / Treatment Session authority.

### `encounter_id`

Required FK:

```text
clinical_encounters.encounter_id
```

Repository justification:

- 10B.5b established canonical Encounter as the server-owned treatment context;
- WRITE_VITALS already requires the exact Encounter;
- a Treatment Session prescription should not be an unscoped write detached from the clinical interaction that authorized it.

Historical external/legacy medication evidence is not required to have Encounter linkage because it is not a new Treatment Session Prescription.

### `issued_at`

Required, server-owned timestamp for live `WRITE_PRESCRIPTION`.

Repository/product justification:

- current Medication has `prescribed_at`;
- patient timeline and FHIR need an issuance time;
- a live clinician-authored prescription should not allow the client to backdate authority implicitly.

For the future live Treatment Session route, `issued_at` should be generated by the server for the first successful mutation.

Backdated/historical prescription ingestion is a different workflow and must not be smuggled into `WRITE_PRESCRIPTION`.

### provenance/origin

For a future Treatment Session `WRITE_PRESCRIPTION` row, origin is server-owned:

```text
NEXA_CLINICIAN_CREATED
```

The repository already distinguishes clinician-created, patient-uploaded, document-extracted, and patient-corrected provenance conceptually.

The new canonical entity should not let the caller submit provenance.

A generic `source_document_id` is **not required for the initial WRITE_PRESCRIPTION path** because a live Nexa clinician-authored prescription is not an imported document.

If a future separately authorized workflow promotes a verified external prescription into a canonical order, that workflow must define its own evidence/source linkage. Do not pre-authorize that behavior in this slice.

## Hospital context

### Recommendation: do not duplicate `hospital_id` on Prescription in v1

Hospital is already durably bound by:

```text
Prescription.encounter_id
-> ClinicalEncounter.hospital_id
```

The canonical Encounter also remains immutable evidence of the facility at the time of issuance even if the provider affiliation later changes.

Reasons not to duplicate hospital identity:

- one authoritative source is safer than two;
- duplicated hospital values can drift or disagree;
- query convenience alone does not justify a second authority field.

The write service and idempotency hash must still bind `authority.hospital_id`.

If later operational evidence proves direct hospital denormalization is required, that should be a separate reviewed schema decision with equality invariants.

## ClinicalAccessSession linkage

### Recommendation: do not duplicate `clinical_session_id` on Prescription in v1

The canonical graph is already:

```text
Prescription.encounter_id
-> ClinicalEncounter.clinical_session_id
-> ClinicalAccessSessionRecord
```

That relation is one-to-one at the Encounter boundary.

The write transaction, idempotency request hash, and audit metadata should bind the ClinicalAccessSession, but the Prescription table does not need a redundant session FK merely for convenience.

---

# 5. PrescriptionItem — recommended fields

Each item is one medication directive within the Prescription aggregate.

## `prescription_item_id`

Required canonical child identity.

## `prescription_id`

Required parent FK.

## `sequence`

Recommended required positive integer.

Repository/product justification:

- prescriptions may have more than one medication;
- deterministic patient display and idempotency require stable aggregate ordering;
- duplicate medications with different instructions must not be auto-collapsed.

Sequence is presentation/order structure, not clinical priority.

## `medication_name`

Required.

Repository justification:

- existing Medication model uses `name`;
- validator/extraction vocabulary recognizes medication/prescription/drug;
- repository has no canonical drug-code system that should be invented in this slice.

Do not fabricate RxNorm/ATC/etc.

## `strength`

Required.

Repository justification:

- existing Medication requires it;
- medical validation treats quantitative strength as a minimum prescription completeness component.

## `frequency`

Required.

Repository justification:

- existing Medication requires it;
- medical validation treats administration frequency as a minimum prescription completeness component.

## `route`

Recommended optional field, subject to implementation approval.

Repository justification:

- extraction normalization explicitly recognizes `route`;
- prescription benchmark data contains route;
- some authentic prescription evidence therefore carries this semantic.

It should not become required because current persisted medication rows do not prove it.

## `duration`

Recommended optional field, subject to implementation approval.

Repository justification:

- extraction normalization explicitly recognizes `duration`;
- prescription benchmark data contains duration;
- current Medication persistence does not consistently contain it.

## `instructions`

### Deferred for v1 unless product explicitly approves a bounded free-text contract

Repository evidence does not currently define a canonical structured instruction field distinct from frequency/route/duration.

Do not add a free-text "instructions" field merely because other EHR systems commonly have one.

If product requires it, the implementation slice must define:

- size limits;
- normalization;
- display behavior;
- audit exclusion;
- whether instructions are part of immutable order semantics.

---

# 6. Provider authorship strategy

Canonical authorship must be:

```text
Prescription.provider_id
-> provider_identity.id
```

The value must be server-derived from the qualified Treatment Session authority and must equal:

```text
ClinicalEncounter.provider_id
ClinicalAccessSessionRecord.provider_id
```

Do not use as authority:

- provider display name;
- role strings;
- frontend-selected provider identifiers;
- free-text registration number;
- provider performing a later export.

Provider display data may be resolved later for presentation, but the durable author is the provider identity UUID.

## Prescriber eligibility question

Current Treatment Session write gating uses current clinical eligibility plus an exact operation grant. The provider-capability vocabulary currently has no dedicated prescribing capability.

Before executable `WRITE_PRESCRIPTION`, governance must explicitly decide whether:

1. existing current clinical eligibility + exact patient-signed `WRITE_PRESCRIPTION` is sufficient; or
2. prescribing requires an additional server-owned provider eligibility/capability rule.

Do not silently invent `RECORD_WRITE` or a prescription role.

This is an implementation blocker until explicitly resolved.

---

# 7. Encounter strategy

## New Treatment Session prescriptions

Mandatory canonical Encounter.

Authority graph:

```text
Treatment Session
-> ClinicalAccessSessionRecord
-> ClinicalEncounter
-> Prescription
-> PrescriptionItem[]
```

The service must lock/revalidate the same durable authority graph used by WRITE_VITALS, but for exact:

```text
ClinicalAccessOperation.WRITE_PRESCRIPTION
```

A token that contains only `WRITE_VITALS` must fail before any prescription mutation is staged.

## Historical / imported data

Existing:

- Medication rows;
- patient-uploaded prescription documents;
- document-extracted medication evidence;
- legacy clinical shard prescription strings

may remain Encounter-null/unbound because they are **not** canonical Treatment Session prescriptions.

Do not fabricate Encounter linkage.

---

# 8. Lifecycle recommendation

## Current evidence

The repository has no authoritative prescription lifecycle model.

Medication rows have no state and are nevertheless surfaced under "active medications."

FHIR export correctly uses `MedicationRequest.status = "unknown"`.

No current Treatment Session operation exists for:

- cancel prescription;
- discontinue prescription;
- supersede prescription;
- complete prescription.

## Recommendation for the first canonical persistence design

Model the first authorized operation as an immutable **prescription issuance fact**.

The first implementation should not invent a broad mutable lifecycle merely because common systems contain states.

For the initial creation operation:

```text
WRITE_PRESCRIPTION -> creates an issued prescription fact
```

Do not automatically infer:

- active medication;
- completed treatment;
- discontinued medication;
- cancelled order;
- superseded order.

### Status column

Recommendation: **defer a mutable lifecycle status field until lifecycle operations are defined**.

The issuance fact is already represented by the existence of the canonical Prescription plus `issued_at`.

If product later needs cancellation/supersession/discontinuation, define a separate authorized lifecycle slice with:

- explicit operation vocabulary;
- who may perform the transition;
- whether patient signature is required;
- transition graph;
- immutable lifecycle audit;
- FHIR mapping.

This avoids creating states that cannot yet be authorized correctly.

---

# 9. Future WRITE_PRESCRIPTION authority graph

The future write must require:

```text
authenticated provider
+
current provider clinical eligibility
+
patient-signed Treatment Session V1
+
exact WRITE_PRESCRIPTION
+
same patient/provider/hospital authority
+
durable ClinicalAccessSession validation
+
durable ConsentGrant validation
+
canonical Encounter
+
provider authorship == Encounter.provider_id
+
canonical Prescription aggregate payload
+
durable idempotency
+
transactional TimelineEvent
+
transactional value-free audit
+
final live provider trust/session-binding recheck
+
commit exactly once
```

The client must never supply as authority:

- patient_id;
- provider_id;
- hospital_id;
- clinical_session_id;
- encounter_id;
- provenance/origin;
- operation.

The route may display context to the clinician but authority must remain server-derived.

---

# 10. Future request contract

A future request should contain **only prescription semantics**, not authority.

Recommended semantic shape:

```text
items:
  - sequence
  - medication_name
  - strength
  - frequency
  - optional route
  - optional duration
```

Potential `instructions` remains gated as described above.

The request must not contain:

- patient;
- provider;
- hospital;
- Encounter;
- ClinicalAccessSession;
- operation;
- source/provenance;
- FHIR status;
- "active" medication state;
- AI-generated recommendations.

One HTTP request should create one Prescription aggregate containing one-or-more items.

Partial item commit is prohibited.

---

# 11. Idempotency design

Use existing:

```text
public.mutation_idempotency
```

Suggested future operation name:

```text
treatment.write_prescription.v1
```

This is a design name only; it is not implemented by this slice.

## Canonical request hash

The future request hash should cryptographically bind:

### Server-derived authority

- ClinicalAccessSession ID;
- canonical Encounter ID;
- patient ID;
- provider ID;
- hospital ID;
- exact operation `WRITE_PRESCRIPTION`.

### Exact intended prescription semantics

For each item, in explicit sequence order:

- medication name;
- strength;
- frequency;
- route if present;
- duration if present;
- any future approved instruction field if present.

The hash should also bind the aggregate item count through the canonical serialized list.

## issued_at and idempotency

Server-owned `issued_at` should **not** be client request semantics.

The first successful reservation/transaction establishes the authoritative issuance timestamp and response.

A lost-response retry using the same idempotency key and identical semantic request returns the original logical Prescription.

Same key + changed semantic content fails closed.

## Transaction boundary

One transaction must contain:

- idempotency reservation;
- Prescription header;
- all PrescriptionItem rows;
- TimelineEvent;
- audit outbox event;
- idempotency completion.

Any failure rolls back the entire aggregate.

---

# 12. Audit design

## Existing audit vocabulary

There is no current canonical:

```text
PRESCRIPTION_CREATED
```

event.

WRITE_VITALS successfully uses the structural:

```text
PATIENT_RECORD_APPEND_SUCCESS
```

event.

## Recommendation for initial creation

Reuse:

```text
PATIENT_RECORD_APPEND_SUCCESS
```

unless governance later establishes a lifecycle-specific prescription event family.

Recommended structural metadata:

```text
clinical_session_id
encounter_id
operation = WRITE_PRESCRIPTION
record_type = prescription
```

Target ID should be the canonical `prescription_id`.

Actor ID should be the authoritative `provider_identity.id`.

## Audit metadata must NOT contain

- medication names;
- strengths;
- frequencies;
- routes;
- durations;
- instructions;
- prescription free text;
- FHIR payload;
- Treatment Session token;
- idempotency request body;
- raw source document contents;
- clinical interpretation;
- recommendations.

Audit stays structural/value-free.

---

# 13. Timeline design

A successful canonical issuance should create:

```text
TimelineEvent.event_type = "PRESCRIPTION"
TimelineEvent.event_ref_id = prescription_id
TimelineEvent.occurred_at = issued_at
```

Recommended summary:

```text
Prescription issued
```

or another bounded structural presentation such as:

```text
Prescription issued (N medication items)
```

The canonical Prescription/PrescriptionItem tables remain the source of clinical detail.

Do not use timeline free text as prescription authority.

---

# 14. Patient UX and provenance contract

## Current problem

The patient UI intentionally merges:

- Medication rows;
- external prescription documents

into one "Prescriptions & Medications" experience.

That is useful for longitudinal navigation but currently blurs authority.

## Required future distinctions

### Canonical Nexa clinician-authored Prescription

Recommended provenance label:

```text
Clinician authored in Nexa
```

May show:

- clinician display name resolved from `provider_id`;
- facility resolved from Encounter;
- issued date;
- item count;
- medication items.

### Legacy manual Medication

Recommended label:

```text
Clinician-recorded medication
```

Do not call it canonical "Clinician Prescribed" unless durable authorship is actually known.

### AI/document-derived Medication

Recommended label:

```text
Document extracted medication
```

Include confidence/review provenance according to existing UX rules.

Do not imply the AI authored or prescribed anything.

### Patient-uploaded prescription document

Recommended label:

```text
Patient-uploaded prescription document
```

It remains a DocumentReference/source artifact, not a Nexa Prescription.

### Patient-corrected external record

Recommended label:

```text
Patient-corrected external record
```

Correction proves patient review of extracted text; it does not establish prescriber identity or convert the artifact into a Nexa clinician-authored Prescription.

## Active medications

A canonical Prescription should **not automatically populate "Active medications"** without a separately defined medication-state contract.

The future UX should distinguish at least:

```text
Prescriptions / orders
Medication history / state
External prescription documents
```

Do not derive medication activity solely from prescription existence.

---

# 15. FHIR / interoperability implications

The existing converter's fail-closed behavior should be preserved.

A canonical Prescription would finally provide authoritative:

- requester / provider;
- authoredOn / issued_at;
- medication item semantics.

However, lifecycle mapping remains unresolved.

Do not map a newly issued canonical Prescription to an FHIR lifecycle status such as `active`, `completed`, or `cancelled` until Nexa defines that lifecycle.

The repository's existing external validation/governance also notes that requester authority must not be fabricated.

---

# 16. Legacy data migration / backfill

## Recommendation

**No automatic backfill from Medication to Prescription.**

Existing Medication rows lack enough authority to safely create canonical Prescription records.

## Why automatic migration is unsafe

Most rows do not prove:

- provider author;
- Treatment Session;
- hospital context;
- Encounter;
- prescription aggregate boundary;
- whether multiple rows belonged to the same issued prescription;
- lifecycle;
- route/duration;
- whether `prescribed_at` is actual issuance time versus historical observation time.

## Evidence required for any future migration

A row or group of rows could be considered for migration only if independent durable evidence proves, at minimum:

- exact patient;
- exact prescribing provider identity;
- authentic issuance timestamp;
- medication item semantics;
- grouping/aggregate boundary if multiple items;
- trustworthy provenance;
- source evidence sufficient to establish that this was a prescription/order, not merely medication history.

For Treatment Session-era migration, Encounter/session authority would also need to be real, not fabricated.

## External documents

Patient-uploaded prescription documents remain `DocumentReference` evidence.

Do not create a canonical Nexa Prescription merely because the uploaded document is categorized as PRESCRIPTION.

## Legacy Medication rows

Leave them as historical/unclassified Medication records unless a separately approved migration proves stronger authority.

---

# 17. Existing model comparison

| Concern | Medication today | Recommended Prescription |
|---|---|---|
| Clinical meaning | Mixed medication/history/prescription-like item | Clinician-authored order/issuance aggregate |
| Multi-item grouping | No | Yes, 1..N items |
| Patient | Direct | Direct, server-derived |
| Provider author | Missing | Direct FK to provider_identity.id |
| Hospital | Missing | Derived through Encounter |
| Encounter | Missing | Mandatory for Treatment Session writes |
| Clinical session | Missing | Derived through Encounter |
| Issued/prescribed time | Caller-supplied prescribed_at | Server-owned issued_at for live write |
| Medication name | Yes | Child item |
| Strength | Yes | Child item |
| Frequency | Yes | Child item |
| Route | Missing | Optional child item, evidence-supported |
| Duration | Missing | Optional child item, evidence-supported |
| Instructions | Missing | Deferred pending explicit contract |
| Lifecycle | Missing | Creation-only issuance fact initially; lifecycle deferred |
| Provenance | manual / ai_extracted | Server-owned clinician-created for WRITE_PRESCRIPTION |
| Source document | Optional | Not used by initial live WRITE_PRESCRIPTION |
| Treatment Session operation | None | Exact WRITE_PRESCRIPTION |
| Idempotency | Legacy route does not provide Treatment Session durable contract | mutation_idempotency aggregate contract |
| Audit | Generic append | Generic structural append initially, authority-bound |
| Timeline | MEDICATION free-text | PRESCRIPTION ref to canonical aggregate |

---

# 18. Regulatory / safety boundary

This audit authorizes no clinical recommendation behavior.

A future prescription write is limited to secure persistence of a clinician-authored directive.

Do not add:

- medication recommendation engine;
- autonomous prescribing;
- dose recommendation;
- interaction recommendation;
- automatic prescription generation;
- AI-authored prescription text;
- conversion of extracted text into a prescription without explicit clinician-authored authority.

The repository's India regulatory baseline already marks prescribing/telemedicine requirements as an area requiring current specialist/legal verification. This design does not claim to resolve that legal analysis.

---

# 19. Unresolved questions

The following must be explicitly resolved before executable `WRITE_PRESCRIPTION` implementation:

1. **Prescriber eligibility**  
   Is current general clinical eligibility plus patient-signed `WRITE_PRESCRIPTION` sufficient, or is an additional server-owned prescribing eligibility/capability required?

2. **Route field**  
   The repository recognizes route in extraction evidence. Confirm whether the first provider-authored prescription contract should expose it as optional input.

3. **Duration field**  
   The repository recognizes duration in extraction evidence. Confirm whether the first provider-authored prescription contract should expose it as optional input.

4. **Instructions field**  
   Is a bounded clinician free-text instruction field required in v1? If yes, define limits/normalization and make it part of immutable idempotency semantics.

5. **Lifecycle operations**  
   There is currently no cancellation/supersession/discontinuation operation. Confirm creation-only issuance for the first implementation.

6. **Medication-state relationship**  
   Confirm that issuing a Prescription does not automatically assert "active medication." Define a separate medication-state strategy before changing emergency/current-medication semantics.

7. **FHIR lifecycle mapping**  
   Define how an internal issued prescription maps to FHIR `MedicationRequest.status` without inventing activity/completion state.

8. **External prescription promotion**  
   Decide whether any future verified external prescription can become a canonical Prescription. Current patient-uploaded documents must remain source artifacts.

These are domain/product authority questions, not reasons to reuse the legacy Medication model.

---

# 20. Implementation gate

## Verdict

```text
CANONICAL DOMAIN DIRECTION: DEFINED
WRITE_PRESCRIPTION IMPLEMENTATION: NOT YET AUTHORIZED
```

The audit establishes that implementation must use a new Prescription aggregate rather than `Medication`.

Implementation remains blocked until the orchestrator explicitly approves:

- header + child aggregate;
- provider authorship strategy;
- mandatory Encounter linkage;
- hospital/session transitive linkage;
- initial item field set;
- prescriber eligibility rule;
- creation-only lifecycle position.

## Prohibited in 10B.5e

Do not create:

- `Prescription` table;
- `PrescriptionItem` table;
- migration;
- `WRITE_PRESCRIPTION` route;
- write service;
- provider form;
- client transport;
- new Treatment Session operation family.

---

# 21. Exact next Task-0 action

The next Task-0 action is:

> **Orchestrator/governance review and explicit approval or amendment of this 10B.5e canonical Prescription contract.**

Only after that approval should a separate implementation slice be defined.

No implementation slice name or scope is implied by this audit.

---

# 22. Frozen safety invariants

1. `Medication != canonical Prescription`.
2. `WRITE_VITALS` cannot authorize prescription creation.
3. Signed Consent V3 remains read-only.
4. A canonical prescription must have durable provider authorship.
5. A Treatment Session prescription must bind the exact canonical Encounter.
6. Patient/provider/hospital/session/Encounter authority is server-derived.
7. One prescription aggregate commits atomically with all child items.
8. No automatic legacy Medication backfill.
9. External prescription documents remain source artifacts unless a future governed promotion path proves authority.
10. Prescription issuance does not automatically mean active medication.
11. Audit metadata remains value-free.
12. Timeline text is presentation, never prescription authority.
13. No autonomous/AI prescribing.
14. No second Treatment Session clinical write becomes executable in this slice.
