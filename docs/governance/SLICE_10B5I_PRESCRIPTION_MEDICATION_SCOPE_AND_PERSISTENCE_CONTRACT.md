# Slice 10B.5i — Prescription Medication Scope and Persistence Contract

**Status:** GOVERNANCE CONTRACT / NO PRESCRIPTION PERSISTENCE IMPLEMENTED  
**Regulatory cutoff:** 2026-09-19 (India)  
**Repository baseline:** `25a461631a3e9c879b7021c10e686e5b98f2410d` on `main`  
**Alembic head:** `20260919_prescriber_eligibility`  
**Task-0 boundary:** final medication-scope and persistence contract before any canonical `WRITE_PRESCRIPTION` persistence implementation.

---

## 1. Purpose and frozen upstream invariants

This slice answers one question only:

> Can Nexa machine-enforce the medication scope that it claims to support before a clinician persists a canonical Prescription?

This slice does **not** implement Prescription persistence.

The following upstream invariants remain frozen:

```text
Medication != canonical Prescription
Prescription issuance != active medication state
WRITE_PRESCRIPTION != professional prescribing eligibility
patient consent != professional prescribing eligibility
provider authentication != professional verification
professional verification != prescribing eligibility
external prescription artifact != canonical Prescription
patient correction != clinician authorship
```

Only the conjunction of:

```text
current ClinicalCapability.PRESCRIBE_MEDICATION
+
exact WRITE_PRESCRIPTION
+
live Treatment Session authority
+
existing canonical Encounter authority
+
future bounded medication-scope validation
```

may authorize a canonical Prescription issuance.

Legacy role `clinician` never independently grants prescribing authority.

---

## 2. Primary-source regulatory audit

### 2.1 Source hierarchy and cutoff

The repository policy below is based on official Indian primary or first-party regulatory sources checked through **2026-09-19**. Draft notifications are recorded as drafts and are not treated as effective final rules.

| Source | Date / status | Relevant effect | Official URL |
| --- | --- | --- | --- |
| CDSCO — Drugs Rules, 1945 consolidated compilation | amended through G.S.R. 360(E), 2024-07-01; CDSCO release 2024-09-19 | Rule 65 retail prescription controls; Rule 97 H/H1/X labels; Schedules H/H1/X/H2 | https://www.cdsco.gov.in/opencms/opencms/en/Acts-and-rules/Drugs-Rules/ |
| CDSCO consolidated Drugs Rules PDF | 2024 compilation | Current consolidated base used with later Gazette amendments | https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/drug_rules/Drugs%20Rules%201945_2024%2009.pdf |
| India Code — Drugs and Cosmetics Rules, 1945 | official rule text | Rule 65 and Rule 97 prescription/recordkeeping requirements | https://www.indiacode.nic.in/bitstream/123456789/19158/1/2016drugsandcosmeticsact1940rules1945.pdf |
| G.S.R. 588(E), Drugs and Cosmetics (Fourth Amendment) Rules, 2013 | 2013-08-30; effective six months after publication | Created Schedule H1 controls; separate retail supply register retained three years | https://upload.indiacode.nic.in/showfile?actid=AC_CEN_12_13_00023_194023_1523353460112&filename=84amendment_of_rule_65a_rule_97_inclusion_of_sch_h1o_sw.pdf&type=notification |
| CDSCO Gazette index | checked 2026-09-19 | Current post-2024 amendments and drafts | https://www.cdsco.gov.in/opencms/opencms/en/Notifications/Gazette-Notifications |
| G.S.R. 95(E) | 2024-02-05 final | Oseltamivir and Zanamivir included in Schedule H1 | CDSCO Gazette index above |
| G.S.R. 377(E) | 2026-05-13 final | Pregabalin and its formulations included in Schedule H1 | CDSCO Gazette index above |
| G.S.R. 745(E) | 2026-08-19 **draft** | Proposed H1 additions: Flupentixol, Zopiclone, Gabapentin, Carisoprodol; not treated as final law at this cutoff | https://www.cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadGazette_NotificationsFiles/2026.08.19%20G.S.R%20745%28E%29_Inclusion%20of%20additional%20four%20drugs%20in%20Schedule%20H1.pdf |
| G.S.R. 823(E) | 2022-11-17; effective 2023-08-01 | Schedule H2 / Rule 96 QR/bar-code product authentication requirement | https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadGazette_NotificationsFiles/2022.11.17_Final%20GSR%20823%28E%29_Amendment%20in%20rule%2096%20for%20mandating.pdf |
| CDSCO Gazette amendments to Schedule H2 | G.S.R. 757(E), 2025-10-16; further G.S.R. 506(E), 2026-06-22 | H2 evolves independently; it is a product traceability/label contract, not by itself a prescribing-authority class | CDSCO Gazette index above |
| Narcotic Drugs and Psychotropic Substances Act, 1985 | enacted 1985-09-16; enforced 1985-11-14 | Section 8 allows controlled operations only for medical/scientific purposes and under applicable Act/rules/licence/permit conditions | https://www.indiacode.nic.in/indiacode/handle/123456789/1791?col=123456789%2F1362&view_type=search |
| Narcotic Drugs and Psychotropic Substances Rules, 1985 | current official India Code rule compilation | Rules 52F/52G impose prescription conditions for essential narcotic drugs and additional controlled handling | https://upload.indiacode.nic.in/showfile?actid=AC_CG_61_1073_00014_00014_1563259383370&filename=the_narcotic_drugs_and_psychotropic_substances_rules%2C_1985_date_14.11.1985.pdf&type=rule |
| Telemedicine Practice Guidelines | 2020-03-25 | Appendix 5 to the 2002 professional-conduct regulations; mode/consultation-specific medicine matrix | https://www.nmc.org.in/wp-content/uploads/2019/10/Public_Notice_for_TMG_Website_Notice-merged.pdf |
| Modification in Medicine Lists in Telemedicine Practice Guidelines | 2020-04-11 | Added Phenobarbitone, Clobazam and Clonazepam to List A despite the broader prohibited-category rule | https://www.nmc.org.in/MCIRest/open/getDocument?path=%2FDocuments%2FPublic%2FPortal%2FLatestNews%2FModification+in+Medicine+lists+in+Telemedicine+Practice+Guidelin.pdf |
| NMC Registered Medical Practitioners (Professional Conduct) (Amendment) Regulations, 2023 | 2023-08-23 | Holds the 2023 regulations in abeyance and expressly adopts/makes effective the 2002 regulations pending further Gazette notification | https://www.nmc.org.in/wp-content/uploads/2026/02/NationalMedicalCommissionRegisteredMedicalPractionerProfessionalonduct.pdf |\n| NMC Rules & Regulations page | checked 2026-09-19 | Current first-party index showing the 2023 conduct regulations and the 2023-08-23 abeyance amendment | https://www.nmc.org.in/rules-regulations-nmc/ |
| Indian Medical Council (Professional Conduct, Etiquette and Ethics) Regulations, 2002 | published 2002-04-06; current NMC page | Current professional-conduct baseline re-adopted when the 2023 regulations were held in abeyance | https://www.nmc.org.in/rules-regulations/code-of-medical-ethics-regulations-2002/ |
| NMC/MCI telemedicine archive | 2020 archive | First-party index for Telemedicine Guidelines, FAQ, and medicine-list modification | https://www.nmc.org.in/old-archive-news/ |

### 2.2 Regulatory observations that affect product architecture

1. **Schedule H / H1 / X are not interchangeable.**
   - Drugs Rules Rule 97 distinguishes Rx/NRx/XRx labels.
   - Rule 65 restricts retail supply of H/H1/X drugs to appropriate prescriptions.
   - Schedule X has additional duplicate-prescription/retention handling at the dispensing boundary.
   - Schedule H1 has a separate retail supply register retained for three years.

2. **The regulated lists change.**
   - Oseltamivir/Zanamivir entered H1 in 2024.
   - Pregabalin entered H1 by final notification G.S.R. 377(E) on 2026-05-13.
   - Four further H1 additions were proposed by G.S.R. 745(E) on 2026-08-19, but that source explicitly remains a draft as of this cutoff.
   - Therefore a hard-coded name list in route logic is not a durable regulatory contract.

3. **NDPS classification introduces a separate legal regime.**
   Section 8 of the NDPS Act and Rules 52F/52G require more than ordinary free-text prescribing semantics. Essential narcotic drug prescriptions have prescribed identity, quantity, daily-dose and consumption-period requirements, among others.

4. **Telemedicine medication scope depends on consultation context.**
   The operative 2020 Telemedicine Practice Guidelines distinguish:
   - List O: any mode / any consultation;
   - List A: video first consultation, with follow-up/refill paths;
   - List B: follow-up add-on medicines under the defined prior-consultation context;
   - Prohibited: Schedule X and NDPS narcotic/psychotropic substances under the base matrix.
   
   The 2020-04-11 official modification adds Phenobarbitone, Clobazam and Clonazepam to List A. Nexa therefore must not reduce the policy to simplistic string or broad-category matching.

5. **The 2023 RMP Professional Conduct Regulations are not used as the operative source here.**
   NMC's current rules page expressly lists the 2023-08-23 amendment that keeps the 2023-08-02 regulations in abeyance. This contract therefore does not silently adopt the broader medicine wording from the held-in-abeyance text.

6. **Schedule H2 is relevant to catalog provenance, not sufficient prescribing authorization.**
   Rule 96/H2 is a product-authentication/traceability mechanism. A future catalog may use H2 identifiers/metadata as evidence, but H2 membership does not replace H/H1/X/NDPS/telemedicine classification.

---

## 3. Repository care-mode audit

### 3.1 ClinicalEncounter

Current `ClinicalEncounter` persists exactly:

```text
encounter_id
clinical_session_id
patient_id
provider_id
hospital_id
created_at
```

It has no field proving:

```text
IN_PERSON
VIDEO
AUDIO
TEXT
```

### 3.2 Treatment Session

Treatment Session V1 durably binds:

- patient;
- provider;
- hospital;
- provider session;
- purpose;
- exact allowed operations;
- signed patient decision;
- durable ClinicalAccessSession;
- canonical Encounter correlation.

It does not bind a consultation modality.

### 3.3 Provider/facility context

`ProviderHospitalAffiliation.affiliation_type` can conceptually include `TELEMEDICINE`, but that describes an affiliation relationship. It does not prove the modality of a particular Encounter.

A provider with a permanent hospital affiliation may perform a remote consultation. A provider with a telemedicine affiliation may still be physically present in some workflow. The affiliation value is therefore not a safe Encounter modality source.

### 3.4 Frontend and visit/location semantics

The provider and patient Treatment Session flows bind provider/hospital/session and signed operation authority. Repository search does not find a canonical `consultation_mode`, `care_mode`, or `in_person` authority. No frontend selection can be treated as server proof because no governed server field persists and validates such a fact.

### 3.5 Frozen finding

```text
CARE MODE UNKNOWN
```

Current Nexa is neither proven always in-person nor proven always remote.

For any medication rule that depends on mode/type of consultation, UNKNOWN must fail closed.

---

## 4. Medication-category enforceability audit

This table answers what the **current server** can classify from the proposed free-text item shape, not what Indian law generally permits a qualified clinician to prescribe.

| Category | Current server classification | Why |
| --- | --- | --- |
| Ordinary non-controlled prescription medicines | **UNCLASSIFIABLE** | No authoritative medication catalog/code; free text cannot prove that a medicine is outside G/H/H1/X/NDPS or determine telemedicine List A/B/O semantics. |\n| Schedule G | **UNCLASSIFIABLE** | Rule 97 requires a medical-supervision warning, but Nexa has no server-owned mapping from submitted free text to Schedule G. |
| Schedule H | **UNCLASSIFIABLE** | No server-owned mapping from submitted name to current Schedule H. |
| Schedule H1 | **UNCLASSIFIABLE** | No server-owned mapping; H1 has recent changes, including Pregabalin in 2026. |
| Schedule X | **UNCLASSIFIABLE** | No authoritative name/code classifier. Product policy can declare it excluded, but current arbitrary strings cannot enforce the exclusion. |
| NDPS narcotic/psychotropic substances | **UNCLASSIFIABLE** | No authoritative NDPS classifier; substance/formulation/salt semantics cannot safely be inferred from free text. |
| Anti-cancer / specialist-controlled therapies | **UNCLASSIFIABLE** | No specialist/high-risk therapeutic-class authority; the operative telemedicine material gives anti-cancer medicines as a prohibited-list example, but Indian law does not provide Nexa with one universal machine-ready 'specialist-controlled' schedule and the repository has no comprehensive classifier. |
| Drugs requiring special prescription/recordkeeping | **UNCLASSIFIABLE** | No machine-readable regulatory obligations attached to medication identity. |
| OTC / Telemedicine List O candidates | **UNCLASSIFIABLE** | Potentially supportable after cataloging, but current string input cannot establish List O/non-prescription status. |
| Emergency medication scenarios | **EXCLUDED** | Routine canonical prescription persistence must not become an emergency-policy bypass; Nexa already has separate emergency authority concepts. |

### 4.1 Product exclusion versus legal prohibition

Where this contract says **EXCLUDED** or future matrix says **DENY**, that is a Nexa v1 product boundary. It does not claim that an otherwise qualified RMP can never prescribe the drug in every lawful in-person context.

Nexa is intentionally narrower than the maximum legal scope.

---

## 5. Free-text medication decision

### Decision

```text
B. A CONTROLLED SERVER-MANAGED MEDICATION CATALOG/CODE IS REQUIRED FIRST.
```

Consequently, today:

```text
ARBITRARY FREE-TEXT WRITE_PRESCRIPTION PERSISTENCE IS BLOCKED.
```

A caller-provided `medication_name: string` cannot safely coexist with statements such as:

```text
Schedule X excluded
NDPS excluded
Schedule H1 excluded
telemedicine-prohibited medicines excluded
```

unless the server can resolve the submitted medication to an authoritative current classification.

The following approaches are explicitly prohibited as clinical security controls:

```text
substring matching
case-insensitive deny lists
regular-expression drug-name detection
brand-name guessing
LLM/AI classification
OCR-derived classification
"unknown means ordinary"
```

Examples such as `if "morphine" in medication_name` are not acceptable.

---

## 6. Medication identifier contract

### 6.1 Required direction

The first canonical Prescription requires a **server-owned internal medication code** backed by a versioned, approved medication-scope catalog.

No third-party vocabulary is adopted by this slice.

A future implementation may map the internal code to external terminologies, but external code selection cannot become authorization until licensing, India relevance, stability, update ownership, generic/brand semantics, and regulatory metadata are separately approved.

### 6.2 MedicationItem identity shape

Freeze the future item identity as:

```text
medication_code
medication_display
```

Semantics:

- `medication_code` is the server-managed stable identity used for validation and authorization;
- the client may submit/select a code only from the currently approved catalog;
- unknown, retired, future-dated, draft-only, or unapproved codes fail closed;
- `medication_display` is a server-resolved immutable display snapshot stored with the issued Prescription;
- the display string does **not** grant identity or override catalog classification;
- client-supplied display text, if ever transported for UX, must be ignored for authorization and must exactly match or be replaced by server display before persistence.

### 6.3 Catalog identity granularity

A catalog entry must be specific enough to establish the regulatory/product attributes used by policy. A bare uncontrolled brand string or ingredient fragment is insufficient.

The catalog may represent generic or branded concepts, but every accepted entry must resolve deterministically to the classification facts required by the release policy.

---

## 7. Safest v1 medication boundary

The narrowest defensible initial scope is:

> **Only medication entries selected from a server-managed, immutable-versioned approved catalog and explicitly marked as eligible for Nexa v1 under every care mode that the current system might represent.**

Because current Encounter care mode is UNKNOWN, the initial executable subset must not depend on proving VIDEO, AUDIO, TEXT or IN_PERSON.

### 7.1 Initial allow-policy concept

An entry may be marked `V1_UNIVERSAL_ALLOWED` only if governance establishes all of the following for the catalog version:

- not Schedule H;
- not Schedule H1;
- not Schedule X;
- not NDPS-controlled;
- not a telemedicine-prohibited medicine;
- not an anti-cancer/specialist-controlled therapy under the Nexa v1 exclusion;
- no special prescription/recordkeeping obligation that the v1 aggregate cannot represent;
- not a draft-only regulatory classification;
- not a banned/prohibited product;
- not an emergency/public-health-only exception requiring separate context;
- safe for the **ANY mode / ANY consultation** boundary supported by the applicable official telemedicine source;
- exact current regulatory evidence attached to the catalog release.

In practice this is expected to begin with a deliberately small List-O/non-prescription-style allowlist, not the universe of Indian medicines.

### 7.2 No implicit fallback

If no catalog entry matches exactly:

```text
DENY
```

If regulatory metadata is unknown or contradictory:

```text
DENY
```

If the active catalog version cannot be loaded/verified:

```text
FAIL CLOSED
```

---

## 8. Consultation-modality future contract

Medication categories whose rules depend on consultation modality require a future server-owned Encounter fact.

A possible controlled vocabulary is:

```text
IN_PERSON
VIDEO
AUDIO
TEXT
UNKNOWN
```

This field is **not implemented by this slice**.

### 8.1 Source-of-truth requirements

A future modality value must be:

- server-owned;
- bound to the exact Encounter/Treatment Session;
- derived from a qualified interaction/channel source, not a free client boolean;
- immutable for the issuance event or versioned with provenance;
- auditable without exposing unnecessary clinical content.

### 8.2 Who may influence it

- patient/provider clients may provide observations needed to establish mode;
- neither side may unilaterally assert an authorization-granting value;
- hospital affiliation type is insufficient;
- geographic presence, IP address, device type, or network location alone is insufficient;
- if the server cannot prove the applicable mode, store/derive `UNKNOWN`.

### 8.3 Fail-closed rule

For any catalog entry whose authorization depends on mode or consultation type:

```text
CARE MODE UNKNOWN -> DENY
```

The narrow initial `V1_UNIVERSAL_ALLOWED` subset avoids making care mode an immediate prerequisite for every v1 item, but care-mode authority is required before Nexa expands to modality-dependent prescription medicines.

---

## 9. Canonical aggregate revalidation

### 9.1 Prescription header

The future first implementation remains an immutable issuance aggregate:

```text
Prescription
- prescription_id
- patient_id
- provider_id
- encounter_id
- issued_at
- origin/provenance
- medication_catalog_version
- medication_scope_policy_version
```

Authority ownership:

- `prescription_id`: server-generated;
- `patient_id`: server-derived from Treatment Session / Encounter;
- `provider_id`: server-derived;
- `encounter_id`: existing canonical Encounter only;
- `issued_at`: server time;
- origin/provenance: server-owned;
- catalog/policy versions: server-owned active approved versions used for validation.

### 9.2 Prescription items

```text
PrescriptionItem
- prescription_item_id
- prescription_id
- sequence
- medication_code
- medication_display
- strength
- frequency
```

`medication_name` is replaced by the code/display pair because arbitrary name text cannot be the authorization identity.

### 9.3 Deliberately absent

Still deferred:

- route;
- duration;
- generic instructions;
- mutable lifecycle status;
- refill/renewal state;
- adherence state;
- medication administration state.

---

## 10. Strict future input bounds

These are product/engineering safety bounds, not claims that regulation mandates these exact lengths.

| Field | Frozen v1 bound |
| --- | --- |
| Prescription items | **1–20** items |
| sequence | integer, unique, contiguous `1..N`, server validates exact order |
| medication_code | **1–64** characters; must resolve exactly in active approved catalog; no free creation |
| medication_display | **1–128** characters; server-derived snapshot, not caller authority |
| strength | **1–64** characters |
| frequency | **1–64** characters |

For clinician-authored display strings `strength` and `frequency`:

- trim leading/trailing whitespace;
- normalize Unicode to a single canonical form before hashing/persistence;
- single-line only;
- reject control characters;
- reject empty values;
- no hidden markup/HTML;
- no automatic clinical expansion or interpretation.

The API must reject oversize strings before persistence.

---

## 11. Strength semantics

### Decision

```text
A. STORE ONE BOUNDED CLINICIAN-AUTHORED DISPLAY STRING.
```

Examples may include:

```text
500 mg
5 mg/mL
100 units/mL
```

Nexa v1 does not parse these into a dose engine.

Important separation:

```text
strength != dose
strength != quantity to take
strength != frequency
strength != duration
strength != administration instruction
```

### Tradeoff

A structured numeric/unit representation would improve computation and validation but creates a medication-dose model that requires additional unit/formulation governance. The initial immutable issuance record therefore preserves the clinician-authored strength display under strict syntactic bounds without claiming machine-understood dose semantics.

---

## 12. Frequency semantics

### Decision

Use one bounded clinician-authored display string in v1.

The server does not translate frequency text into a scheduling engine or infer clinical timing.

Freeze:

```text
frequency != dose
frequency != duration
frequency != route
frequency != administration instruction
```

A controlled schedule vocabulary may be added only in a separately governed schema/version if required.

---

## 13. Route, duration and instructions

Prior decisions remain unchanged:

```text
route: DEFER
duration: DEFER
generic free-text instructions: DEFER
```

Conventional prescription-system expectations do not justify reopening those fields without a bounded semantic contract.

---

## 14. Active-medication separation

Creating a canonical Prescription must **not**:

- insert into `patient_medications`;
- update `patient_medications`;
- assert that the patient is currently taking the medicine;
- alter emergency/current-medication projections;
- create adherence state;
- create administration state;
- mark any legacy Medication row active/inactive;
- infer a longitudinal medication lifecycle.

Freeze:

```text
PRESCRIPTION ISSUANCE != ACTIVE MEDICATION STATE
```

Legacy `Medication` remains a separate model/domain.

---

## 15. FHIR contract

### 15.1 Current behavior

Current FHIR export reads legacy Medication data and converts it to `MedicationRequest`.

Because legacy Medication does not carry an authoritative FHIR lifecycle state, the converter uses:

```text
MedicationRequest.status = unknown
MedicationRequest.intent = order
```

rather than fabricating `active`.

### 15.2 Canonical Prescription boundary

Canonical Prescription must **not** be surfaced by pretending it is a legacy Medication row.

Until a dedicated canonical-Prescription exporter is implemented and qualified:

```text
CANONICAL PRESCRIPTION -> FHIR EXPORT UNSUPPORTED / OMITTED
```

That omission is preferable to semantic fabrication.

### 15.3 Future mapping direction

A future dedicated mapping may use:

- canonical medication code/display;
- `authoredOn = issued_at`;
- authoritative requester/provider;
- patient subject;
- `intent = order`;
- `status = unknown` while Nexa has no lifecycle authority, if the chosen FHIR profile accepts that truthful value.

It must never set `status = active` solely because issuance occurred.

FHIR interoperability does not create internal lifecycle truth.

---

## 16. Timeline contract

Future creation may emit:

```text
event_type = PRESCRIPTION
event_ref_id = prescription_id
occurred_at = issued_at
```

Timeline remains a presentation projection only.

Safe summary examples:

```text
Prescription issued
Prescription issued (3 items)
```

Do not place the complete medication list, strengths or frequencies into generic Timeline summary text.

The Prescription aggregate is authoritative.

---

## 17. Audit contract

Future prescription audit remains structural/value-safe.

Allowed structural metadata may include:

- provider ID;
- patient internal ID where required by existing audit partitioning;
- hospital/facility ID;
- clinical session ID;
- Encounter ID;
- Prescription ID;
- item count;
- operation = `WRITE_PRESCRIPTION`;
- mutation operation = `treatment.write_prescription.v1`;
- medication catalog version;
- medication-scope policy version.

Do **not** include in generic audit metadata:

- medication code if it would expose clinical content beyond structural necessity;
- medication display/name;
- strength;
- frequency;
- item text;
- route;
- duration;
- patient-facing instructions.

Clinical values belong in the Prescription aggregate.

---

## 18. Idempotency contract

Future operation:

```text
treatment.write_prescription.v1
```

Use existing:

```text
public.mutation_idempotency
```

The canonical request hash must bind the complete semantic aggregate and authority context, including:

- exact Treatment Session / ClinicalAccessSession identity;
- exact `WRITE_PRESCRIPTION`;
- canonical Encounter ID;
- provider ID;
- patient ID;
- hospital ID;
- medication catalog version;
- medication-scope policy version;
- ordered items;
- item sequence;
- medication_code;
- clinician-authored strength;
- clinician-authored frequency;
- every other clinically relevant submitted field introduced by that API version.

`medication_display` is server-resolved and must be deterministically bound to the chosen code/catalog version.

Behavior:

```text
same key + identical canonical request -> replay original logical result
same key + different aggregate/authority -> conflict
```

Two distinct later clinician-authorized issuance events may contain identical item content. Do not create content-based uniqueness across prescriptions.

---

## 19. Transaction / atomicity contract

Freeze one database transaction:

```text
live authority validation
-> authority lock/revalidation
-> medication catalog/version validation
-> idempotency reservation
-> Prescription header
-> ordered PrescriptionItems
-> TimelineEvent
-> value-safe audit outbox
-> idempotency completion
-> commit
```

Any failure:

```text
ROLLBACK ALL
```

Never persist a partial item list.

The catalog version used for authorization must remain stable for the transaction. A mid-transaction policy/catalog change must not produce an aggregate authorized under a mixture of versions.

---

## 20. Encounter requirement

Canonical Prescription issuance requires an existing canonical `ClinicalEncounter`.

No:

- implicit Encounter creation;
- fabricated historical Encounter;
- external-document Encounter;
- patient-generated Encounter;
- caller-selected unrelated Encounter.

The locked Encounter must agree with the Treatment Session authority on:

```text
clinical_session_id
patient_id
provider_id
hospital_id
encounter_id
```

---

## 21. Professional eligibility revalidation

Future prescription mutation must use the already-implemented Treatment Session clinical-session gate for:

```text
WRITE_PRESCRIPTION
```

That gate maps the operation to:

```text
ClinicalCapability.PRESCRIBE_MEDICATION
```

and revalidates current prescribing eligibility.

Do not create a second prescriber-eligibility path.

Patient approval never freezes professional eligibility.

---

## 22. Creation-only lifecycle

First canonical version remains:

```text
IMMUTABLE ISSUANCE FACT ONLY
```

No endpoint in the initial creation slice may:

- edit;
- cancel;
- discontinue;
- supersede;
- renew;
- refill;
- complete;
- mutate an issued item's text.

An issuance mistake must never be silently corrected by SQL UPDATE.

---

## 23. Correction/error strategy

### Frozen strategy

The original issuance remains immutable historical evidence.

A future safety operation must model retraction/voiding as a **separate append-only clinical event/relationship**, not an UPDATE of the original Prescription.

Conceptually:

```text
original Prescription remains immutable
+
future RETRACT/VOID event references prescription_id
+
future corrective Prescription, if clinically appropriate, is a new issuance
```

This slice does not define or implement that future route/schema.

For the first creation-only implementation, no UI or API may imply that an erroneous issuance can be edited in place. Production rollout planning must include the separately governed retraction/void operation before Nexa presents canonical Prescription as a complete prescribing lifecycle.

---

## 24. External prescriptions

Patient-uploaded/extracted prescriptions remain:

```text
DocumentReference / external evidence
```

No direct promotion to canonical Prescription.

A clinician reviewing external evidence may issue a **new** Nexa Prescription only through current live authority and the future medication-scope gate.

Never preserve an external author/date as though it were Nexa clinician authorship.

Patient corrections to extracted text are never clinician prescribing authority.

---

## 25. Duplicate/repeat issuance

Two separately authorized Prescription issuance events may have identical item contents.

That is allowed.

The distinction is the clinician-authorized issuance event, time, Encounter, and idempotency operation—not uniqueness of medication content.

Idempotency prevents accidental retry duplication. It does not prohibit a legitimate later reissuance.

---

## 26. Provider UI boundary

A future provider UI may collect only the fields authorized by the v1 aggregate:

- ordered item list;
- catalog-selected medication identity;
- server display;
- bounded clinician-authored strength;
- bounded clinician-authored frequency.

No:

- arbitrary medication-name text;
- AI medication suggestions;
- dose recommendations;
- default drug recommendations;
- silent substitutions;
- autocomplete sourced from an unapproved vocabulary;
- formulary recommendations.

Autocomplete is permitted only if it is a view over the approved server medication catalog and cannot introduce unregistered values.

No provider UI is implemented in this slice.

---

## 27. Patient UI boundary

Future patient read semantics may show:

- clinician-authored Prescription;
- issuing timestamp;
- clinician/facility provenance;
- item display;
- clinician-authored strength;
- clinician-authored frequency.

Patient display creates no write authority and no professional prescribing authority.

Patient UI is not implemented in this slice.

---

## 28. Medication catalog ownership and release contract

A medication-scope catalog is now a required security/regulatory dependency.

### 28.1 Owner

Primary owner:

```text
Nexa medication-safety / clinical-regulatory governance
```

Engineering implements the approved artifact but cannot unilaterally classify a medicine as allowed.

A release requires independent clinical/regulatory approval.

### 28.2 Source policy

Permitted release sources are authoritative, versioned official materials such as:

- CDSCO Drugs Rules and final Gazette notifications;
- India Code Drugs and Cosmetics / NDPS Acts and Rules;
- operative NMC/MoHFW telemedicine rules/guidelines and official amendments.

Pharmacy websites, blogs, search-engine summaries, LLM outputs and scraped retail catalogs cannot be authorization sources.

### 28.3 Minimum catalog fields

Each accepted medication entry must carry enough facts to support the policy, conceptually including:

```text
medication_code
canonical_display
generic_or_brand_semantics
effective_from
effective_until
regulatory_schedule = NONE | G | H | H1 | X | UNKNOWN
ndps_controlled
telemedicine_class = O | A | B | PROHIBITED | UNKNOWN
telemedicine_exception_or_amendment_reference
special_recordkeeping_required
specialist_or_high_risk_category
anti_cancer_flag
h2_traceability_metadata_or_reference
v1_scope = UNIVERSAL_ALLOWED | DENIED
official_source_references
source_effective_dates
entry_review_status
```

Exact implementation schema is deferred.

### 28.4 Version

Every approved release has an immutable catalog version.

A Prescription must persist/bind the exact catalog version used for authorization.

### 28.5 Release cadence

- scheduled official-source review at least weekly;
- immediate/out-of-band review when CDSCO/NMC/MoHFW/India Code publishes a medication-scope change;
- no automatic activation of scraped regulatory changes;
- final Gazette status/effective date must be distinguished from draft publication.

### 28.6 Integrity verification

Each catalog release must retain:

- official source URL/identifier;
- publication/effective date;
- normalized release manifest;
- cryptographic digest of the release artifact;
- reviewer/approver identities;
- approval time;
- policy version.

Two-person clinical/regulatory approval is preferred for medication-scope changes.

### 28.7 Rollback and emergency restriction

Catalog releases are immutable.

The active version may move back only to another previously approved safe release under governed rollback.

Existing Prescriptions retain their original catalog-version binding.

If a medicine becomes newly restricted/prohibited, new issuance must fail closed immediately under a new approved deny/restriction release; old Prescription history is not rewritten.

---

## 29. Decision matrix

This matrix is the binding Nexa v1 product decision. **DENY** can be stricter than general legal permission. **BLOCKED** means Nexa currently lacks the machine authority necessary to make the decision safely.

| Question | Decision | Exact rationale / source basis |
| --- | --- | --- |
| Arbitrary free-text medication | **BLOCKED** | No authoritative classifier; free text cannot enforce current H/H1/X/NDPS or telemedicine list changes. |
| Catalog-selected medication | **ALLOW** | Allowed only when exact entry is in an approved active catalog version and marked `V1_UNIVERSAL_ALLOWED`; server code, not display text, is authority. |
| Ordinary prescription medicines | **BLOCKED** | They may depend on Schedule H/H1 and telemedicine mode/type. Current Encounter care mode is UNKNOWN and no catalog exists. |
| Schedule G | **DENY** | Nexa v1 excludes medicines carrying the Rule 97 medical-supervision warning from the universal first scope. |\n| Schedule H | **DENY** | Nexa v1 intentionally excludes prescription-schedule medicines from the universal/unknown-mode first scope. Rule 65/97 establishes prescription controls; later expansion requires catalog + care-mode policy. |
| Schedule H1 | **DENY** | Nexa v1 excludes H1; Rule 65 imposes additional downstream retail-supply recordkeeping and H1 membership changes over time. G.S.R. 588(E), G.S.R. 95(E), G.S.R. 377(E). |
| Schedule X | **DENY** | Stricter Nexa policy; Schedule X has special controls and is prohibited by the operative telemedicine base matrix. |
| NDPS narcotic/psychotropic substances | **DENY** | Separate NDPS Act/Rules controls and telemedicine prohibition; v1 aggregate intentionally does not model those special prescription semantics. |
| Oncology / specialist therapies | **DENY** | No specialist/formulary/context classifier; official telemedicine materials identify anti-cancer drugs as a prohibited/high-risk example. Narrow v1 excludes them. |
| Unknown/unclassified medication | **DENY** | Unknown never falls through to ordinary medicine. |
| Drugs with special prescription/recordkeeping obligations | **DENY** | v1 does not model category-specific prescription/recordkeeping requirements. |
| OTC / ordinary List-O-style approved catalog entry | **ALLOW** | Only after exact catalog validation and only when marked `V1_UNIVERSAL_ALLOWED`, not emergency-only, restricted, or otherwise excluded. |
| Emergency medication scenario | **DENY** | Routine Prescription persistence must not become emergency authority; emergency care has separate product/clinical policy. |
| Remote/telemedicine Encounter | **BLOCKED** | Current Encounter does not durably establish remote mode/type. Modality-dependent medicine scope cannot be authorized. Universal catalog subset avoids relying on this fact, but the system cannot label a particular Encounter remote today. |
| Care-mode UNKNOWN | **BLOCKED** | Modality-dependent medicines fail closed under UNKNOWN. Only a separately catalog-approved universal ANY-mode subset may later bypass the need for modality knowledge. |

---

## 30. Governance qualification constraints

This slice is governance-only.

It must create:

```text
ZERO migrations
ZERO Prescription tables
ZERO PrescriptionItem tables
ZERO WRITE_PRESCRIPTION persistence routes
ZERO medication-catalog ingestion
ZERO Encounter modality fields
ZERO provider/patient prescription UI
ZERO canonical FHIR MedicationRequest exporter
```

Alembic head must remain:

```text
20260919_prescriber_eligibility
```

---

## 31. Final implementation verdict

```text
WRITE_PRESCRIPTION PERSISTENCE BLOCKED
```

Smallest remaining prerequisite:

```text
MEDICATION CLASSIFICATION CATALOG REQUIRED
```

The catalog is the smallest prerequisite because it is required even for a narrow care-mode-independent first scope. Without it, Nexa cannot machine-enforce its exclusions.

A future care-mode authority is additionally required **before expanding** to medication categories whose lawful/product permission depends on IN_PERSON/VIDEO/AUDIO/TEXT or first/follow-up consultation status.

The next Task-0 action is therefore **not** Prescription persistence. It is a bounded medication-catalog governance/design slice that defines the first reviewed release artifact, classification source mapping, versioning/integrity process, and exact `V1_UNIVERSAL_ALLOWED` entries without yet creating clinical Prescription persistence.

Core invariant:

```text
Nexa must machine-enforce the medication scope it claims to support
before a clinician can persist a canonical Prescription.
```
