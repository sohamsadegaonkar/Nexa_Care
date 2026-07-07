# Nexa Care V2 — Structured Patient Record & Provenance Schema

**Module Owner:** Patient Records Backend (Workstream 3)  
**Coordinating Partners:** Lead Architect (WS1), AI Pipeline / Adjudication (WS4/WS5)  
**Version:** `v2.0.0-alpha`  
**Status:** LOCKED & ENFORCED

---

## 1. Architectural Provenance Governance (Invariant 3)

To ensure clinical safety and data integrity, every structured clinical observation table (`Vitals`, `Medication`, `LabResult`, `Allergy`) strictly enforces **Data Provenance**. No clinical observation may be saved without provenance columns:
1. `source`: Must indicate ingestion origin (`manual` or `ai_extracted`).
2. `confidence`: Float `[0.0, 1.0]`, indicating AI extraction assurance (nullable when `source == 'manual'`).
3. `risk_level`: Clinical risk classification (`LOW_RISK`, `MEDIUM_RISK`, `HIGH_RISK`, `CRITICAL_RISK`). **For `Allergy` rows, `risk_level` strictly defaults to `HIGH_RISK`.**
4. `source_document_id`: UUID reference linking to the originating `DocumentReference` file (nullable when `source == 'manual'`).

---

## 2. Canonical Relational Models

### 2.1 PatientRecord (`patient_records`)
Top-level anchor linking structured clinical entities to a patient account.

| Column Name | Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | No | `gen_random_uuid()` | Primary key |
| `patient_id` | UUID | No | | Target patient account UUID |
| `created_at` | TIMESTAMPTZ | No | `now()` | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | No | `now()` | Last modification timestamp |

---

### 2.2 Vitals (`patient_vitals`)
Stores quantitative vital observations (e.g., blood pressure, heart rate, glucose).

| Column Name | Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | No | `gen_random_uuid()` | Primary key |
| `patient_id` | UUID | No | | Foreign key to patient account |
| `type` | VARCHAR(32) | No | | Observation type (`BP`, `sugar`, `HR`, `temp`, `SpO2`) |
| `value` | VARCHAR(64) | No | | Recorded measurement (`120/80`, `98.6`) |
| `unit` | VARCHAR(32) | No | | Unit of measurement (`mmHg`, `mg/dL`, `bpm`, `C`) |
| `recorded_at` | TIMESTAMPTZ | No | `now()` | Timestamp of observation |
| `source` | VARCHAR(20) | No | `'manual'` | Provenance (`manual` \| `ai_extracted`) |
| `confidence` | FLOAT | Yes | `NULL` | AI confidence score `[0.0, 1.0]` |
| `risk_level` | VARCHAR(20) | No | `'LOW_RISK'` | Adjudicated clinical risk tier |
| `source_document_id`| UUID | Yes | `NULL` | Foreign key to `DocumentReference` |

---

### 2.3 Medication (`patient_medications`)
Stores active or historical pharmaceutical prescriptions.

| Column Name | Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | No | `gen_random_uuid()` | Primary key |
| `patient_id` | UUID | No | | Foreign key to patient account |
| `name` | VARCHAR(128)| No | | Drug name (e.g. `Lisinopril`) |
| `strength` | VARCHAR(64) | No | | Dosage strength (`10mg`, `500mg`) |
| `frequency` | VARCHAR(64) | No | | Administration regimen (`Daily`, `BID`) |
| `prescribed_at`| TIMESTAMPTZ| No | `now()` | Timestamp of prescription |
| `source` | VARCHAR(20) | No | `'manual'` | Provenance (`manual` \| `ai_extracted`) |
| `confidence` | FLOAT | Yes | `NULL` | AI confidence score `[0.0, 1.0]` |
| `risk_level` | VARCHAR(20) | No | `'MEDIUM_RISK'`| Adjudicated clinical risk tier |
| `source_document_id`| UUID | Yes | `NULL` | Foreign key to `DocumentReference` |

---

### 2.4 LabResult (`patient_lab_results`)
Stores quantitative and qualitative diagnostic laboratory evaluations.

| Column Name | Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | No | `gen_random_uuid()` | Primary key |
| `patient_id` | UUID | No | | Foreign key to patient account |
| `test_name` | VARCHAR(128)| No | | LOINC / test label (`hba1c`, `cholesterol`) |
| `value` | VARCHAR(64) | No | | Observed value (`6.8`, `195`) |
| `unit` | VARCHAR(32) | No | | Unit of measurement (`%`, `mg/dL`) |
| `reference_range`| VARCHAR(64)| No | | Standard clinical bounds (`4.0-5.6 %`) |
| `is_abnormal` | BOOLEAN | No | `false` | Flag for out-of-bounds observations |
| `recorded_at` | TIMESTAMPTZ | No | `now()` | Specimen / analysis timestamp |
| `source` | VARCHAR(20) | No | `'manual'` | Provenance (`manual` \| `ai_extracted`) |
| `confidence` | FLOAT | Yes | `NULL` | AI confidence score `[0.0, 1.0]` |
| `risk_level` | VARCHAR(20) | No | `'MEDIUM_RISK'`| Adjudicated clinical risk tier |
| `source_document_id`| UUID | Yes | `NULL` | Foreign key to `DocumentReference` |

---

### 2.5 Allergy (`patient_allergies`)
Stores immunological sensitivities. **Strict Rule: All allergy records default to `HIGH_RISK`.**

| Column Name | Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | No | `gen_random_uuid()` | Primary key |
| `patient_id` | UUID | No | | Foreign key to patient account |
| `allergen` | VARCHAR(128)| No | | Agent name (`Penicillin`, `Peanuts`) |
| `severity` | VARCHAR(32) | No | | Reaction severity (`Mild`, `Severe`, `Anaphylaxis`) |
| `source` | VARCHAR(20) | No | `'manual'` | Provenance (`manual` \| `ai_extracted`) |
| `confidence` | FLOAT | Yes | `NULL` | AI confidence score `[0.0, 1.0]` |
| `risk_level` | VARCHAR(20) | No | `'HIGH_RISK'` | Strict WS5 default risk tier |
| `source_document_id`| UUID | Yes | `NULL` | Foreign key to `DocumentReference` |

---

### 2.6 DocumentReference (`document_references`)
Records uploaded files and links them to background PyTorch extraction jobs.

| Column Name | Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | No | `gen_random_uuid()` | Primary key |
| `patient_id` | UUID | No | | Foreign key to patient account |
| `document_type`| VARCHAR(64) | No | | Category (`LAB_REPORT`, `PRESCRIPTION`) |
| `uploaded_at` | TIMESTAMPTZ | No | `now()` | Timestamp of file upload |
| `storage_ref` | VARCHAR(256)| No | | Object store reference URI |
| `extraction_job_id`| UUID | Yes | `NULL` | WS4 background pipeline job identifier |

---

### 2.7 TimelineEvent (`timeline_events`)
Unified chronological view aggregating clinical encounters, lab commits, and document ingestion milestones.

| Column Name | Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | No | `gen_random_uuid()` | Primary key |
| `patient_id` | UUID | No | | Foreign key to patient account |
| `event_type` | VARCHAR(64) | No | | Category (`ENCOUNTER`, `LAB_RESULT`, `DOCUMENT_INGESTED`)|
| `event_ref_id` | UUID | Yes | `NULL` | Underlying entity primary key |
| `occurred_at` | TIMESTAMPTZ | No | `now()` | Historical occurrence timestamp |
| `source` | VARCHAR(20) | No | `'manual'` | Provenance (`manual` \| `ai_extracted`) |
| `summary` | VARCHAR(512)| No | | Human-readable event headline |

---

## 3. Write Authorization Policy (Alpha vs. Pilot Behavior)

To ensure strict zero-trust data governance across Workstream 3 write routes (`/records/vitals`, `/records/medications`, `/records/labs`, `/records/allergies`, `/records/documents`), the authorization policy separates immediate demo execution requirements from long-term production requirements:

### Alpha Behavior (`v2.0.0-alpha`)
Provider write routes require an authenticated healthcare provider identity (`Authorization: Bearer <provider_session_token>`) and strictly enforce an **audit-before-write guarantee** (`PATIENT_RECORD_APPEND_ATTEMPT` emitted prior to Postgres execution and `PATIENT_RECORD_APPEND_SUCCESS` emitted upon commit).

### Pilot Behavior (`v2.1.0-pilot`)
Provider write routes must also verify authorization against one of the following explicit policy gates before permitting mutations:
1. **Active Treatment Relationship:** Verified real-time care context or encounter session.
2. **Explicit Consent Grant:** Cryptographically signed patient consent capability (`X-Consent-Token` with `clinical_append` purpose).
3. **Data-Operator Review Policy:** Verified human steward adjudication role for AI pipeline commits (`require_role("operator")`).

---

## 4. Ingestion Pipeline Commit Architecture & Roadmap

### Alpha Ingestion Safeguards (`v2.0.0-alpha`)
1. **Strict Status Adjudication Gate:** `ingest_extracted_fields(...)` strictly rejects any observation where `status == "needs_review"` or `status == "rejected"`. Only `auto_approved`, `approved`, or `edited` fields may enter the patient record.
2. **Job-Level Idempotency:** The ingestion engine inspects existing `TimelineEvent` records matching `(patient_id, event_ref_id == job_id, source == "ai_extracted")`. If found, ingestion terminates immediately returning zero duplicates.

### Pilot Roadmap (`v2.1.0-pilot`)
Before entering clinical pilot deployment, the ingestion engine will introduce two structural enhancements:
1. **Dedicated Ingestion Marker Table (`ingestion_job_commits`):**
   A dedicated transaction marker table separating job-level commit metadata from individual clinical observations:
   - `job_id` (UUID, Primary Key)
   - `patient_id` (UUID, Foreign Key)
   - `committed_at` (TIMESTAMPTZ)
   - `committed_by` (VARCHAR / Actor UID)
   - `ingested_count` (INTEGER)
2. **Pre-Commit Clinical Conflict Detection:**
   Automated verification rules executed prior to commit execution. Any detected discrepancy halts auto-approval and routes the job to human review:
   - **Medication Discrepancy:** Same active medication prescribed with conflicting dosage or frequency.
   - **Lab Discrepancy:** Same diagnostic test recorded with conflicting value or date within a narrow temporal window.
   - **Allergy Contradiction:** Uploaded observation contradicts or attempts to remove an established severe allergy.
   - **Identity Mismatch:** Document header demographics (name, DOB, ABHA ID) fail fuzzy match against target patient profile.

---

## 5. Dual-Perspective Audit Ledger Access Architecture

To ensure audit visibility without cluttering patient transparency screens, the Nexa Care V2 architecture enforces two distinct audit retrieval perspectives over `system_audit`:

### 1. Patient Access History (`GET /api/v2/patient/me/access-history`)
Exposes only human healthcare provider view, decrypt, read, and emergency break-glass events (`PATIENT_RECORD_VIEW_*`, `PATIENT_RECORD_READ_SUCCESS`, `CONSENT_GATED_DECRYPT_*`, `BREAK_GLASS_*`). Filters out background technical writes, pipeline extraction jobs, and device key operations to provide patients with an intelligible record of clinical accesses.

### 2. Admin & Auditor Audit Trail (`GET /api/v2/patient/{id}/audit-trail`)
Gated strictly by administrative authorization (`require_role("admin")`), exposing the complete, unfiltered hash-chained audit ledger (`system_audit`) for a patient account. Includes all record append attempts, pipeline extraction commits, human field adjudications, device registrations/revocations, and consent challenges required for regulatory compliance and forensic audit investigations.
