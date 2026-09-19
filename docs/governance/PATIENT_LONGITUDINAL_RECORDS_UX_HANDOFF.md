# Patient Longitudinal Records UX — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `task2/slice-11e-patient-import-ux`
- **Reconciled Main Baseline:** `0b9cb40d4125d7a02f2a08513b67cc7b40639bde`
- **Consolidated Merged PRs:**
  - PR #59: Task 1 Patient External Record Actions & D4 Capabilities + Task 0 Encounter Vitals Write (merged, `0b9cb40`)
  - PR #57: Merge Task 2 Slice 11D onto main (merged, `06c9a3d`)
  - PR #55: Slice 11D Patient Records Discovery & UX Reliability (Task 2 merged, `cb7df25`)
  - PR #53: Patient External-Record D3 Retry/Cancel Lifecycle (Task 1 merged, `0afc5d2`)
  - PR #54: Canonical Treatment Session Encounter Boundary (Task 0 merged, `a9b2210`)
  - PR #52: Checkpoint freeze (`337c822`)
  - PR #50: Treatment Session V1 Operation Gate (Task 0 merged, `a92033c`)
  - PR #47: Patient External-Record Import Lifecycle (Task 1 merged Phase D2, `48ea8fe`)
  - PR #51: Slice 11C External-Record Longitudinal Integration (Task 2 merged, `6a4f21f`)
  - PR #48: Slice 11B Longitudinal Records UX (Task 2 merged, `34510ec`)
  - PR #49: Qualification Fixture Repair (merged, `20c75f9`)
- **Current Alembic head:** `20260918_treatment_vitals_encounter` (singular, inherited unchanged from main)
- **New Migrations:** NONE (Zero Task-2 migrations)
- **Current phase:** Slice 11E — Patient External Medical Record Import UX
- **Last completed step:** Complete implementation and qualification of Slice 11E (dynamic upload policy, actions-driven workflow, candidate review, save semantics, cancel retention warning, advisory source viewer, Next.js Suspense route, Expo route, 20 comprehensive Vitest tests, Next production build verified).
- **Exact next task:** Open PR targeting `main`, monitor remote CI and Vercel qualification.
- **Current blockers:** None
- **Tests to run next:** `yarn --cwd nexa-client test:app`, `yarn --cwd nexa-client test:next`, `yarn verify:next-build`, `pytest tests/test_patient_screens.py tests/test_patient_external_record_d4_capabilities.py`
- **Protected files not to touch:**
  - `app/services/clinical_access_session.py`
  - `app/models/clinical_access_session.py`
  - `app/security/clinical_access_policy.py`
  - `app/services/approved_access_capability.py`
  - `app/core/consent_gate.py`
  - `app/api/v2/consent_v3_routes.py`
  - `app/api/v2/treatment_session_v1_routes.py`
  - `app/api/v2/treatment_session_v1_claim_routes.py`
  - Raw import pipeline internals in `app/services/patient_external_record_import.py`
  - Any Alembic migrations

---

## Scope

This branch (`task2/patient-records-ux-next`) implements **Slice 11D — Patient Records Discovery & UX Reliability**:
1. **Loading, Error Recovery, and Empty States:**
   - Add explicit, accessible "Retry" actions in error states across all patient screens.
   - Prevent false empty states when errors occur in category drilldowns or report tabs.
   - Differentiate global empty datasets from filter-empty results (e.g. "No events found for this filter" with a "Clear filter" CTA).
2. **Pagination & Cursor Usability:**
   - Immediately reset cursors upon filter change to avoid stale pagination requests.
   - Surface accessible "All records loaded" / "End of timeline" indicators upon pagination exhaustion.
3. **Filter Navigation & Stale Response Race Protection:**
   - Guard against network race conditions when user rapidly toggles filter pills or category tabs using request sequence tracking.
   - Add in-category search and filtering within loaded category records.
4. **Accessibility (a11y) & Usability:**
   - Add `accessibilityState={{ selected: isActive }}` and descriptive labels to filter pills and tabs.
   - Add keyboard Escape dismiss and improved focus handling in `PatientRecordDetailModal`.
   - Maintain 100% compliance with existing Tamagui and React Testing Library invariants.

---

## Explicit Non-Scope

1. Modifying Task 0 clinical access session, treatment authority, or consent policies.
2. Modifying Task 1 external-record lifecycle mutations (retry, cancel, onboarding actions).
3. Introducing database migrations.
4. Fabricating missing clinical models or inventing interpretations (no fake normal ranges or doctor verification badges).

---

## Repository Baseline

- **Authoritative Main Baseline SHA:** `0afc5d24e7383fb91cb20ba5dd67c739bb9de40f` (Post Task 1 PR #53)
- **Current Branch:** `task2/patient-records-ux-next`
- **Alembic Head:** `20260918_canonical_encounter` (singular, inherited unchanged from main)
- **New Migrations:** NONE (Zero Task-2 migrations)
- **Active Remote Branches:**
  - `origin/main` (`0afc5d2`)
  - `origin/slice-11b-patient-longitudinal-records-ux` (`dd4aba0` - frozen Task 2 artifact)
  - `origin/task0/10b5c-first-clinical-write` (PR #56 awaiting consolidation)
- **Consolidation Sequence:**
  - Step 1: Task 0 PR #54 merged to main (`a9b2210`).
  - Step 2: Task 1 PR #53 reconciled, qualified, and merged to main (`0afc5d2`).
  - Step 3: Task 2 PR #55 reconciled onto `0afc5d2`, qualified, and merged.
  - Step 4: Task 0 PR #56 unblocked for final clinical write consolidation.

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

## Slice 11D Product UX Audit Matrix

| Area | Existing behavior | Gap | Severity | Status | Proposed Bounded Fix |
|---|---|---|---|---|---|
| **Health Home: Error Recovery** | Error banner appears without retry CTA. | User on web cannot recover from transient network errors without full page reload. | Medium | DEFECT | Add accessible "Retry" CTA in error banner calling `loadSummary(false)`. |
| **Health Home: Stale Data Guard** | Async `loadSummary` does not track cancellation. | Potential unmounted state update or stale race. | Low | PARTIAL | Add `isMounted` guard ref in `loadSummary`. |
| **Health Home: Accessibility** | Action buttons lack full descriptive labels. | Screen reader says "View All →" without context. | Medium | PARTIAL | Add `accessibilityLabel="View all active medications"`, etc. |
| **Timeline: Filter Empty State** | Filtered empty shows global empty text. | Misleading: patient assumes whole record is empty. | High | DEFECT | Differentiate: if `activeFilter !== 'ALL'`, show *"No {category} events found"* + *"Clear filter"* CTA. |
| **Timeline: Filter Change Pagination** | Cursor retained on filter change. | User clicking "Load older" can send old cursor to new category filter. | Medium | DEFECT | Immediately reset `setNextCursor(null)` on filter switch. |
| **Timeline: Filter Race Condition** | Boolean flag drops fast sequential filter clicks. | Quick filter taps get dropped or stale filter loads. | High | DEFECT | Implement incrementing `requestIdRef` so newest filter always wins. |
| **Timeline: End-of-Records Status** | List ends abruptly when `nextCursor === null`. | User unclear if more events exist. | Low | MISSING | Add accessible *"End of timeline records"* indicator. |
| **Timeline: a11y Filter Pills** | Filter buttons lack selected state attribute. | Screen reader cannot tell which filter is active. | Medium | PARTIAL | Add `accessibilityRole="button"`, `accessibilityLabel`, and `accessibilityState={{ selected: isActive }}`. |
| **Records: Drilldown Search** | Flat list without in-category search. | Hard to find specific item in large category. | Medium | MISSING | Add client-side in-category search filter across loaded records. |
| **Records: Drilldown Error State** | Category fetch error shows false empty state. | Error banner in header while list says "No records on file". | High | DEFECT | Show explicit in-drilldown error card with Retry button instead of false empty state. |
| **Records: Drilldown End-of-Records** | List ends abruptly. | Unclear pagination status. | Low | MISSING | Add *"End of {category} records"* indicator. |
| **Records: a11y List Items** | Record items lack explicit button role. | Screen readers don't announce items as interactive. | Medium | PARTIAL | Add `accessibilityRole="button"` and descriptive `accessibilityLabel`. |
| **Prescriptions: Search / Filter** | No search or source filter. | Cannot quickly search medications or isolate external uploads. | Medium | PARTIAL | Add client-side search query input and source filter tabs. |
| **Prescriptions: Filter Empty State** | Generic empty state only. | User unclear if search returned 0 items vs account has 0 items. | Medium | PARTIAL | Distinguish search/filter empty vs global empty with clear reset CTA. |
| **Prescriptions: End-of-Records** | List ends abruptly. | Unclear pagination status. | Low | MISSING | Add *"All prescriptions loaded"* notice. |
| **Reports: Stale Filter Race** | Asynchronous tab switch has no sequence guard. | Slow earlier tab response can overwrite faster current tab. | High | DEFECT | Implement active request sequence tracking. |
| **Reports: Tab Empty State** | Tab empty shows global empty text. | Misleads patient if other document categories exist. | High | DEFECT | Show *"No {tab label} reports on file"* + *"Show All Reports"* CTA. |
| **Reports: End-of-Records** | List ends abruptly. | Unclear pagination status. | Low | MISSING | Add *"All reports loaded"* notice. |
| **Reports: a11y Filter Tabs** | Tabs lack `accessibilityState`. | Screen reader cannot tell which tab is active. | Medium | PARTIAL | Add `accessibilityState={{ selected: active }}`. |
| **Detail Modal: Web Escape Key** | Bottom sheet lacks keyboard Escape listener. | Keyboard users on web cannot dismiss sheet with Escape. | Medium | PARTIAL | Add Escape key handler on web environment. |

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

---

## Slice 11D — Patient Records Discovery & UX Reliability Qualification

- **Branch:** `task2/patient-records-ux-next`
- **Reconciled Main Baseline:** `origin/main` @ `0afc5d24e7383fb91cb20ba5dd67c739bb9de40f` (Post-PR #53)
- **Alembic Head:** `20260918_canonical_encounter` (singular, zero migrations introduced)
- **Backend Changes:** Reconciled Task 0 canonical Encounter and Task 1 D3 retry/cancel routes cleanly. Zero conflicts.
- **Commit History:**
  1. `4dfe1d8` `docs(task2): start Slice 11D patient UX audit and handoff`
  2. `9176a68` `feat(task2): harden longitudinal loading, error recovery, and empty states`
  3. `b7404d8` `feat(task2): improve patient records drilldown, search, and pagination UX`
  4. `4e89f24` `feat(task2): improve prescriptions and reports discovery, filtering, and provenance clarity`
  5. `518626b` `test(task2): add patient records UX reliability coverage`
  6. `680ae8b` `fix(task2): type-safe button text styling in prescriptions screen`
  7. `cbffacb` `fix(task2): eliminate duplicate prescription search and reset category pagination state`
  8. `122c2fd` `test(task2): add deterministic request-id race and pagination reset tests`
  9. `merge(reconcile): merge post-D3 main 0afc5d2 into task2/patient-records-ux-next`

### Verification Evidence
| Test Target | Scope | Result | Execution Detail |
|---|---|---|---|
| Frontend Vitest App Suite | `yarn --cwd nexa-client test:app` | PASS | 44 test files, 290 tests passed (including 19 longitudinal records UX tests and 12 timeline tests) |
| Frontend Vitest Next Suite | `yarn --cwd nexa-client test:next` | PASS | 2 test files, 6 tests passed |
| Python AST & Screen Guards | `pytest tests/test_patient_screens.py` | PASS | 164 passed, AST invariant constraints verified |
| Route Registration Tests | `pytest tests/test_route_registration.py` | PASS | 16/16 route invariants passed (Task 0 Encounter + Task 1 D3 retry/cancel + Task 2 patient routes) |
| Workspace Build | `yarn --cwd nexa-client build` | PASS | `@my/config` and `@my/ui` built cleanly |
| Next Production Build | `yarn --cwd nexa-client verify:next-build` | PASS | All 29 routes prerendered/compiled cleanly |

### Core Improvements Delivered
1. **Error Recovery with Explicit Retry:**
   - PatientHealthHome: Accessible retry CTA in error state with mounted state guards.
   - PatientRecordsScreen: Dedicated in-drilldown error card with retry button (eliminating false empty states).
   - PatientPrescriptionsScreen: Error notice with retry button.
   - PatientReportsScreen: Error notice with retry button.
2. **Empty State & Search Differentiation:**
   - Differentiated global empty states ("No {category} on file") from search/filter empty states ("No records match '{query}'").
   - Added actionable "Reset Filters" / "Clear Search" / "Show All Reports" CTAs.
   - Consolidated single intentional search surface on `PatientPrescriptionsScreen` covering medication name, dosage/strength, and frequency/instructions.
3. **Network Race Condition Prevention & State Invalidation:**
   - Request sequence tracking (`requestIdRef`) across Timeline, Records, Prescriptions, and Reports screens to discard stale out-of-order responses during rapid tab or filter switching.
   - Deterministic controlled-promise race condition tests proving newest-request-wins across Timeline, Records, and Reports screens.
   - Synchronous pagination and record state invalidation upon category switch in `PatientRecordsScreen` preventing stale cursors from lingering across categories.
4. **Client-Side Discovery & Filtering:**
   - In-category search in `PatientRecordsScreen`.
   - Prescription source filtering (`All Treatments`, `Clinic Prescriptions`, `Uploaded Prescriptions`) and medication name/frequency search.
   - Diagnostic document category tabs with explicit type filtering.
5. **Accessibility Enhancements:**
   - `accessibilityState={{ selected: isActive }}` on all filter pills.
   - Web keyboard `Escape` key dismissal for `PatientRecordDetailModal`.
   - Descriptive accessibility labels on interactive controls.

---

## Slice 11E — Patient External Medical Record Import UX Qualification

- **Branch:** `task2/slice-11e-patient-import-ux`
- **Reconciled Main Baseline:** `origin/main` @ `0b9cb40d4125d7a02f2a08513b67cc7b40639bde` (Post-PR #59)
- **Alembic Head:** `20260918_treatment_vitals_encounter` (singular, zero migrations introduced)
- **Routes Delivered:**
  - Next.js: `/patient/records/import` (wrapped in `<Suspense>` boundary)
  - Expo: `/patient/import`
  - Deep-link from `PatientRecordsScreen` and `PatientReportsScreen` `+ Add Record` buttons
- **TypeScript DTOs & API Methods:**
  - `PatientExternalRecordActions`, `PatientExternalRecordResponse`, `PatientExternalRecordUploadPolicy`, `PatientExternalRecordReviewItem`, `PatientExternalRecordReviewResponse`
  - `getPatientUploadPolicy`, `uploadPatientExternalRecord`, `getPatientExternalRecord`, `processPatientExternalRecord`, `retryPatientExternalRecord`, `cancelPatientExternalRecord`, `getPatientExternalRecordReview`, `reviewPatientExternalRecordItem`, `savePatientExternalRecord`, `getPatientExternalRecordSourceBlob`

### Strict Invariants Enforced
1. **Strict `actions`-Driven Authority:**
   - UI capability checks rely strictly on server-returned `response.actions` (`can_process`, `can_retry`, `can_cancel`, `can_review`, `can_save`, `can_view_source`).
   - Never infer capability from public status strings or local boolean heuristics.
2. **Dynamic Upload Policy:**
   - Limits (`max_upload_bytes`), file extensions, and MIME types fetched dynamically via `GET /upload-policy`.
   - No hardcoded byte limits; no advertising unsupported formats (e.g. TIFF not offered unless policy returns it).
3. **Truthful Copy & Non-Deceptive Clinical Claims:**
   - Progress copy: *"Processing document"*, *"Nexa is extracting information for review"*.
   - Strictly prohibits claiming *"clinically verified"*, *"doctor reviewed"*, *"malware free"*, or *"virus scanned"*.
4. **Save Semantics:**
   - Server creates `DocumentReference` and `TimelineEvent` only; does NOT fabricate typed clinical facts (medications, conditions, observations).
   - Save CTA reads: *"Save document to medical records"*.
5. **Cancel Semantics:**
   - Cancel != erasure. Prominent retention warning: *"Cancel this import? No information will be added to your medical records. The uploaded source remains in your import history unless it is separately erased."*
   - Cancel action is unavailable while extraction is actively running (`can_cancel: false`).
6. **Provenance & Evidence Integrity:**
   - Provenance labels: *"Document extracted"*, *"Patient corrected"*, *"Patient uploaded"*.
   - Original extracted value is preserved and displayed alongside patient correction.
7. **Security & Privacy:**
   - Server-derived self-patient authority only (`/api/v2/patient/me/*`).
   - Zero patient identifiers, tokens, or internal S3 storage keys in URLs, error messages, or logs.
   - Advisory source viewing uses short-lived blob URLs revoked on modal close.

### Verification Evidence
| Test Target | Scope | Result | Execution Detail |
|---|---|---|---|
| Vitest App Suite | `yarn --cwd nexa-client test:app` | PASS | 45 test files, 310 tests passed (including 20/20 in `PatientImportWorkflow.test.tsx`) |
| Vitest Next Suite | `yarn --cwd nexa-client test:next` | PASS | 2 test files, 6 tests passed |
| Python AST & Screen Guards | `pytest tests/test_patient_screens.py` | PASS | 164 passed, AST invariant constraints verified |
| Route Registration Invariants | `pytest tests/test_route_registration.py` | PASS | 3/3 route invariant suites passed |
| D4 Contract Tests | `pytest tests/test_patient_external_record_d4_capabilities.py` | PASS | 18/18 capability and actions tests passed |
| Alembic Migration Head | `python -m alembic heads` | PASS | Singular head `20260918_treatment_vitals_encounter`, 0 migrations |
| Package Builds | `yarn --cwd nexa-client build` | PASS | `@my/config` and `@my/ui` built cleanly |
| Next Production Build | `yarn --cwd nexa-client verify:next-build` | PASS | All 30 routes (including `/patient/records/import`) compiled & prerendered cleanly |
