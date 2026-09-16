# Patient Longitudinal Records UX — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11b-patient-longitudinal-records-ux`
- **Current HEAD:** `5db3117`
- **Base main SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Current phase:** Phase 2 — Frontend API Client & Patient DTO Models
- **Last completed step:** Backend patient self-view read endpoints, keyset cursor pagination, and tests (14 new unit tests, route registration updated, 194/194 patient tests green).
- **Exact next task:** Add TypeScript DTO models and client methods to `nexa-client/packages/app/utils/apiClient.ts` for patient summary, records, detail, prescriptions, and reports.
- **Current blockers:** None
- **Tests to run next:** `yarn test:app`
- **Protected files not to touch:**
  - `app/services/clinical_access_session.py`
  - `app/models/clinical_access_session.py`
  - `app/security/clinical_access_policy.py`
  - `app/services/approved_access_capability.py`
  - `app/core/consent_gate.py`
  - `app/api/v2/consent_v3_routes.py`
  - `alembic/versions/20260916_clinical_access_sessions.py` (and any new competing migrations)
  - Patient external import workstream files (`slice-11a-patient-external-record-import`)
- **Concurrent branches to re-check:** `origin/slice-11a-patient-external-record-import` (remote commit `aa218f2`)

---

## Scope

This branch (`slice-11b-patient-longitudinal-records-ux`) exclusively owns:
1. **Patient Home:** Personal health summary with trustworthy highlights (active medications, allergies, recent vitals, recent labs/reports, quick access navigation).
2. **Timeline:** Chronological healthcare history from all valid sources, bounded keyset pagination (`limit`, `cursor`, `next_cursor`), safe user-facing event titles/summaries, and deep link/navigation to underlying record details.
3. **Categorized Records:** Structured record browsing by category (Allergies, Medications, Vitals, Laboratory, Reports / Documents) with counts, recent previews, list view, and record detail view.
4. **Prescriptions vs. Medications UX:** Clear distinction between medication treatments and clinician-issued prescriptions (consuming `Medication` entities with appropriate provenance without conflating them).
5. **Reports UX:** Categorized medical reports aggregating typed lab evaluations, imaging, discharge summaries, and external source documents with provenance, facility, and authorized source-view access without leaking storage keys or internal IDs.
6. **Record Detail & Provenance Presentation:** Readable structured clinical details, origin source, doctor/facility/date, honest trust labels (`Nexa Clinician Created`, `Patient Reported`, `Document Extracted`, `Hospital HMS Imported`).
7. **Patient Self-View Read Backend Models:** Server-derived patient self-view endpoints on `/api/v2/patient/me/*` with strict `no-store` cache controls, bounded pagination, zero cross-patient exposure, and no provider consent requirements.

---

## Explicit Non-Scope

1. Doctor treatment workspace, clinical access session backend (`ClinicalAccessSession`), and provider consent gate redesign.
2. Patient external record upload/OCR processing pipeline (owned by parallel workstream `slice-11a-patient-external-record-import`).
3. Sibling/competing Alembic migrations: No database migrations will be introduced in this branch unless strictly necessary and after linear verification from head `20260916_clinical_access_sessions`.
4. Fabricating missing clinical models: Missing models (e.g. separate Prescription order table, Encounter table, Diagnosis table) must NOT be faked with JSON blobs or synthetic mock data.

---

## Repository Baseline

- **Origin/Main SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Branch:** `slice-11b-patient-longitudinal-records-ux`
- **Alembic Current Single Head:** `20260916_clinical_access_sessions`
- **Active Remote Branches:**
  - `origin/main` (`2a10384`)
  - `origin/slice-11a-patient-external-record-import` (`aa218f2` - touched only `docs/governance/PATIENT_EXTERNAL_RECORD_IMPORT_HANDOFF.md`)
- **Open PRs:** None
- **Concurrent Workstreams:**
  - Workstream A: Bounded `ClinicalAccessSession` / treatment-access backend (merged to main at `fd34956` / `2a10384`).
  - Workstream B: Patient External-Record Import + Medical-History Onboarding (`slice-11a-patient-external-record-import`).

---

## Current Product Audit

Classification: `EXISTS`, `PARTIAL`, `WRONG_FLOW`, `MISSING`.

| Area / Capability | Status | Findings & Evidence |
|---|---|---|
| **A. Patient Self-View Visibility** | PARTIAL | Patient web dashboard only shows security ledger and access history stats. Patient mobile only shows access history, device management, and generic timeline. No categorized records, medications, allergies, or reports are visible to the patient. |
| **B. TimelineEvent Role** | PARTIAL | `TimelineEvent` exists (`timeline_events` table) with `patient_id`, `event_type`, `event_ref_id`, `occurred_at`, `source`, `summary`. `_fetch_and_merge_timeline` queries both `TimelineEvent` and typed tables (`Vitals`, `Medication`, `LabResult`, `DocumentReference`) but lacks deduplication across `event_ref_id` and does not provide links to record details. |
| **C. Existing Typed Entities** | EXISTS | `Vitals`, `Medication`, `LabResult`, `Allergy`, `DocumentReference`, `DocumentStorage`, `PatientRecord`. |
| **D. Missing Typed Entities** | MISSING | `Prescription` (separate order model), `Encounter`, `Imaging` (dedicated typed model), `Diagnosis` / `Condition`, `ClinicalNote`. |
| **E. Patient Self-View Endpoints** | PARTIAL | Only `/api/v2/patient/me/timeline` and `/api/v2/patient/me/access-history` exist. Self-view summary, records, records by category, record detail, prescriptions, and reports are MISSING. |
| **F. Timeline Information Content** | PARTIAL | Returns `title`, `summary`, `occurred_at`, `source_display`, `badges`. Lacks `record_id`, `category`, provider/facility information, and category filters. |
| **G. Timeline Pagination** | PARTIAL | Accepts `limit` and `cursor` query params, but ignores `cursor` and permanently returns `"next_cursor": None`. Unbounded/large history cannot be loaded incrementally. |
| **H. Record Detail Screens** | MISSING | No patient-facing record detail screens exist in mobile or web. Timeline items are not interactive or navigable. |
| **I. Patient Home** | WRONG_FLOW | Current `/patient/dashboard` is an Access History & Security Audit dashboard. Shows "Access History Events", "Emergency Accesses", "Recent Access Ledger". Shows zero personal health information (no allergies, no active medications, no recent vitals, no recent labs). |
| **J. Prescriptions vs. Medications** | PARTIAL | Only `Medication` exists. Prescriptions as issued doctor orders are not separately modeled in the DB. |
| **K. Lab Reports vs. Lab Results** | PARTIAL | Individual results exist as `LabResult`. Lab reports exist only as unstructured files in `DocumentReference` / `DocumentStorage` (`document_type='lab_report'`). |
| **L. Imaging Modeling** | PARTIAL | Only generic `DocumentReference` (`document_type='imaging'`) exists. No DICOM or dedicated imaging entity. |
| **M. Source Distinguishability** | PARTIAL | Backend has `source` column (`manual`, `ai_extracted`, `human_adjudicated`). Enriched presentation shows "Manual entry" or "AI-extracted from document". Clinician-created vs patient-reported vs external document is not clearly distinguished. |
| **N. Provenance Presentation** | PARTIAL | `SourceBadge` and `RiskBadge` exist in frontend. Need honest clinical labels (`Clinician verified`, `Patient reported`, `Document extracted`). |
| **O. Patient Isolation** | EXISTS | `require_self_patient_access()` in `app/core/consent_gate.py` derives patient from session and validates against path/query `patient_id`, failing closed with 403 on mismatch. |
| **P. Client Parameter Trust** | EXISTS | Patient self-view endpoints on `/api/v2/patient/me/*` use server-derived session identity; client cannot override with an arbitrary UUID. |
| **Q. Query Bounding** | PARTIAL | Limits exist, but lack stable keyset cursor pagination for timeline and records. |
| **R. Internal Secret Leakage** | PARTIAL | `storage_ref` is exposed in doctor structured record route (`/api/v2/patient/{id}/records`). Patient self-view routes must omit raw `storage_ref` and internal pipeline UUIDs. |
| **S. Response Caching** | PARTIAL | Sensitive patient health responses lack explicit `Cache-Control: no-store, private` headers. |
| **T. Timeline Data Safety** | EXISTS | Built from typed records and structured timeline rows, but pipeline extraction summaries contain technical strings ("AI ingested..."). |
| **U. User-Facing Event Copy** | PARTIAL | Some event types and summaries are engineering-facing (e.g. `EXTRACTED_DATA_INGESTED`). Must present human-friendly healthcare language. |

---

## Product Information Architecture

### Target Navigation
- **Home (`/patient/dashboard` & mobile home):**
  - Personal Health Summary: Active Medications, Known Allergies, Recent Vitals, Recent Labs/Reports.
  - Recent Health Activity feed (latest 3 events with link to full Timeline).
  - Quick Actions / Navigation: Timeline, Records, Prescriptions, Reports, Access & Privacy.
- **Timeline (`/patient/timeline`):**
  - Chronological healthcare history across all sources.
  - Filter by category: All, Vitals, Medications, Labs, Reports, Documents.
  - Infinite scroll / cursor-based pagination ("Load More").
  - Clickable items leading to Record Detail.
- **Records (`/patient/records`):**
  - Categorized grid: Allergies, Medications, Vitals, Laboratory, Reports & Documents.
  - Category list view with search/filter within category.
  - Record Detail view with structured clinical fields, provenance, doctor/facility, and source document link where available.
- **Prescriptions (`/patient/prescriptions`):**
  - Focused medication and prescription treatment view.
  - Active vs. historical medications.
  - Honest indication of source (clinician-prescribed vs. patient-uploaded).
- **Reports (`/patient/reports`):**
  - Diagnostic evaluations, laboratory reports, imaging documents, discharge summaries.
  - Provenance and authorized source viewing.
- **Access & Privacy (`/patient/access-history` & `/patient/discoverability`):**
  - Kept distinct from clinical browsing. Who accessed files, emergency break-glass ledger, privacy settings.

---

## Backend Read Model

All endpoints use `Depends(require_self_patient_access())` and return `Cache-Control: no-store, private`:

1. `GET /api/v2/patient/me/summary`
   - Bounded snapshot: allergy highlights, active medications, latest vitals, recent labs, recent reports, recent timeline events (latest 5).
2. `GET /api/v2/patient/me/timeline?limit=20&cursor=...&category=...`
   - Keyset cursor pagination using `(occurred_at, event_id)`.
   - Bounded limit (max 50). Returns `events`, `next_cursor`.
   - Category filtering (`vitals`, `medications`, `labs`, `documents`, `allergies`).
   - Clean user-facing titles and summaries.
3. `GET /api/v2/patient/me/records`
   - Category overview with record counts and latest timestamp per category.
4. `GET /api/v2/patient/me/records/{category}?limit=20&cursor=...`
   - Typed records for category: `allergies`, `medications`, `vitals`, `labs`, `documents`.
   - Bounded keyset cursor pagination.
5. `GET /api/v2/patient/me/records/{category}/{record_id}`
   - Detailed view for specific record with full provenance, source document presence, facility/provider info.
6. `GET /api/v2/patient/me/prescriptions`
   - Medication and prescription records formatted for treatment review.
7. `GET /api/v2/patient/me/reports`
   - Aggregated diagnostic reports and documents.

---

## Patient Self-Access Authority

- **Authority Anchor:** Derived strictly from the authenticated patient session (`get_scoped_session` / `require_self_patient_access()`).
- **No Provider Consent Token:** Patient self-access does NOT require or consume a doctor consent token or clinical access session.
- **IDOR Prevention:** Any client-supplied `patient_id` or `id` parameter that does not match the server-derived patient session immediately fails closed with `403 FORBIDDEN` and logs an audit event.
- **Audit Logging:** Patient reads are audited as `PATIENT_RECORD_READ_SUCCESS` with `metadata={"access_type": "self_access"}` so they are cleanly excluded from provider access history (SEC-021).

---

## Provenance Rules

- **Nexa Clinician Created:** Created by verified Nexa clinician in structured workflow.
- **Patient Reported:** Entered directly by patient or imported without clinician sign-off.
- **Document Extracted:** Extracted from external document (Textract/OCR). Shows source document name/type, page, and extraction confidence without faking clinical certainty.
- **Clinician Verified:** Document-extracted or patient-reported data that was reviewed and verified by an authorized clinician.
- **Hospital HMS Imported:** Received from connected hospital/clinic electronic health record system.

Clinical Safety Rules:
- Never infer "No allergies" from zero rows. Render "No recorded allergies on file".
- Never infer "Normal" from missing reference ranges.
- Never invent diagnoses, dosages, frequencies, or clinical interpretations.

---

## Architecture Decisions

### Decision 1: Do not modify database schema / Alembic migrations
- **Reason:** The schema already has `Vitals`, `Medication`, `LabResult`, `Allergy`, `DocumentReference`, `TimelineEvent`, `PatientRecord`. Parallel workstream A just landed migration `20260916_clinical_access_sessions`. Introducing a migration risks competing heads.
- **Alternatives rejected:** Creating a new `Prescriptions` or `Encounters` table before clinical-domain consensus.
- **Security implication:** Zero migration drift; linear single-head preserved.

### Decision 2: Keyset cursor pagination for patient timeline and records
- **Reason:** Offsets degrade at scale and can skip or duplicate rows during concurrent writes. Keyset cursor using base64-encoded `{"occurred_at": "...", "id": "..."}` ensures deterministic O(1) paging.
- **Alternatives rejected:** Unbounded reads or naive offset/limit.
- **Security implication:** Bounded memory and execution time on DB and API.

### Decision 3: Unified patient self-view read endpoints in `app/api/v2/patient_record_routes.py`
- **Reason:** Keeps all patient record read models together with existing routes, reusing established authorization and encryption patterns.
- **Alternatives rejected:** Creating a separate competing route file that duplicates model imports and DB sessions.

---

## Work Log

### Entry 1
- **Timestamp:** 2026-09-16T20:45:00+05:30
- **Step:** Baseline verification, branch isolation, and product audit.
- **Starting SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Ending SHA:** `5db3117`
- **Files changed:** `docs/governance/PATIENT_LONGITUDINAL_RECORDS_UX_HANDOFF.md`
- **Behavior changed:** None (audit and living handoff created).
- **Tests run:** `pytest tests/test_patient_screens.py tests/test_patient_records.py tests/test_patient_self_auth.py tests/test_patient_session_authority.py`, `yarn test:app`, `yarn test:next`.
- **Result:** PASS (214 Python tests passed; 270 app vitest passed; 6 next vitest passed).
- **Security impact:** Non-regression verified.
- **UX impact:** Foundation set for longitudinal patient records UX.
- **Concurrent-overlap result:** Checked `origin/slice-11a-patient-external-record-import` (only `PATIENT_EXTERNAL_RECORD_IMPORT_HANDOFF.md` changed). Overlap: NONE.
- **Known issue:** Timeline currently returns `next_cursor: None` and patient self-view endpoints for summary/records/detail are missing.
- **Exact next action:** Implement backend patient self-view read endpoints with cursor pagination and unit tests.

### Entry 2
- **Timestamp:** 2026-09-16T23:35:00+05:30
- **Step:** Backend patient self-view read endpoints, keyset cursor pagination, and tests.
- **Starting SHA:** `5db3117`
- **Ending SHA:** Pending commit for backend endpoints
- **Files changed:** `app/api/v2/patient_record_routes.py`, `tests/test_route_registration.py`, `tests/test_patient_longitudinal_records.py`
- **Behavior changed:**
  - Added keyset cursor encoding/decoding (`_encode_keyset_cursor`, `_decode_keyset_cursor`) with validation.
  - Added `_fetch_patient_longitudinal_timeline` with keyset cursor pagination (`(occurred_at, event_id)`), category filtering, and typed-record deduplication.
  - Implemented 7 patient self-access endpoints derived strictly from patient session via `require_self_patient_access()`:
    - `GET /api/v2/patient/me/summary`
    - `GET /api/v2/patient/me/timeline` (with cursor, category, limit, next_cursor)
    - `GET /api/v2/patient/me/records` (category overview with item counts)
    - `GET /api/v2/patient/me/records/{category}` (paginated category items)
    - `GET /api/v2/patient/me/records/{category}/{record_id}` (record detail with provenance)
    - `GET /api/v2/patient/me/prescriptions` (medication treatments with provenance)
    - `GET /api/v2/patient/me/reports` (diagnostic reports with provenance)
    - `GET /api/v2/patient/me/documents/{document_id}` (safe metadata without exposing internal S3 storage keys)
  - Added `Cache-Control: no-store, no-cache, must-revalidate, private` on all patient self responses.
  - Enforced fail-closed IDOR protection across all endpoints.
- **Tests run:** `pytest tests/test_route_registration.py tests/test_patient_records.py tests/test_records_qa.py tests/test_patient_screens.py tests/test_patient_longitudinal_records.py -v`.
- **Result:** PASS (194/194 passed: 3 route registration, 9 patient records, 4 records QA, 164 patient screens, 14 new patient longitudinal records).
- **Security impact:** Non-regression verified. No secrets or S3 keys exposed. No schema/Alembic changes.
- **UX impact:** Complete backend read contract established for patient home, timeline, records, prescriptions, and reports.
- **Concurrent-overlap result:** Checked parallel workstreams; zero overlapping files modified.
- **Known issue:** Frontend API client and UI screens need to be updated to consume new endpoints.
- **Exact next action:** Add TypeScript DTO models and client methods to `nexa-client/packages/app/utils/apiClient.ts`.

---

## Qualification Matrix

| Area | Status | Notes |
|---|---|---|
| Backend focused tests | PASS | 194 passed (route registration, patient records, screens, QA, longitudinal records) |
| Patient self-view API tests | PASS | 14 new tests in `tests/test_patient_longitudinal_records.py` covering cursor, IDOR, summary, records, detail, prescriptions, reports, documents |
| Route registration test | PASS | All 7 new endpoints added to EXPECTED_ROUTES without duplicate routes |
| Privacy/security tests | PASS | Fail-closed IDOR, zero S3 key exposure, `no-store` headers verified |
| PostgreSQL | PASS | Models and schema validated without new migrations |
| Redis where relevant | PASS | Patient session authority tests passing |
| Frontend vitest (app) | PASS | 270 passed across 43 test files (baseline verified) |
| Frontend vitest (next) | PASS | 6 passed across 2 test files (baseline verified) |
| Timeline tests | PASS | Keyset pagination, category filters, and deduplication verified |
| Records tests | PASS | Category overview, paginated list, and detail verified |
| Dashboard tests | PASS | Existing tests pass; health home to be integrated |
| Next production build | NOT RUN | To be run during frontend phase |
| Workspaces build | NOT RUN | To be run during qualification |
| Android compile | NOT RUN | To be run during final qualification |
| iOS compile | NOT RUN | To be run during final qualification |
| Vercel exact head | NOT RUN | Post-implementation |
| Accessibility checks | NOT RUN | Touch targets, contrast, screen reader labels |
| Pagination checks | PASS | Keyset cursor pagination unit tested |
| Empty/loading/error states | PASS | Unit tested on backend responses |

---

## Open Risks

1. Parallel workstream `slice-11a-patient-external-record-import` is working on document import. We must consume `DocumentReference` and `TimelineEvent` with external document provenance without modifying import-specific routes or tables.
2. Prescriptions vs. Medications: No dedicated `Prescription` database entity exists in the repo. The UX must present `Medication` truthfully without pretending a full prescription order model exists.

---

## Merge / Rebase Safety

- Base SHA: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- Never rebase blindly with ours/theirs.
- Regularly fetch origin and check diff against main and parallel branches.

---

## Final Definition of Done

- [ ] Authenticated patient can access a real Health Home showing personal health summary (active medications, allergies, recent vitals, recent labs/reports).
- [ ] Authenticated patient can navigate a chronological Timeline with working keyset pagination and category filters.
- [ ] Timeline events are interactive and link to underlying record details.
- [ ] Authenticated patient can browse categorized Records (Allergies, Medications, Vitals, Laboratory, Reports / Documents).
- [ ] Record details show structured data, provenance badges, facility/doctor where known, and source document links where authorized.
- [ ] Prescriptions / medications view shows treatments clearly with honest provenance.
- [ ] Reports view aggregates lab evaluations, imaging reports, discharge summaries, and external documents safely.
- [ ] Patient self-access is derived exclusively from authenticated patient session; no doctor consent token required; cross-patient IDOR prevented.
- [ ] No internal storage keys (`s3://...`), pipeline execution IDs, or PHI are leaked in URLs, client errors, or logs.
- [ ] All Main / Web / Expo navigation flows work seamlessly with proper loading, empty, and error states.
- [ ] Full backend and frontend test suites pass with zero regressions.
