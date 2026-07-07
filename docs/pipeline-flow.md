# AI Ingestion Pipeline End-to-End Specification (Workstream 4)

This document defines the authoritative architecture, data models, and lifecycle flow for the Nexa Care V2 AI Ingestion Pipeline.

---

## 1. End-to-End Lifecycle Flow

```
Upload → Store metadata → Classify type → Extract fields →
Attach confidence + source page (WS5) → Validate + risk score (WS5) →
Auto-approve safe fields → Route risky/uncertain to review queue →
Human review (WS8) → Commit approved fields to patient record (WS3) → Audit
```

### Phase 1: Document Upload & Storage (`POST /api/v2/pipeline/documents/upload`)
- Authenticated provider or patient uploads a clinical document (`LAB_REPORT`, `PRESCRIPTION`, `DISCHARGE_SUMMARY`).
- The payload size is checked against the global **20 MB hard cap** (`ContentSizeLimitMiddleware`). Oversized payloads return `413 Request Entity Too Large`.
- A `DocumentStorage` record is created with object storage references (`s3://...`).
- An `ExtractionJob` (`status="queued"`) is initialized.

### Phase 2: Classification & PyTorch Extraction (`ExtractionJob` Processing)
- Background worker picks up the job (`status="extracting"`).
- Document type classification runs (`LAB_REPORT`, `PRESCRIPTION`, etc.).
- Optical Character Recognition (OCR) and PyTorch extraction generate candidate medical observations.
- Each observation is bound to an `ExtractedField` structure with mandatory spatial and scoring slots: `confidence` $[0.0, 1.0]$, `source_page` (1-indexed), and `source_bbox` $[x, y, w, h]$.

### Phase 3: Clinical Validation & Scoring (`WS5` Integration)
- Candidate fields pass through reference range rules (`validation_result.reference_range`).
- Risk scoring assigns one of four clinical tiers: `LOW_RISK`, `MEDIUM_RISK`, `HIGH_RISK`, `CRITICAL_RISK`.
- **Strict Adjudication Rules (Alpha vs. Pilot Behavior):**
  - **Alpha Rule (`v2.0.0-alpha`):**
    - `LOW_RISK`: May auto-approve if `confidence >= 0.95`, validation is clean (`is_valid == true`), source evidence exists (`source_page` / `source_bbox`), and no clinical conflict exists.
    - `MEDIUM_RISK`: May auto-approve only if `confidence >= 0.97`, validation is clean, source evidence exists, and no clinical conflict exists.
    - `HIGH_RISK` / `CRITICAL_RISK` / Conflicting Data / Failed Validation: Always route to human review (`status="needs_review"`).
  - **Pilot Rule (`v2.1.0-pilot`):**
    - `MEDIUM_RISK` strictly defaults to human review unless hospital organizational governance policy explicitly enables auto-approval for low-consequence diagnostic parameters.
  - **Human Review Routing:** Any candidate field failing auto-approval criteria is assigned `status="needs_review"` and linked to a `ReviewQueueItem`.

### Phase 4: Human Steward Adjudication (`GET /api/v2/pipeline/review-queue`, `POST /review`)
- Healthcare stewards review flagged fields via frontend review UI (`WS8`).
- Stewards execute `approve`, `reject`, or `edit` actions (`corrected_value`).
- Adjudicated fields transition to `status="approved"`, `status="rejected"`, or `status="edited"`.

### Phase 5: Patient Record Commit & Hard Audit (`POST /api/v2/pipeline/jobs/{job_id}/commit`)
- Only fields with `status` in `{"auto_approved", "approved", "edited"}` are passed to `ingest_extracted_fields(...)` (`WS3`). Unreviewed (`needs_review`) or `rejected` fields are strictly blocked (`400 Bad Request`).
- Approved fields are routed by clinical name into persistent sub-models (`Vitals`, `Medication`, `LabResult`, `Allergy`).
- Chronological `TimelineEvent` records are appended with full provenance display.
- Every ingested observation triggers an immutable audit log entry (`EXTRACTED_DATA_INGESTED`) chained into the Supabase ledger.

---

## 2. Canonical Data Models (`app/models/pipeline.py`)

### 2.1 DocumentStorage (`document_storage`)
Records metadata for uploaded raw files.
- `id` (UUID, Primary Key)
- `patient_id` (UUID, Indexed)
- `storage_ref` (VARCHAR 256)
- `content_type` (VARCHAR 64)
- `size` (INTEGER)
- `uploaded_at` (TIMESTAMPTZ)

### 2.2 ExtractionJob (`extraction_jobs`)
Tracks background pipeline execution.
- `id` (UUID, Primary Key)
- `patient_id` (UUID, Indexed)
- `document_id` (UUID, Foreign Key to `document_storage.id`)
- `document_type` (VARCHAR 64)
- `status` (VARCHAR 32: `queued`, `extracting`, `scored`, `review_pending`, `committed`, `failed`)
- `created_at` (TIMESTAMPTZ)
- `completed_at` (TIMESTAMPTZ, Nullable)

### 2.3 ExtractedFieldRecord (`extracted_fields`)
Persistent storage for the canonical `ExtractedField` schema (WS1 single source of truth).
- `id` (UUID, Primary Key)
- `job_id` (UUID, Foreign Key to `extraction_jobs.id`, Indexed)
- `field_name` (VARCHAR 128)
- `raw_value` (VARCHAR 512)
- `normalized_value` (VARCHAR 512, Nullable)
- `confidence` (FLOAT, Not Null)
- `risk_level` (VARCHAR 32, Not Null)
- `validation_result` (JSONB, Nullable)
- `source_page` (INTEGER, Default 1)
- `source_bbox` (JSONB, Nullable)
- `status` (VARCHAR 32: `auto_approved`, `needs_review`, `approved`, `rejected`, `edited`)
- `corrected_value` (VARCHAR 512, Nullable)
- `source_document_id` (UUID, Nullable)

### 2.4 ReviewQueueItem (`review_queue_items`)
Links flagged observations to human stewards.
- `id` (UUID, Primary Key)
- `job_id` (UUID, Indexed)
- `field_id` (UUID, Foreign Key to `extracted_fields.id`, Indexed)
- `patient_id` (UUID, Indexed)
- `queued_at` (TIMESTAMPTZ)
- `status` (VARCHAR 32: `pending`, `adjudicated`)
- `adjudicated_by` (VARCHAR 64, Nullable)
- `adjudicated_at` (TIMESTAMPTZ, Nullable)
- `notes` (VARCHAR 512, Nullable)

---

## 3. Strict Safety Invariants

1. **No Local Variant Schemas:** All modules must use the canonical `ExtractedField` structure.
2. **Mandatory Provenance Slots:** No observation may exist without numeric `confidence` and `risk_level`.
3. **Strict Adjudication Gate:** Unreviewed (`needs_review`) and `rejected` fields cannot be committed into patient clinical sub-models.
4. **Idempotency:** Re-committing an already ingested job (`job_id`) returns zero duplicate clinical records.
