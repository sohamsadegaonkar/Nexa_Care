# Patient UX & Product Gap Audit: Next Bounded Patient Slice

## Executive Summary

Following the completion and merge of **Slice 11F** (PR #65, commit `e3505baa6d8a9591ccfef7fb10c43012b1c80f30`), this audit performs a repository-grounded analysis of the patient product surface across Next.js and Expo. 

With the recent landing of **Task 0 PR #66** (Canonical Encounter & Treatment Session Request) and **Task 1 D6 PR #64** (Clamd Malware Scanner & Provider WRITE_VITALS), the Nexa Care core architecture has established a live clinical write capability. However, the patient product experience exhibits significant downstream gaps:
1. **Provenance Obfuscation:** Vitals written by clinicians during qualified Treatment Sessions are currently grouped under the legacy label `"Manual entry"`, lacking hospital, facility, or encounter provenance.
2. **Provenance Inconsistency:** The application displays contradictory provenance labels across screens (`"Clinician recorded"` in Health Home vs. `"Manual entry"` in Timeline vs. `"Clinician Recorded"` in Records vs. `"Document extracted"` vs. `"AI-extracted from document"`).
3. **Post-Encounter Patient Blindspot:** Patients approving a Treatment Session are redirected to Access History, which strictly filters out consent claims and clinical write events, leaving the patient with zero record of the session.
4. **Terminal Import Hang (Post-D6):** When an imported document is rejected by malware scanning or fails extraction terminally (`can_retry === false`), the import UI remains stuck in an active spinner state indefinitely.

This document inventories all patient surfaces, maps backend-to-UI coverage, audits end-to-end user journeys, evaluates accessibility and navigation, ranks candidate slices, and specifies exactly **ONE** recommended next bounded slice.

---

## Phase 0: Patient Surface Inventory

### 1. Route Inventory & Parity Map

| Route (Next / Expo) | Shared Feature Component | Primary Backend Endpoints | Auth / Security Model | Web / Native Parity | Test Coverage | Status & Observations |
|---|---|---|---|---|---|---|
| `/patient/login` | `PatientLoginScreen.tsx` | `POST /api/v2/auth/patient/session`, Supabase OTP | Unauthenticated entry -> `AuthenticatedPatientSession` | Full parity | `PatientLoginScreen.test.tsx`, `test_patient_screens.py` | Complete. Standard session establishment. |
| `/patient/account-recovery` (Next)<br>`/patient/recovery` (Expo) | `PatientRegistrationRecoveryScreen.tsx` / `PatientRecoveryScreen.tsx` | `POST /api/v2/patient-recovery/*` | Unauthenticated / Recovery OTP | **Path Divergence** (`account-recovery` vs `recovery`) | `PatientRegistrationRecoveryScreen.test.tsx` | Functional, but route naming differs across platforms. |
| `/patient/onboarding` | `PatientOnboardingScreen.tsx` + `PatientOnboardingCard.tsx` | `GET/PUT /api/v2/patient/me/profile`, `GET /api/v2/patient/me/legal-requirements`, `POST /api/v2/patient/me/legal-acceptances`, `GET /api/v2/patient/me/onboarding-status` | `AuthenticatedPatientSession` | Full parity | `PatientOnboardingWorkflow.test.tsx`, `test_patient_onboarding.py` | Complete (Slice 11F). Non-coercive historical record card. |
| `/patient/dashboard` (Next)<br>`/patient/records` (Expo home) | `PatientHealthHome.tsx` | `GET /api/v2/patient/me/summary` | `require_self_patient_access()` | Partial (Next uses `PatientShell` layout; Expo uses Stack) | `PatientLongitudinalRecords.test.tsx`, `test_patient_screens.py` | Complete. Decoupled from onboarding status. |
| `/patient/records` | `PatientRecordsScreen.tsx`, `PatientRecordDetailModal.tsx` | `GET /api/v2/patient/me/records`, `GET /api/v2/patient/me/records/{category}`, `GET /api/v2/patient/me/records/{category}/{id}` | `require_self_patient_access()` | Full parity | `PatientLongitudinalRecords.test.tsx`, `test_patient_records.py` | Complete. Categorized record browsing. |
| `/patient/records/import` | `PatientImportScreen.tsx` | `GET /api/v2/patient/external-records/upload-policy`, `POST /api/v2/patient/external-records`, `GET /api/v2/patient/external-records/{id}`, `POST .../{id}/retry`, `POST .../{id}/cancel`, `GET .../{id}/review`, `POST .../{id}/review/decisions`, `POST .../{id}/save`, `GET .../{id}/source` | `AuthenticatedPatientSession` | Full parity | `PatientImportWorkflow.test.tsx`, `test_patient_screens.py` | Complete (Slice 11E). Closed `returnTo` allowlist. |
| `/patient/timeline` | `PatientTimelineScreen.tsx` | `GET /api/v2/patient/me/timeline` | `require_self_patient_access()` | Full parity | `PatientTimelineScreen.test.tsx`, `test_patient_records.py` | Complete. Keyset pagination and category filtering. |
| `/patient/prescriptions` | `PatientPrescriptionsScreen.tsx` | `GET /api/v2/patient/me/prescriptions` | `require_self_patient_access()` | Full parity | `PatientLongitudinalRecords.test.tsx` | Complete. Unified view of active Rx and imported Rx. |
| `/patient/reports` | `PatientReportsScreen.tsx` | `GET /api/v2/patient/me/reports`, `GET /api/v2/patient/me/documents/{id}` | `require_self_patient_access()` | Full parity | `PatientLongitudinalRecords.test.tsx` | Complete. Diagnostic documents and reports. |
| `/patient/access-history` | `AccessHistoryScreen.tsx` | `GET /api/v2/patient/me/access-history` | `require_self_patient_access()` | Full parity | `AccessHistoryScreen.test.tsx`, `test_access_history.py` | Complete. Audit ledger projection. |
| `/patient/discoverability` | `PhoneDiscoverabilityScreen.tsx` | `GET/POST/DELETE /api/v2/patient/me/discoverability/phone` | `AuthenticatedPatientSession` | Full parity | `test_patient_phone_discoverability.py` | Complete. Opt-in phone discovery control. |
| `/patient/profile` | `PatientProfilePage` (Next)<br>**Missing on Expo** | `GET/PUT /api/v2/patient/me/profile`, `GET /api/v2/patient/me/onboarding-status` | `AuthenticatedPatientSession` | **Missing on Expo** | Manual / Next build | Next has a full self-service profile page; Expo has no `/patient/profile` route. |
| `/patient/treatment-request` | `TreatmentSessionRequestScreen.tsx` (Expo)<br>**Missing on Next** | `GET /api/v2/treatment-session/v1/challenge/{id}`, `POST /api/v2/treatment-session/v1/approve-signed` | Native hardware device key + biometrics | **Missing on Next (404 Dead End)** | `TreatmentSessionRequestScreen.test.tsx` | Health Home tile links to `/patient/treatment-request` on web, resulting in a 404! |
| `/patient/secure-device`<br>`/patient/enrolled`<br>`/patient/devices` | Native device components | `/api/v2/patient/devices/*` | Native hardware keys | Native Only (Expo) | `currentDeviceEnrollment.test.ts`, `patientDeviceManagement.test.ts` | Intentionally native-only for cryptographic hardware key enrollment. |
| `/patient/consent-request`<br>`/patient/biometric-approval`<br>`/patient/approval-result` | V3 Consent screens | `/api/v2/consent/v3/*` | Native biometric signing | Native Only (Expo) | `ConsentRequestScreen.test.tsx` | V3 signed consent approval screens. |
| `/patient/[id]` | `ProfileScreen.tsx` | `GET /api/v2/patient/{id}` (Clinical record or emergency summary) | Provider capability token or emergency auth | Parity | `ProfileScreen.tsx` | **Stale/Misplaced Route**: Clinician/emergency summary view placed under `/patient/[id]`. |

---

## Phase 1: Backend-to-UI Gap Map

| Endpoint | Method | Classification | Justification & Product Gap |
|---|---|---|---|
| `/api/v2/patient/me/profile` | GET, PUT | `FULL_UI` | Surfaced in Next `/patient/profile` and during Onboarding. |
| `/api/v2/patient/me/legal-requirements` | GET | `FULL_UI` | Surfaced during Onboarding. |
| `/api/v2/patient/me/legal-acceptances` | POST | `FULL_UI` | Surfaced during Onboarding. |
| `/api/v2/patient/me/onboarding-status` | GET | `FULL_UI` | Authoritative source for onboarding progression. |
| `/api/v2/patient/me/discoverability/phone` | GET, DELETE | `FULL_UI` | Surfaced on `/patient/discoverability`. |
| `/api/v2/patient/me/discoverability/phone/enable` | POST | `FULL_UI` | Surfaced on `/patient/discoverability` with fresh OTP. |
| `/api/v2/patient/me/summary` | GET | `FULL_UI` | Surfaced on `/patient/dashboard` (Health Home). |
| `/api/v2/patient/me/timeline` | GET | `FULL_UI` | Surfaced on `/patient/timeline`. |
| `/api/v2/patient/me/records` | GET | `FULL_UI` | Surfaced on `/patient/records`. |
| `/api/v2/patient/me/records/{category}` | GET | `FULL_UI` | Surfaced on `/patient/records` (paginated list). |
| `/api/v2/patient/me/records/{category}/{id}` | GET | `FULL_UI` | Surfaced in `PatientRecordDetailModal`. |
| `/api/v2/patient/me/prescriptions` | GET | `FULL_UI` | Surfaced on `/patient/prescriptions`. |
| `/api/v2/patient/me/reports` | GET | `FULL_UI` | Surfaced on `/patient/reports`. |
| `/api/v2/patient/me/documents/{document_id}` | GET | `FULL_UI` | Surfaced in `PatientReportsScreen`. |
| `/api/v2/patient/me/access-history` | GET | `FULL_UI` | Surfaced on `/patient/access-history`. |
| `/api/v2/patient/external-records/*` (9 routes) | GET, POST | `FULL_UI` | Surfaced in `PatientImportScreen`. |
| `/api/v2/treatment-session/v1/challenge/{id}` | GET | `PARTIAL_UI` | Supported on Expo (`TreatmentSessionRequestScreen`), missing on Next. |
| `/api/v2/treatment-session/v1/approve-signed` | POST | `PARTIAL_UI` | Supported on Expo via native hardware keys. |
| `/api/v2/consent/history/self` | GET | `NO_UI` | **Gap:** Patient has zero visibility into active and historical consent grants and Treatment Session grants. The backend returns active/revoked/expired status, purpose, scope, and validity window, but no patient screen calls it. |
| `/api/v2/consent/request/{request_id}/revoke` | DELETE | `NO_UI` | **Gap:** Profile UI promises SEC-022 "Sovereign Consent Revocation: You can revoke consent at any moment", but there is no patient UI to invoke this. In addition, `apiClient.ts` calls it via `POST` instead of `DELETE`. |
| `/api/v2/patient/me/erasure` | POST | `INTERNAL_ONLY` | Governed by India DPDP / erasure registry; requires operator confirmation and verified backup irreversibility. |

---

## Phase 2: Product Journey Audit

1. **New Patient Registration:**
   - Patient enters Indian phone number, receives Supabase SMS OTP, establishes session. Smooth handoff.
2. **Profile & Legal Onboarding:**
   - Patient accepts Terms of Service and Privacy Policy, optionally updates profile name/DOB. Authoritative onboarding status drives completion. Smooth handoff.
3. **Optional Historical Record Import:**
   - Onboarding card offers non-coercive entry (`/patient/records/import?returnTo=onboarding`) and "Skip for now" (`/patient/dashboard`). Reuses `PatientImportScreen`. Smooth handoff.
4. **Patient Dashboard (Health Home):**
   - Displays summary counts, latest vitals, active medications, recent reports, and quick action tiles.
   - **Broken Handoff:** The "Treatment Approval" tile navigates to `/patient/treatment-request`. On Next.js web, this route is absent, producing a 404 error.
5. **Medical Records & Categories:**
   - Navigates to categorized records (Allergies, Medications, Vitals, Labs, Documents). Detail modal opens with structured fields and provenance.
6. **Timeline:**
   - Longitudinal chronological feed.
   - **Broken Handoff / Provenance Defect:** Clinician-recorded vitals from Treatment Sessions appear with the ambiguous label `"Manual entry"` and `"Manual Entry"` badge, without clinician or facility attribution.
7. **Prescriptions / Medications:**
   - Displays active prescribed medications alongside imported external prescriptions. Works cleanly.
8. **Diagnostic Reports:**
   - Displays uploaded documents and lab reports with "View Source" affordance. Works cleanly.
9. **Access History:**
   - Displays provider audit reads.
   - **Broken Handoff:** After approving a Treatment Session, the patient is redirected to `/patient/access-history`. However, `_READ_PATIENT_ACCESS_HISTORY_SQL` excludes `CONSENT_ACCESS_CLAIMED`, `CONSENT_APPROVED_SIGNED`, and `PATIENT_RECORD_APPEND_SUCCESS`. The patient sees zero record of the session they just approved.
10. **Provider Discovery:**
    - Controls phone number discoverability via fresh OTP. Works cleanly.
11. **Treatment Session Approval:**
    - Available only on mobile. Patient enters 36-char request ID, reviews operations and provider name, signs with biometric.
    - Post-approval redirects to Access History (where it is invisible).
12. **Revisit After Treatment:**
    - Patient checks vitals: vitals appear with `source: "manual"` and `source_display: "Clinician Recorded"`.
    - Encounter ID and facility name are not returned.
    - Patient cannot view active/recent treatment sessions or revoke ongoing session authority.

---

## Phase 3: Post-Treatment Patient Experience

Now that provider `WRITE_VITALS` is integrated and active:
- **Visibility of New Vitals:** The patient can see the new observation in `latest_vitals` on Health Home, in `/patient/records/vitals`, and in the Timeline.
- **Clinician-Recorded Identification:**
  - On `/patient/records`: The detail modal displays `"Clinician Recorded"`.
  - On `/patient/timeline`: The entry displays `"Manual entry"` with a `"Manual Entry"` badge.
- **Facility & Provider Provenance:**
  - **Not visible.** Although `Vitals` stores `encounter_id`, and `CanonicalEncounter` stores `hospital_id` and `provider_id`, the patient record endpoints (`/summary`, `/records/vitals`, `/records/vitals/{id}`, `/timeline`) do **not** join or return the encounter details or facility display name.
- **Observation Timestamp:** The `recorded_at` ISO-8601 timestamp is correctly preserved and displayed.
- **Distinction from Imported/Extracted Values:**
  - Imported documents have `source: "patient_uploaded"` or `"ai_extracted"`.
  - Clinician treatment vitals have `source: "manual"`.
  - However, because treatment vitals share `source: "manual"` with older manual entries, the patient cannot distinguish an official hospital vitals observation from an unverified manual input.

---

## Phase 4: Treatment Session Patient UX

- **Incoming Request Visibility:** There is currently no push notification or in-app inbox for incoming treatment requests. Patients must manually receive and type a 36-character UUID into `TreatmentSessionRequestScreen`.
- **Approval / Rejection:** Native biometric signature works cleanly on iOS and Android.
- **Exact Requested Operations:** Clearly displayed (`CREATE_ENCOUNTER`, `WRITE_VITALS`).
- **Expiry:** Strict 120-second challenge window is enforced.
- **After-Session UX & Missing Surface:**
  - Once approved, the provider claims the session (`POST /{request_id}/claim`), which mints a temporary capability and inserts a `ConsentGrantLog` row with `scope=["treatment.session.v1"]` (e.g. 15-minute access window).
  - The patient has **no surface** showing that an active treatment session is currently in progress.
  - The patient cannot see how much time remains on the session, cannot see what operations have been performed, and cannot revoke the session early.
  - Backend support already exists: `ConsentGrantLog` tracks active grants and `DELETE /api/v2/consent/request/{request_id}/revoke` terminates them.

---

## Phase 5: Import Experience After D6

Auditing `PatientImportScreen.tsx` against the D6 malware scanner integration:
- **Scanner Unavailable:**
  - Backend returns `status: "could_not_process"` with `actions.can_retry: true`.
  - UI displays `"Extraction Paused"` with `"🔄 Retry Extraction"` and non-blocking skip affordances. Behaves truthfully.
- **Malicious Source Rejected & Terminal Failures:**
  - Backend returns `status: "could_not_process"` with `actions.can_retry: false`.
  - **CRITICAL UI DEFECT:** `PatientImportScreen.tsx` only renders the retry card when `can_retry === true`. When `status === "could_not_process"` and `can_retry === false`, the UI renders **only** the active spinner (`<Spinner size="large" />` with `"Processing document: Nexa is extracting information for review"`).
  - The UI hangs indefinitely in a loading state, never informing the patient that the document was rejected or failed extraction!
- **ClamAV Internals:**
  - The backend returns stable error codes (`SOURCE_MALWARE_DETECTED`, `SOURCE_MALWARE_SCANNER_UNAVAILABLE`) and does not leak ClamAV engine versions or virus signatures.
- **Required UI Adjustment:**
  - The import screen must render a terminal failure card when `status === 'could_not_process'` and `can_retry === false`:
    - Title: `"Document Could Not Be Processed"`
    - Copy: `"This document could not be safely extracted. No information was added to your medical records."`
    - Action: `"Try Another Document"` (reset flow) and `"Back to Records / Onboarding"`.

---

## Phase 6: Records Provenance Consistency

### Provenance Audit Across Surfaces

| Surface | Entity | Current Label / Badge | Ambiguity / Defect | Recommended Standard Label |
|---|---|---|---|---|
| Health Home | Summary Vitals | `"Clinician recorded"` (Stethoscope icon) | Accurate | `Clinician recorded` |
| Health Home | Summary Allergies | `"Clinician recorded"` / `"Document extracted"` | Accurate | `Clinician recorded` / `Document extracted` |
| Records List | Vitals, Meds, Labs | Badges: `Clinician Recorded`, `Document Extracted` | Capitalization varies | `Clinician recorded`, `Document extracted` |
| Record Detail Modal | Vitals | `source_display: "Clinician Recorded"` | Missing facility/encounter context | `Clinician recorded at {Hospital}` |
| Record Detail Modal | Labs | `source_display: "Laboratory Integration"` | Different from vitals/meds | `Clinician recorded` or `Laboratory report` |
| Record Detail Modal | Documents | `source_display: "Imported by you from an external report"` | Wordy | `Patient imported` |
| Timeline | Vitals (Treatment) | `source_display: "Manual entry"`, Badge: `"Manual Entry"` | **Misleading:** Treatment vitals are not "manual entries"; they are clinician-recorded observations. | `Clinician recorded` |
| Timeline | Meds | `source_display: "Manual entry by {Dr. X}"` | Inconsistent with Records | `Clinician prescribed by {Dr. X}` |
| Timeline | Extracted items | `source_display: "AI-extracted from document"` | Uses "AI" without review context | `Document extracted ({X}% confidence)` |

---

## Phase 7: Navigation Consistency

1. **Web vs. Native Route Parity:**
   - `/patient/treatment-request`: Registered in Expo `_layout.tsx`, but missing in Next.js `app/patient/`. Web users clicking the Health Home tile encounter a 404 dead end.
   - `/patient/profile`: Present in Next.js, completely missing in Expo router.
   - `/patient/account-recovery` (Next) vs. `/patient/recovery` (Expo): Inconsistent segment name.
2. **Post-Approval Return Path:**
   - `TreatmentSessionRequestScreen` routes solely to `/patient/access-history`, which does not display treatment approvals. Should provide return to `/patient/dashboard` or a dedicated Treatment Sessions view.
3. **Records ↔ Timeline Interlinking:**
   - Records screen has no shortcut to view the item in Timeline; Timeline has no shortcut to jump to the full category in Records.

---

## Phase 8: Accessibility Audit

1. **Dynamic Status Announcements:**
   - `TreatmentSessionRequestScreen` lacks an `aria-live` announcement when challenge data is fetched, expires, or errors.
   - `PatientImportScreen` live region does not announce terminal processing failures.
2. **Touch Targets:**
   - In `PatientHealthHome.tsx`, the top `Access History →` button uses `size="$2"`, measuring ~32px height, violating WCAG 2.5.5 / 2.5.8 (minimum 44px/48px).
3. **Keyboard & Focus Navigation:**
   - `PatientRecordDetailModal.tsx` does not return keyboard focus to the triggering list row upon closing.
   - In `PatientHealthHome.tsx`, quick navigation action tiles are non-semantic `<YStack>` elements without keyboard `onKeyDown` handlers on web.

---

## Phase 9: Candidate Slices Ranking

### Candidate A: Patient Post-Treatment Provenance & Clinical Encounter Visibility
- **Problem:** Clinician-recorded vitals from qualified Treatment Sessions are labeled as generic `"Manual entry"` in Timeline, lack hospital/encounter attribution, and suffer from conflicting provenance terminology across Health Home, Records, and Timeline.
- **Affected User:** Patients reviewing vitals recorded during recent provider visits.
- **Backend Readiness:** High. `Vitals.encounter_id` and `CanonicalEncounter` table already exist. Requires enriching the patient read projection in `app/api/v2/patient_record_routes.py` to resolve hospital display name.
- **Frontend Readiness:** High. Detail modal, timeline items, and badges are already modularized.
- **Security Implications:** Read-only projection; exposes only authorized hospital display names; zero PHI leakage.
- **Files Likely Touched:**
  - `app/api/v2/patient_record_routes.py`
  - `nexa-client/packages/app/features/patient/PatientRecordDetailModal.tsx`
  - `nexa-client/packages/app/features/patient/PatientTimelineScreen.tsx`
  - `nexa-client/packages/app/features/patient/badges/SourceBadge.tsx`
- **Migration Requirement:** Zero database migrations.
- **Complexity:** Low-Medium.
- **Dependencies:** None.
- **Measurable Criteria:**
  1. Timeline displays `"Clinician recorded"` instead of `"Manual entry"` for clinical vitals.
  2. Record detail modal displays `"Clinician recorded at {Hospital Name}"` when `encounter_id` is present.
  3. Consistent provenance terminology across Health Home, Records, and Timeline.

### Candidate B: Patient Import Terminal Failure & Error Transparency UX (Post-D6)
- **Problem:** When an imported document is rejected by malware scanning or fails extraction terminally (`can_retry === false`), `PatientImportScreen.tsx` hangs indefinitely on an active spinner with no error message or recovery action.
- **Affected User:** Patients uploading malformed, encrypted, or unsafe medical documents.
- **Backend Readiness:** 100% complete. Backend already returns `status: "could_not_process"` and `actions.can_retry: false`.
- **Frontend Readiness:** High. Purely frontend state rendering in `PatientImportScreen.tsx`.
- **Security Implications:** Truthful error messaging without disclosing ClamAV internals.
- **Files Likely Touched:**
  - `nexa-client/packages/app/features/patient/PatientImportScreen.tsx`
  - `nexa-client/packages/app/features/patient/PatientImportWorkflow.test.tsx`
- **Migration Requirement:** Zero database migrations.
- **Complexity:** Low.
- **Dependencies:** None.
- **Measurable Criteria:** Terminal extraction failures immediately display an error card and provide recovery paths without indefinite spinning.

### Candidate C: Patient Active Treatment Sessions & Sovereign Consent Revocation UX
- **Problem:** Patients have no visibility into active treatment sessions or consent grants after approval, cannot see session countdown or permitted operations, and have no UI to exercise sovereign consent revocation (SEC-022).
- **Affected User:** Patients undergoing active treatment sessions at medical facilities.
- **Backend Readiness:** Medium. Requires extending `GET /api/v2/consent/history/self` to include provider/hospital display names and treatment operations, and fixing `DELETE /api/v2/consent/request/{id}/revoke` alignment.
- **Frontend Readiness:** Medium. Requires a dedicated active sessions screen or expanded Access History.
- **Security Implications:** High. Revocation mutates Redis and PostgreSQL session records.
- **Complexity:** Medium-High.

---

## Phase 10: Selected Recommended Next Slice

### Recommended Slice: **Patient Post-Treatment Provenance & Clinical Encounter Visibility**

#### 1. Rationale
Now that provider `WRITE_VITALS` and `CanonicalEncounter` are merged on `main`, the patient-facing side of that transaction is incomplete:
- Clinical vitals written in hospital treatment sessions appear indistinguishable from unverified manual notes.
- Provenance labels across Health Home, Records, and Timeline contradict each other.
- It is fully supported by the existing database schema (`Vitals.encounter_id` -> `CanonicalEncounter` -> `HospitalRegistry`).
- It requires zero database migrations and introduces zero clinical authority risks.

#### 2. Precise Scope
1. **Encounter & Hospital Provenance Resolution:**
   - In `app/api/v2/patient_record_routes.py`, update `get_my_record_detail("vitals", ...)` and `_fetch_patient_longitudinal_timeline` to join `CanonicalEncounter` and `HospitalRegistry` when `vitals.encounter_id` is present.
   - Return safe hospital display name (`hospital_name`) and encounter timestamp (`encounter_recorded_at`).
2. **Provenance Terminology Standardization:**
   - Eliminate `"Manual entry"` across all patient-facing surfaces.
   - Standardize on three clear provenance categories:
     - `Clinician recorded` (optionally with `at {Hospital Name}`)
     - `Patient imported` (for external documents)
     - `Document extracted` (for AI-extracted items from imported documents, with confidence)
3. **Frontend Component Updates:**
   - Update `PatientRecordDetailModal.tsx` to render encounter and facility provenance.
   - Update `PatientTimelineScreen.tsx` and `_enrich_timeline_provenance` to display `"Clinician recorded"` and hospital badges for clinical vitals.
   - Update `SourceBadge.tsx` to ensure uniform visual style and labeling across Health Home, Records, and Timeline.
4. **Terminal Import Hang Bugfix (Opportunistic Quality Fix):**
   - In `PatientImportScreen.tsx`, ensure that `status === 'could_not_process'` with `can_retry === false` displays the terminal failure card rather than spinning indefinitely.

#### 3. Explicit Non-Scope
- No provider write capability expansion (no `WRITE_PRESCRIPTION` or `WRITE_NOTES`).
- No changes to `SignedTreatmentSessionV1` protocol or cryptographic signing.
- No push notification infrastructure for incoming treatment requests.
- No database schema migrations.

#### 4. UX Acceptance Criteria
- When a patient views a vital recorded via a Treatment Session in `PatientRecordDetailModal`:
  - It displays `"Clinician recorded at {Hospital Name}"`.
  - It displays the exact encounter timestamp.
  - It does **not** display internal UUIDs (`encounter_id`, `hospital_id`).
- When viewed on the Timeline:
  - The entry displays `"Clinician recorded"` and a hospital badge.
  - The label `"Manual entry"` does not appear.
- When an import fails terminally (e.g. malware rejected or corrupt):
  - The spinner terminates immediately and displays a clear, truthful failure notice with a retry/reset button.

#### 5. Test Plan
- **Backend Pytest:**
  - Test `get_my_record_detail` with an encounter-linked vital returns `hospital_name` and `Clinician recorded`.
  - Test `get_my_timeline` returns enriched hospital provenance for treatment vitals.
- **Frontend Vitest:**
  - Test `PatientRecordDetailModal` renders facility name when present.
  - Test `PatientTimelineScreen` renders standardized provenance badges.
  - Test `PatientImportScreen` renders terminal error card when `can_retry === false`.
- **AST / Architecture Guards:**
  - Verify zero database migrations.
  - Verify zero PHI in URLs.
