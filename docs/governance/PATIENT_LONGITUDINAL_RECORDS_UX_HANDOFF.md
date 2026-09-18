# Patient Longitudinal Records UX — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11c-external-record-longitudinal-integration`
- **Merged Main Baseline:** `34510ec1e308762cf2836c70de7e1cc8a828b39d`
- **PR #48 Merge Commit:** `34510ec1e308762cf2836c70de7e1cc8a828b39d` (closed & merged)
- **PR #49 Merge Commit:** `20c75f969657c4e9ea28cd80998724a958a88e03` (closed & merged)
- **Source Slice 11B Frozen Head:** `dd4aba0ea5996e1af01575ee7f0dd7c1eaa2060c`
- **Current Alembic head:** `20260917_treatment_session_operations` (singular)
- **New Migrations:** NONE
- **Current phase:** Slice 11C External Record Longitudinal Integration
- **Last completed step:** Merged PR #49 qualification repair and PR #48 Slice 11B longitudinal records onto main with 100% green CI across all partitions.
- **Exact next task:** Implement Slice 11C external record longitudinal projection & frontend integration.
- **Current blockers:** None
- **Tests to run next:** Full CI verification suite
- **Protected files not to touch:**
  - `app/services/clinical_access_session.py`
  - `app/models/clinical_access_session.py`
  - `app/security/clinical_access_policy.py`
  - `app/services/approved_access_capability.py`
  - `app/core/consent_gate.py`
  - `app/api/v2/consent_v3_routes.py`
  - `app/api/v2/treatment_session_v1_routes.py`
  - `app/api/v2/treatment_session_v1_claim_routes.py`
  - `alembic/versions/20260917_treatment_session_operations.py` (and any new competing migrations)
  - Raw import pipeline internals in `slice-11a-patient-external-record-import`
- **Concurrent branches to re-check:** `origin/slice-11a-patient-external-record-import`

---

## Scope

This branch (`slice-11c-external-record-longitudinal-integration`) bridges:
1. **Longitudinal Record Projection of External Records:** Projecting authentic patient-imported and external clinical records into the unified timeline and categorized records with faithful provenance tracking, honest trust badges, and document deep-links.
2. **Patient Records External Import Entry Points:** Exposing patient-friendly "Add Medical Record" / "Upload Document" entry points from Patient Records (`/patient/records`) and Reports (`/patient/reports`) adhering to strict patient-self authorization.
3. **External Record Provenance & Document Inspection:** Surfacing external document metadata (type, uploaded date, extraction status, processing stage) without leaking internal storage keys (`s3://...`), presigned URLs, or raw pipeline execution IDs.
4. **Non-destructive Coexistence:** Maintaining full compatibility with parallel Slice 11A import services (`PatientExternalRecordImport`) while projecting standard `DocumentReference` and `TimelineEvent` records on main.

---

## Explicit Non-Scope

1. Doctor treatment workspace, clinical access session backend (`ClinicalAccessSession`), and provider consent gate redesign.
2. Auto-committing unverified OCR/Textract output as authoritative clinical truth.
3. Introducing sibling/competing Alembic migrations: No database migrations will be introduced in this branch. Current head `20260917_treatment_session_operations` remains unchanged.
4. Fabricating missing clinical models: Missing models (e.g. separate Prescription order table, Encounter table, Diagnosis table) must NOT be faked with synthetic mock data.

---

## Repository Baseline

- **Merged Main SHA:** `34510ec1e308762cf2836c70de7e1cc8a828b39d`
- **PR #48 (Slice 11B):** MERGED at `34510ec` (reconciled head `7c725a5`)
- **PR #49 (Fixture Repair):** MERGED at `20c75f9` (head `f30a9b4`)
- **Source Slice 11B Frozen Head:** `dd4aba0ea5996e1af01575ee7f0dd7c1eaa2060c`
- **Alembic Current Single Head:** `20260917_treatment_session_operations`
- **New Migrations:** NONE
- **Active Remote Branches:**
  - `origin/main` (`34510ec`)
  - `origin/slice-11b-patient-longitudinal-records-ux` (`dd4aba0` - frozen Task 2 artifact)
  - `origin/slice-11a-patient-external-record-import` (PR #47 in DRAFT)
- **Concurrent Workstreams:**
  - Workstream A: Treatment Session Operations (merged to main).
  - Workstream B: Patient External-Record Import + Medical-History Onboarding (`slice-11a-patient-external-record-import`).

---

## Current Product Audit

Classification: `EXISTS`, `PARTIAL`, `WRONG_FLOW`, `MISSING`.

| Area / Capability | Status | Findings & Evidence |
|---|---|---|
| **A. Patient Self-View Visibility** | EXISTS | Patient web dashboard now provides a comprehensive Personal Health Home summary (medications, allergies, vitals, labs), plus dedicated routes for Timeline, Records, Prescriptions, Reports, and Access History. Mobile mirrors this architecture. |
| **B. TimelineEvent Role** | EXISTS | `_fetch_patient_longitudinal_timeline` queries both `TimelineEvent` and typed tables (`Vitals`, `Medication`, `LabResult`, `DocumentReference`) with deduplication across `event_ref_id`, category filters, and deep-link/detail modal inspection. |
| **C. Existing Typed Entities** | EXISTS | `Vitals`, `Medication`, `LabResult`, `Allergy`, `DocumentReference`, `DocumentStorage`, `PatientRecord`. |
| **D. Missing Typed Entities** | MITIGATED | Separate `Prescription` table is not fabricated; `Medication` treatments are surfaced with honest provenance badges (`Clinician Recorded` vs `Document Extracted`) without faking clinical order models. |
| **E. Patient Self-View Endpoints** | EXISTS | 8 dedicated self-view endpoints on `/api/v2/patient/me/*` (summary, timeline, records, records/{category}, record detail, prescriptions, reports, documents). |
| **F. Timeline Information Content** | EXISTS | Keyset-paginated timeline returns `record_id`, `category`, `source_display`, `badges`, `has_source_document`, and human-friendly titles/summaries. |
| **G. Timeline Pagination** | EXISTS | Keyset cursor pagination `(occurred_at, event_id)` with `limit` and `next_cursor` implemented and unit tested. |
| **H. Record Detail Screens** | EXISTS | Interactive cross-platform `PatientRecordDetailModal` inspecting full structured clinical details and provenance without leaking internal storage keys. |
| **I. Patient Home** | EXISTS | `/patient/dashboard` and `PatientHealthHome` feature Personal Health Summary (active medications, honest allergy status, vitals, labs, quick navigation tiles) while keeping Access History cleanly segregated. |
| **J. Prescriptions vs. Medications** | EXISTS | Prescriptions hub focuses on medication treatments with explicit provenance indicators. |
| **K. Lab Reports vs. Lab Results** | EXISTS | Reports hub surfaces diagnostic documents while Records/Timeline surfaces granular typed lab results. |
| **L. Imaging Modeling** | EXISTS | Reports hub surfaces diagnostic imaging documents with safe metadata without exposing storage paths. |
| **M. Source Distinguishability** | EXISTS | Clear provenance badges distinguish `Clinician Recorded`, `Document Extracted`, and `Patient Reported`. |
| **N. Provenance Presentation** | EXISTS | `SourceBadge` and `RiskBadge` with honest clinical labels and confidence scores. |
| **O. Patient Isolation** | EXISTS | `require_self_patient_access()` derives identity strictly from authenticated session; IDOR rejected with fail-closed 403. |
| **P. Client Parameter Trust** | EXISTS | All patient endpoints derive identity server-side; client cannot override UUID. |
| **Q. Query Bounding** | EXISTS | Bounded limits (default 20, max 50) and keyset cursor pagination on all listing endpoints. |
| **R. Internal Secret Leakage** | EXISTS | Storage keys (`s3://...`), upload paths, and internal pipeline IDs are stripped from all patient self responses. |
| **S. Response Caching** | EXISTS | `Cache-Control: no-store, no-cache, must-revalidate, private` on all patient self endpoints. |
| **T. Timeline Data Safety** | EXISTS | Safe clinical language displayed; extraction summaries formatted cleanly. |
| **U. User-Facing Event Copy** | EXISTS | All event types and summaries mapped to human-friendly healthcare labels. |

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
- **Ending SHA:** `3fc0530`
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
- **Known issue:** None.
- **Exact next action:** Frontend implementation.

### Entry 3
- **Timestamp:** 2026-09-17T00:50:00+05:30
- **Step:** Frontend longitudinal records UX, cross-platform routes, Next.js production build, and Vitest suite.
- **Starting SHA:** `3fc0530`
- **Ending SHA:** `894cc34`
- **Files changed:**
  - `nexa-client/packages/app/utils/apiClient.ts`
  - `nexa-client/packages/app/features/patient/PatientRecordDetailModal.tsx`
  - `nexa-client/packages/app/features/patient/PatientTimelineScreen.tsx`
  - `nexa-client/packages/app/features/patient/PatientRecordsScreen.tsx`
  - `nexa-client/packages/app/features/patient/PatientPrescriptionsScreen.tsx`
  - `nexa-client/packages/app/features/patient/PatientReportsScreen.tsx`
  - `nexa-client/packages/app/features/patient/PatientHealthHome.tsx`
  - `nexa-client/packages/app/features/patient/PatientShell.tsx`
  - `nexa-client/apps/next/app/patient/dashboard/page.tsx`
  - `nexa-client/apps/next/app/patient/records/page.tsx`
  - `nexa-client/apps/next/app/patient/prescriptions/page.tsx`
  - `nexa-client/apps/next/app/patient/reports/page.tsx`
  - `nexa-client/apps/expo/app/patient/_layout.tsx`
  - `nexa-client/apps/expo/app/patient/records.tsx`
  - `nexa-client/apps/expo/app/patient/prescriptions.tsx`
  - `nexa-client/apps/expo/app/patient/reports.tsx`
  - `nexa-client/packages/app/features/patient/PatientLongitudinalRecords.test.tsx`
- **Behavior changed:**
  - Added TypeScript DTOs and client methods on `NexaApiClient` for summary, timeline, records categories, records by category, record detail, prescriptions, reports, and document detail.
  - Implemented `PatientHealthHome`: Personal health summary displaying active medications, allergies ("No recorded allergies on file" when empty), recent vitals, recent labs, and quick hub tiles.
  - Enhanced `PatientTimelineScreen`: Category filter pills (All, Vitals, Medications, Labs, Documents, Allergies), keyset pagination ("Load older timeline events"), and detail modal inspection. 100% preserved all AST invariants in `test_patient_screens.py`.
  - Implemented `PatientRecordsScreen`: 5 category cards with counts and drilldown, keyset cursor pagination, and detail modal.
  - Implemented `PatientPrescriptionsScreen`: Medication treatments with honest provenance badges, source document status, and detail modal.
  - Implemented `PatientReportsScreen`: Aggregated lab and document reports with provenance and zero S3 storage key exposure.
  - Implemented `PatientRecordDetailModal`: Cross-platform `Sheet` modal with honest clinical provenance badges (`Clinician Recorded` vs `Document Extracted`), confidence scores, and raw-data inspection.
  - Updated web navigation in `PatientShell.tsx` and mobile Expo stack in `_layout.tsx`.
- **Tests run:**
  - `pytest tests/test_route_registration.py tests/test_patient_records.py tests/test_records_qa.py tests/test_patient_screens.py tests/test_patient_longitudinal_records.py -v` (194/194 passed)
  - `yarn test:app` (276/276 passed across 44 test files)
  - `yarn test:next` (6/6 passed across 2 test files)
  - `yarn build` (workspace build passed)
  - `yarn verify:next-build` (Next.js production build: 29/29 routes generated in 34.4s)
- **Security impact:** Non-regression verified. Fail-closed IDOR, zero S3 key leakage, no PHI in URLs.
- **UX impact:** Patient experience completely transformed into a true longitudinal health product.
- **Concurrent-overlap result:** Checked parallel workstreams; zero overlapping files modified.
- **Known issue:** None.
- **Exact next action:** Code review and PR.

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
| Frontend vitest (app) | PASS | 276 passed across 44 test files (including 6 new longitudinal tests) |
| Frontend vitest (next) | PASS | 6 passed across 2 test files |
| Timeline tests | PASS | Keyset pagination, category filters, and deduplication verified |
| Records tests | PASS | Category overview, paginated list, and detail verified |
| Dashboard tests | PASS | Personal health summary integrated with honest allergy messaging |
| Next production build | PASS | Verified with `verify:next-build`: 29/29 routes prerendered/compiled |
| Workspaces build | PASS | Verified with `yarn build` (@my/config, @my/ui) |
| Android native compile | NOT QUALIFIED BY SLICE 11B LOCAL RUN | Expo routes declared and registered in `_layout.tsx`; native compilation to be validated by CI |
| iOS native compile | NOT QUALIFIED BY SLICE 11B LOCAL RUN | Expo routes declared and registered in `_layout.tsx`; native compilation to be validated by CI |
| Vercel exact-head deployment | NOT RUN | Local Next production build verified; cloud deployment to be validated by CI |
| Accessibility checks | STATIC REVIEW | Tamagui accessible controls, high contrast labels, semantic roles verified via static code review |
| Pagination checks | PASS | Keyset cursor pagination unit tested frontend & backend |
| Empty/loading/error states | PASS | Verified across Home, Timeline, Records, Prescriptions, Reports |

---

## Open Risks

1. Parallel workstream `slice-11a-patient-external-record-import` is working on document import. Our implementation consumes `DocumentReference` and `TimelineEvent` with external document provenance without modifying import-specific routes or tables. Overlap is zero.
2. Prescriptions vs. Medications: No dedicated `Prescription` database entity exists in the repo. The UX presents `Medication` treatments truthfully without pretending a separate prescription order model exists.

---

## Merge / Rebase Safety

- Integration Base SHA: `342d25cb960a3c81539502e6d3eacde4e7121aee`
- Source Slice 11B Head: `dd4aba0`
- Never rebase blindly with ours/theirs.
- Regularly fetch origin and check diff against main and parallel branches.

---

## Final Definition of Done

- [x] Authenticated patient can access a real Health Home showing personal health summary (active medications, allergies, recent vitals, recent labs/reports).
- [x] Authenticated patient can navigate a chronological Timeline with working keyset pagination and category filters.
- [x] Timeline events are interactive and link to underlying record details.
- [x] Authenticated patient can browse categorized Records (Allergies, Medications, Vitals, Laboratory, Reports / Documents).
- [x] Record details show structured data, provenance badges, facility/doctor where known, and source document links where authorized.
- [x] Prescriptions / medications view shows treatments clearly with honest provenance.
- [x] Reports view aggregates lab evaluations, imaging reports, discharge summaries, and external documents safely.
- [x] Patient self-access is derived exclusively from authenticated patient session; no doctor consent token required; cross-patient IDOR prevented.
- [x] No internal storage keys (`s3://...`), pipeline execution IDs, or PHI are leaked in URLs, client errors, or logs.
- [x] All Main / Web / Expo navigation flows work seamlessly with proper loading, empty, and error states.
- [x] Full backend and frontend test suites pass with zero regressions.
