# Pipeline UI Flow — AI-Assisted Human Review

> **ALPHA**: This flow is an alpha implementation. The AI extraction pipeline,
> review queue, and commit lifecycle are under active development. Backend
> endpoints are wired but use placeholder data for fields that have not yet
> been processed by a live extraction engine.

---

## Overview

The pipeline UI enables providers to upload clinical documents, monitor AI
extraction jobs, review flagged fields, and commit approved data into patient
records. Every step is consent-gated and audited.

```
Upload → Job Status → Review Queue → Review Cockpit → Commit to Record
  (1)       (2)           (3)            (4)              (5)
```

---

## Screen Inventory

| # | Screen | Route | Purpose |
|---|--------|-------|---------|
| 1 | `PipelineUploadScreen` | `/doctor/pipeline/upload` | Upload a clinical document for AI extraction |
| 2 | `JobStatusScreen` | `/doctor/pipeline/jobs/[jobId]` | Monitor extraction job progress and field summary |
| 3 | `ReviewQueueScreen` | `/doctor/pipeline/review-queue` | Browse all flagged fields needing human adjudication |
| 4 | `ReviewCockpitScreen` | `/doctor/pipeline/review/[jobId]` | Side-by-side document preview + field cards for review |
| 5 | `CommitScreen` | `/doctor/pipeline/commit/[jobId]` | Final review summary and commit to patient record |

---

## (1) PipelineUploadScreen

**Route**: `/doctor/pipeline/upload?patient_id=...&consent_token=...`

### Behaviour
- Provider selects a clinical document (PDF, PNG, JPG, TIFF, DOC, DOCX).
- File is sent as `FormData` via `NexaApiClient.uploadDocument()`.
- On success (HTTP 202), redirect to `JobStatusScreen` with the returned
  `job_id`.
- If upload fails, show error with retry button (no auto-retry for upload
  failures — user must explicitly re-upload).
- Allowed file extensions validated on both frontend and backend.

### API Contract
```
POST /api/v2/pipeline/documents/upload
Headers: X-Consent-Token, X-Consent-Purpose: ai_document_ingestion
Body: FormData { file, patient_id, filename }
Response 202: { job_id, patient_id, filename, status: "queued",
                estimated_completion_seconds }
```

### State Machine
```
idle → uploading → (success → redirect to job status)
                  → (error → show error, stay on upload)
```

### Constraints
- File size: max 25 MB (frontend check; backend may enforce differently).
- Allowed types: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.doc`, `.docx`.
- Requires active consent token with `ai_document_ingestion` scope.
- Session guard: must be authenticated.

---

## (2) JobStatusScreen

**Route**: `/doctor/pipeline/jobs/[jobId]?consent_token=...`

### Behaviour
- Polls `GET /api/v2/pipeline/jobs/{job_id}` every 3 seconds with adaptive
  backoff (same pattern as consent status polling).
- Displays extraction job status: `queued` → `processing` → `scored` →
  `review_required` | `auto_approved` | `failed`.
- Shows summary: total fields, auto-approved count, needs-review count,
  overall confidence.
- When status reaches `review_required` or `auto_approved`, shows "Continue"
  button leading to Review Cockpit or Commit respectively.
- When `failed`, shows error details and "Re-upload" button.

### API Contract
```
GET /api/v2/pipeline/jobs/{job_id}
Headers: X-Consent-Token, X-Consent-Purpose: pipeline_status
Response 200: { job_id, patient_id, status, document_type,
               overall_confidence, auto_approved_count,
               needs_review_count, extracted_fields[], created_at }
```

### Polling Strategy
| Elapsed | Interval | Max Attempts |
|---------|----------|-------------|
| 0–30s   | 3s       | unlimited   |
| 30–90s  | 6s       | unlimited   |
| 90–300s | 15s      | unlimited   |
| >300s   | 30s      | stop at 60  |

Terminal states that stop polling: `auto_approved`, `review_required`,
`failed`, `committed`.

### Constraints
- Consent token required for `pipeline_status` purpose.
- Session guard: must be authenticated.

---

## (3) ReviewQueueScreen

**Route**: `/doctor/pipeline/review-queue?consent_token=...`

### Behaviour
- Fetches `GET /api/v2/pipeline/review-queue` to list all flagged items
  requiring human adjudication.
- Each item shows: document title, flagged field count, highest risk level,
  queued timestamp.
- Clicking an item navigates to `ReviewCockpitScreen` with the associated
  `job_id`.
- Empty state: "No items pending review."
- Loading state: spinner.
- Error state: error message with retry.

### API Contract
```
GET /api/v2/pipeline/review-queue
Query: hospital_id, patient_id (optional filters)
Headers: X-Consent-Token, X-Consent-Purpose: clinical_review
Response 200: { items: [{ review_item_id, job_id, patient_id,
                          document_title, flagged_fields_count,
                          highest_risk_level, queued_at }] }
```

### Risk Level Display
| Risk Level | Badge Color | Icon |
|-----------|-------------|------|
| `LOW_RISK` | Green | ✓ |
| `MEDIUM_RISK` | Yellow/Orange | ⚠ |
| `HIGH_RISK` | Red | ⛔ |
| `CRITICAL_RISK` | Red + Bold | 🚨 |

### Constraints
- Consent token required for `clinical_review` purpose.
- Session guard: must be authenticated.

---

## (4) ReviewCockpitScreen

**Route**: `/doctor/pipeline/review/[jobId]?consent_token=...`

### Layout
```
┌─────────────────────────────────────────────────────────┐
│  Job: {job_id}  ·  Patient: {patient_id}               │
│  Status: {status}  ·  Confidence: {overall_confidence}  │
├─────────────────────────────────────────────────────────┤
│  Review Progress    3/7 reviewed  [========----] 43%    │
├──────────────────────────┬──────────────────────────────┤
│                          │                              │
│   Original Document      │   Field Cards (scrollable)   │
│   Preview                │                              │
│                          │   ┌─ FieldCard ─────────┐   │
│   [Image / PDF viewer]   │   │ field_name          │   │
│                          │   │ extracted value      │   │
│   ┌─ Page Nav ───────┐   │   │ confidence badge     │   │
│   │ ◀ Page 1/3 ▶     │   │   │ risk badge           │   │
│   └──────────────────┘   │   │ validation messages  │   │
│                          │   │ source page indicator│   │
│                          │   │ [Approve] [Edit] [Reject]│
│                          │   └─────────────────────┘   │
│                          │                              │
│                          │   ... more FieldCards ...    │
│                          │                              │
├──────────────────────────┴──────────────────────────────┤
│  [← Back to Queue]     Progress: 3/7 reviewed   [Commit →] │
└─────────────────────────────────────────────────────────┘
```

### FieldCard Component
Each `FieldCard` displays:
- **Field name** — e.g. `hba1c`, `blood_pressure_systolic`
- **Extracted value** — raw_value, with normalized_value shown alongside if different
- **Confidence badge** — ProvenanceBadge pattern:
  - ≥ 0.9 and `auto_approved`: Yellow ("AI extracted · X% confidence · Not yet verified")
  - ≥ 0.9 and `approved`: Green ("Clinician verified")
  - < 0.9: Orange/Red ("AI extracted · X% confidence · Not yet verified")
  - Manual entry: Gray
- **Risk badge** — colour-coded per risk level table above
- **Validation messages** — from `validation_result.validation_errors[]`
  - Also shows `reference_range` if available
- **Source page** — "Source: Page {source_page}" with highlight link to jump
  the document viewer to that page
- **Action buttons**: Approve / Edit / Reject
  - **Approve**: calls `POST /api/v2/pipeline/fields/{field_id}/review`
    with `{ action: "approve" }`. Shows "Approving…" during the request,
    then briefly displays a green "✓ Approved" confirmation.
  - **Edit**: opens inline edit mode showing the original AI extraction
    with strikethrough for comparison. User enters the corrected value.
    Calls `{ action: "edit", corrected_value: "..." }`. Shows "Saving…"
    during the request, then briefly displays a yellow "✎ Edited" confirmation.
  - **Reject**: opens reject mode showing the value being excluded with
    strikethrough. Optional rejection reason. Calls
    `{ action: "reject", review_notes: "..." }`. Shows "Rejecting…"
    during the request, then briefly displays a red "✕ Rejected" confirmation.
  - After action, card status updates to `approved`/`edited`/`rejected`
    and the card transitions to read-only mode.

### Document Preview (Left Panel)
- Renders the uploaded document as images (one per page).
- Page navigation: ◀ Page N/M ▶
- When a field card's source page is clicked, the preview scrolls/jumps to
  that page.
- ALPHA: PDF rendering uses a simple image-based viewer. Production should
  use a proper PDF.js renderer.

### API Contracts

**Fetch job details + fields:**
```
GET /api/v2/pipeline/jobs/{job_id}
Headers: X-Consent-Token, X-Consent-Purpose: pipeline_status
```

**Adjudicate a field:**
```
POST /api/v2/pipeline/fields/{field_id}/review
Headers: X-Consent-Token, X-Consent-Purpose: field_adjudication
Body: { action: "approve"|"reject"|"edit", corrected_value?, review_notes? }
Response 200: { field_id, job_id, previous_status, new_status,
               final_value, adjudicated_by, adjudicated_at }
```

### Constraints
- All adjudication calls require `field_adjudication` consent purpose.
- A job with unresolved `needs_review` fields cannot be committed (HTTP 409).
- Session guard: must be authenticated.
- Field cards that are already adjudicated show their final status but remain
  in the list (grayed out, read-only).

---

## (5) CommitScreen

**Route**: `/doctor/pipeline/commit/[jobId]?consent_token=...&patient_id=...`

### Behaviour
- Shows a final summary of the job: total fields, approved, edited, rejected.
- Lists all fields that will be committed (status: `auto_approved`,
  `approved`, or `edited`).
- Rejected fields are shown crossed-out and will NOT be committed.
- "Commit to Record" button calls `POST /api/v2/pipeline/jobs/{job_id}/commit`.
- On success (HTTP 201), shows confirmation with:
  - `committed_fields_count`
  - `timeline_event_id`
  - `committed_at` timestamp
- On failure (HTTP 409 — unresolved fields), shows error with link back to
  Review Cockpit.
- Optional `encounter_summary` free-text field (sent in commit payload).

### API Contract
```
POST /api/v2/pipeline/jobs/{job_id}/commit
Headers: X-Consent-Token, X-Consent-Purpose: pipeline_commit
Body: { patient_id, encounter_summary? }
Response 201: { job_id, patient_id, status: "committed",
               fields_committed, committed_fields_count,
               timeline_event_id, ledger_tx_hash, committed_at }
Error 409: { detail: "Review incomplete: job contains unresolved fields needing review." }
```

### Constraints
- Backend rejects commit if any field is still `needs_review` (HTTP 409).
- Every committed field MUST have `confidence` and `risk_level` metadata
  (backend enforces; HTTP 400 if missing).
- Consent token required for `pipeline_commit` purpose.
- Session guard: must be authenticated.
- Once committed, the job status transitions to `committed` permanently.

---

## Cross-Cutting Concerns

### Consent Gating
Every pipeline API endpoint requires a valid consent token with the
appropriate purpose. The consent token is passed as the `X-Consent-Token`
header and is validated server-side on every request. Frontend consent
enforcement is UX-only — the backend is the security boundary.

### Audit Trail
All pipeline actions are audited:
- `DOCUMENT_UPLOADED` — when a document is staged
- `FIELD_APPROVED` / `FIELD_REJECTED` / `FIELD_EDITED` — field adjudication
- `JOB_COMMITTED` — commit to patient record

### Error Handling
| HTTP Status | Behaviour |
|-------------|-----------|
| 401 | Session expired → redirect to login |
| 403 | Consent required or denied → show access error |
| 404 | Resource not found → show expired/unavailable message |
| 409 | Conflict (unresolved fields, terminal state) → show conflict error |
| 429 | Rate limited → retry with exponential backoff |
| 5xx | Server error → retry with bounded backoff, show reconnecting |

### Session Guards
All 5 pipeline screens check `isAuthenticated` from `useProviderAuth()`.
When unauthenticated, they render a "🔒 Session Required" state with a
"Go to Login" button. No data is fetched while unauthenticated.

### Consent Guards
All 5 pipeline screens also guard for missing consent tokens. When no
`consent_token` is available (empty or not provided in URL params), they
render a "🔒 Consent Required" state with a "Request Consent" button.
No API calls are made without a valid consent token.

### Review Progress Bar
The ReviewCockpitScreen shows a progress bar tracking review completion.
It updates in real-time as fields are adjudicated. The bar turns green
when all fields are reviewed, and a "✓ All Reviewed" badge appears.

### FieldCard Action Feedback
After a successful adjudication action (approve, edit, reject), the
FieldCard briefly shows an animated confirmation overlay:
- **Approved**: ✓ green "Approved" card
- **Edited**: ✎ yellow "Edited" card
- **Rejected**: ✕ red "Rejected" card

This confirmation auto-fades after 1.5 seconds, then the card
transitions to its read-only state with the final status displayed.

### ALPHA Status Warnings
Every screen displays an ALPHA badge and the precise wording:
> "ALPHA · AI-assisted extraction results require clinical verification before commitment."

### FieldCard Alignment with ExtractedField Schema (WS1)
The `FieldCard` component is typed against the canonical `ExtractedField`
interface:

```typescript
interface ExtractedField {
  field_id: string
  job_id: string
  field_name: string
  raw_value: string
  normalized_value: string | null
  confidence: number
  risk_level: 'LOW_RISK' | 'MEDIUM_RISK' | 'HIGH_RISK' | 'CRITICAL_RISK'
  validation_result: ValidationResult
  source_page: number
  source_bbox: [number, number, number, number] | null
  status: 'auto_approved' | 'needs_review' | 'approved' | 'rejected' | 'edited'
  corrected_value: string | null
}
```

No field in the `FieldCard` is hardcoded or uses placeholder values. All
data comes from the API response via the shared `apiClient`.

---

## Route Summary

### Next.js (Doctor Dashboard — `/app/doctor/pipeline/...`)
| Route | File | Screen |
|-------|------|--------|
| `/doctor/pipeline/upload` | `app/doctor/pipeline/upload/page.tsx` | `PipelineUploadScreen` |
| `/doctor/pipeline/jobs/[jobId]` | `app/doctor/pipeline/jobs/[jobId]/page.tsx` | `JobStatusScreen` |
| `/doctor/pipeline/review-queue` | `app/doctor/pipeline/review-queue/page.tsx` | `ReviewQueueScreen` |
| `/doctor/pipeline/review/[jobId]` | `app/doctor/pipeline/review/[jobId]/page.tsx` | `ReviewCockpitScreen` |
| `/doctor/pipeline/commit/[jobId]` | `app/doctor/pipeline/commit/[jobId]/page.tsx` | `CommitScreen` |

### Expo (Patient — `/app/pipeline/...`)
Pipeline screens are **doctor-only** (Next.js dashboard). Expo patient app
does NOT have pipeline screens. The patient's role is limited to granting
consent for `ai_document_ingestion` scope.
