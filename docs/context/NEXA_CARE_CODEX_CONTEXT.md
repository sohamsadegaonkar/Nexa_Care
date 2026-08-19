# Nexa Care — Canonical Product & Engineering Context for Codex

> **Purpose:** This file is the product/engineering source of truth for Codex when working on Nexa Care. It exists to prevent workflow drift and to ensure the implementation matches the actual B2C/B2B product model.
>
> **Repository:** https://github.com/sohamsadegaonkar/Nexa_Care
>
> **Branch policy:** Work directly on `main`. Do not create branches or pull requests unless explicitly requested. Do not commit or push until qualification gates pass and explicit authorization is given.
>
> **Current GitHub checkpoint at time of writing:** `9a504b430af0a8b2a0f74eaa99dbe845baf846b5`
>
> **Important:** The SHA above is only a checkpoint. Before every task, fetch/inspect current `main` and treat the repository as the implementation source of truth.

---

## 1. Product Definition

Nexa Care has **two connected business models** that converge on the same longitudinal patient record.

### B2C — Patient / Individual

A normal person can join Nexa Care even if their doctor, clinic, or hospital is not connected to Nexa Care.

The B2C product allows a patient to:

- Register for a Nexa Care account.
- Verify identity/authentication using the approved login flow.
- Maintain a longitudinal medical history.
- Import previous medical records during onboarding.
- Upload future records received from doctors/hospitals outside Nexa Care.
- View prescriptions, laboratory records, imaging reports, encounters, allergies, and other medical information in categorized form.
- See who accessed their records.
- Approve or deny doctor access requests.
- Revoke access according to policy.

The patient app must be useful even if zero nearby doctors use Nexa Care.

### B2B — Doctors, Clinics, and Hospitals

B2B onboarding includes:

- Individual doctors / small clinics.
- Multi-doctor clinics.
- Large hospitals.
- Later: hospitals connected through HMS/HIS integrations.

A Nexa-connected clinician should create **new medical data as structured clinical records**, not by creating a PDF and sending it back through OCR.

The normal B2B flow is:

```text
Find patient
→ patient approves a bounded treatment session
→ doctor opens patient workspace
→ reads permitted history
→ creates/continues encounter
→ records structured clinical data
→ API/JSON transport
→ typed backend records
→ timeline updates
```

---

## 2. Core Product Rule: PDF Is an Interoperability Bridge

### External / historical healthcare data

PDF/image ingestion exists primarily to bring healthcare information **from outside Nexa Care into Nexa Care**.

Examples:

- Old prescriptions.
- Old laboratory reports.
- Discharge summaries.
- External imaging/radiology reports.
- External doctor prescriptions.
- Other legacy medical documents.

Flow:

```text
External PDF/Image
→ encrypted source/archive
→ extraction
→ field-level evidence
→ structured candidates
→ provenance-aware structured history
```

### Nexa-native healthcare data

When a Nexa-connected doctor creates a prescription, diagnosis, vital, clinical note, or encounter record:

```text
Doctor UI
→ structured form
→ JSON/API
→ typed database entities
→ patient timeline
```

Do **not** introduce:

```text
Doctor enters data
→ generate PDF
→ upload PDF
→ OCR the PDF again
```

A printable/downloadable PDF may be generated as an output representation, but it must not become the canonical source for Nexa-native structured data.

### Future HMS/HIS integration

Integrated hospitals should send structured records directly through an adapter/API boundary whenever possible.

```text
Hospital HMS/HIS
→ integration adapter
→ validated structured records
→ patient longitudinal record
```

---

## 3. Doctor-Side PDF Upload Is Not a Primary Workflow

Do **not** design the doctor dashboard around `Upload & AI Extract`.

The doctor dashboard should focus on clinical work:

- Find Patient
- Today's Patients / Active Encounters
- Start or Continue Consultation
- Pending Patient Access
- Patient Workspace
- Write Prescription
- Emergency Access

If provider-side external document import is retained, it should be a **secondary contextual action** inside a patient's Documents/External Records area, e.g.:

> Add external record

It must not be presented as the normal way a Nexa doctor records a consultation.

Likewise, internal engineering terminology such as `SOURCE_ONLY`, `QUARANTINE`, `workflow_id`, or "Source adjudication workspace" should not be exposed as top-level clinician concepts.

Use user-facing language such as:

> Needs clinical verification

---

## 4. Patient Registration and Onboarding

Nexa Care requires a real distinction between **new-user registration** and **existing-user login**.

### New patient

Target flow:

```text
Create account
→ verify phone/approved identity factor
→ create patient profile
→ accept required terms/privacy notices
→ create/bind patient record
→ enroll current device where required
→ basic health/emergency setup where appropriate
→ offer previous-record import
→ Home
```

Initial record import should allow categories such as:

- Prescription
- Lab report
- Imaging / radiology report
- Discharge summary
- Other medical record
- Skip for now

### Existing patient

```text
Phone / account identifier
→ OTP / approved authentication
→ Home
```

Do not collapse registration and login into the same product experience.

---

## 5. Real Patient Discovery for Doctors

The doctor should not need to know or type internal database UUIDs.

The current product target is secure patient discovery using appropriate identifiers such as:

- Phone number
- Nexa public patient ID
- Name + another approved identifier
- NFC
- QR
- Later: hospital MRN / institution-scoped identifier

Privacy requirements:

- Do not expose broad patient-directory search.
- Require sufficient identifying information.
- Return the minimum necessary pre-consent identity data.
- Resolve merged/canonical patient identities server-side.
- Never trust a client-provided patient identifier without authoritative resolution/binding where the architecture requires it.

---

## 6. Consent Means a Bounded Clinical Access Session

Patient approval is not merely "allow this doctor to view my records."

The intended model is:

> The patient authorizes this clinician/clinic/hospital to treat them for a bounded clinical session.

Once approved, the session may allow both **read** and **write** operations within a defined scope.

Conceptual entity:

```text
ClinicalAccessSession
- session_id
- patient_id
- provider_id
- hospital_or_clinic_id
- purpose = treatment
- encounter_id (when created/bound)
- created_at
- expires_at
- status
- allowed_operations
```

Possible operations include:

```text
READ_CLINICAL_HISTORY
READ_DOCUMENTS
CREATE_ENCOUNTER
WRITE_PRESCRIPTION
WRITE_DIAGNOSIS
WRITE_VITALS
WRITE_CLINICAL_NOTES
ORDER_INVESTIGATION
```

Exact policy names/lifetimes must be derived from the existing security/consent architecture and explicitly designed; do not hardcode a casual duration.

The session must remain:

- patient-bound
- provider-bound
- organization/hospital-bound
- purpose-bound
- expiring
- revocable
- auditable
- fail-closed

---

## 7. Doctor Patient Workspace

After patient authorization, the doctor should enter a persistent patient workspace rather than bounce through isolated demo workflows.

Target information architecture:

```text
Patient Header
Access active / expiring status

Overview | Timeline | Records | Consultation
```

### Overview

- Important allergies
- Active medications
- Important diagnoses/problems
- Recent vitals
- Recent labs
- High-priority clinical alerts

### Timeline

Chronological references to typed records from all sources.

### Records

Categorized clinical records, not one generic bucket.

### Consultation

The current clinical encounter where the doctor records new structured data.

---

## 8. Medical Record Taxonomy

Do not treat all health data as a generic "medical record".

The system should keep typed domains such as:

```text
Patient
├── Encounters
│   ├── Consultation
│   ├── Diagnosis
│   ├── Clinical Notes
│   └── Follow-up
├── Prescriptions
├── Medications
├── Laboratory
├── Vitals
├── Imaging
│   ├── X-ray
│   ├── CT
│   ├── MRI
│   └── Ultrasound
├── Procedures
├── Discharge Summaries
├── Allergies
├── Clinical Notes
└── Source Documents / External Records
```

The longitudinal timeline should link these typed records chronologically.

Do not implement a single giant JSON blob as the canonical clinical store when proper relational/typed entities are appropriate.

JSON is primarily a **transport representation** for structured APIs.

---

## 9. Imaging Scope

For the controlled pilot, Nexa Care does not need to become a full PACS/DICOM storage platform.

Initial imaging support can represent:

- modality
- body part
- date
- facility
- radiology report
- impression
- source/reference document

Actual DICOM/PACS integration may be a later hospital-integration milestone.

---

## 10. Provenance Is First-Class

Every clinical datum must retain meaningful source/provenance.

Conceptual provenance classes include:

```text
PATIENT_IMPORTED
EXTERNAL_DOCUMENT_EXTRACTED
CLINICIAN_VERIFIED_IMPORT
NEXA_CLINICIAN_CREATED
HOSPITAL_HMS_IMPORTED
LAB_INTEGRATION
```

Do not expose raw enum names to users.

User-facing examples:

- "Created by Dr. Sharma · Nexa Clinic"
- "Imported by you from an external report"
- "Verified against source by Dr. Sharma"

Never blur patient-imported or AI-extracted information into clinician-created/verified information.

---

## 11. Existing Document-Processing Safety Work Remains Valid

The completed local E2E qualification commit is:

```text
18a9290aad16beef48545e210f6e802b2ebaf71c
fix(pipeline): qualify local document processing e2e
```

It proved the existing provider-authorized document-processing path through:

```text
HTTP upload
→ consent authorization
→ encrypted storage
→ extraction
→ evidence
→ encrypted candidate
→ SOURCE_ONLY
→ zero pre-adjudication clinical truth
→ human adjudication
→ typed clinical record
→ timeline
→ audit/outbox
```

AUTO_COMMIT remained disabled and unapproved.

Do not discard or bypass this safety architecture when correcting the product flows.

The extraction engine, evidence model, encrypted candidate persistence, routing, audit, PostgreSQL integrity, adjudication, recovery, and rollback work should be **reused**.

---

## 12. Current Product Gaps Identified

Before UI freeze/AWS physical qualification, the following product gaps need explicit attention:

1. Proper patient registration/onboarding.
2. Real secure patient discovery/search.
3. Bounded clinical treatment/access session.
4. Structured encounter creation.
5. Structured prescription workflow.
6. Typed/categorized record presentation.
7. Correct patient external-record import UX.
8. Removal/repositioning of doctor-side primary PDF upload.
9. Contextual clinician verification instead of engineering-style adjudication navigation.
10. Major UI/UX refinement of both patient app and clinician web.

When auditing the current repository, classify each required behavior as:

```text
EXISTS
PARTIAL
WRONG_FLOW
MISSING
```

Do not assume "screen exists" means "product behavior is complete."

---

## 13. UI/UX Direction

### Patient app

The patient app should feel like a personal healthcare product, not mainly a consent utility.

Target navigation concepts:

```text
Home
Timeline
Records
Prescriptions
Reports
Doctors / Care
Access & Privacy
Profile
```

The UI should include clear:

- registration/onboarding
- import previous records
- consent requests
- approve/deny
- access history
- categorized record views
- loading/empty/error states
- senior-friendly typography and touch targets

### Doctor web

The doctor website should feel like a clinical workspace.

Target journey:

```text
Login
→ Dashboard
→ Find Patient
→ Request Treatment Access
→ Patient Approves
→ Patient Workspace
→ Consultation
→ Structured Clinical Write
→ Timeline Updated
```

Do not expose internal engineering terms or low-level identifiers unless operationally necessary.

---

## 14. Revised Development Order

Current preferred sequence:

```text
Existing safe backend foundation
→ product architecture correction
→ registration
→ real patient search
→ clinical access session
→ record taxonomy + encounter/prescription primitives
→ patient external-record import flow
→ patient app + doctor web UI/UX redesign
→ frontend functional qualification
→ freeze release candidate
→ isolated AWS pilot deployment
→ physical Android qualification
→ extraction accuracy benchmark qualification
→ HMS/HIS integration
→ HOD demonstration
→ controlled single-hospital pilot
```

Do not rush into AWS/device qualification before the UI/product flow intended for the pilot is frozen, otherwise qualification will need to be repeated after product redesign.

---

## 15. Engineering/Safety Rules

Always preserve:

- patient/tenant isolation
- provider/hospital authorization
- consent/session scope
- expiry/revocation
- erasure checks
- encryption
- audit integrity
- evidence provenance
- source-document traceability
- transactional integrity
- idempotency
- human review boundary for imported/extracted data

Never allow AI/OCR output to silently become authoritative clinical truth.

Keep:

```text
AUTO_COMMIT_ENABLED = False
AUTO_COMMIT_APPROVED = False
```

unless separately authorized through governance.

---

## 16. Repository Workflow

Before any engineering task:

1. Inspect current `main`.
2. Record `HEAD` and `origin/main`.
3. Inspect relevant implementation and tests.
4. Cross-check this product context against current code.
5. Implement the smallest correct delta.
6. Run focused tests.
7. Run broader regression gates as required.
8. Do not commit until explicitly authorized.

Do not create branches or PRs unless explicitly requested.

Prefer one optimal implementation path over multiple speculative alternatives.

---

## 17. Core Product Statement

> **Nexa Care is a consent-controlled longitudinal health-record platform with two acquisition/data-generation sides. B2C lets individuals build and control their medical history even outside the Nexa network. B2B lets doctors, clinics, and hospitals create new healthcare data directly as structured records. External PDFs/images are bridges for importing legacy/out-of-network information; Nexa-native healthcare should be structured from creation. Patient authorization creates a bounded clinical session in which an approved clinician can read permitted history and add new treatment records. Every record remains typed, provenance-aware, and auditable.**