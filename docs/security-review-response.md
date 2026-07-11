# Security Review Response — Sprint 3-8

**Date:** 2026-07-10  
**Status:** All critical items addressed; documented gaps remain

---

## Issues Addressed

### 1. Frontend lock is NOT the security boundary ✅

**Change:** `PatientRecordViewerScreen` now explicitly documents that the frontend timer is a UX control only. The expired state screen shows: "The frontend timer is a UX indicator only. All data access is independently validated server-side on every request."

**Source:** `PatientRecordViewerScreen.tsx` JSDoc and expired state render.

### 2. Scope-restricted data access ✅

**Change:** Implemented `SCOPE_TO_TABS` mapping and `availableTabs` computed from consent validation scope. Tabs not in scope are hidden and their data is NOT fetched. Timeline fetch is conditional on scope.

**Note:** The backend `/api/v2/patient/{id}/summary` currently returns all data in one response. The frontend cannot prevent data from being transmitted unless the backend implements per-section endpoints or redaction. This is documented as a known gap.

### 3. Consent tokens never displayed ✅

**Change:** 
- `maskToken()` function renders only `abc123••••wxyz` format
- Access Status tab shows "Authorization: Active" badge + "Authorization Reference: abc123••••wxyz"
- No raw token displayed anywhere
- Emergency success screen shows masked authorization reference, not the consent_token value

### 4. Consent token passed as X-Consent-Token header ✅

**Change:** `PatientRecordViewerScreen` passes `{ headers: { 'X-Consent-Token': consentToken } }` on data API calls. The backend `require_consent` dependency reads this header.

### 5. Break-glass reason codes expanded ✅

**Change:** Expanded from 6 to 12 reason codes:
- `IMMEDIATE_THREAT_TO_LIFE` (replaces `LIFE_THREATENING`)
- `PATIENT_INCAPACITATED`
- `EMERGENCY_DIAGNOSTIC_DECISION`
- `EMERGENCY_MEDICATION_SAFETY`
- `UNIDENTIFIED_PATIENT`
- `SURGICAL_EMERGENCY`
- `SEVERE_BLEEDING`
- `CARDIAC_ARREST`
- `ANAPHYLAXIS`
- `ACUTE_RESPIRATORY_FAILURE`
- `SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE`
- `OTHER_CLINICALLY_JUSTIFIED_EMERGENCY` (triggers mandatory compliance review)

### 6. Clinical justification strengthened ✅

**Change:** 
- Minimum 20 characters (no more "urgent" or "aaa")
- `OTHER_CLINICALLY_JUSTIFIED_EMERGENCY` requires 50 characters
- `validateJustification()` function enforces this
- Whitespace-only values rejected
- Real-time character count feedback shown

### 7. Honest patient notification wording ✅

**Change:** Replaced "The patient will be notified" with "This access will be recorded and may trigger patient and compliance notifications." Applied across:
- EmergencyAccessScreen success view
- WaitingForApprovalScreen waiting text
- PatientRecordViewerScreen Access Status tab

### 8. AI confidence badges redesigned ✅

**Change:** `ConfidenceBadge` → `ProvenanceBadge` with three states:
- **Clinician verified** (green) — shows "Clinician verified"
- **AI extracted** (yellow/orange/red by confidence) — shows "AI extracted · 95% model confidence" + "Not yet verified"
- **Manual entry** (gray) — shows "Manual entry"

AI confidence ≥90% no longer shows green — it shows yellow to avoid implying clinical verification.

### 9. Allergies scope policy documented ✅

**Change:** Allergies banner is only shown when `allergiesInScope` is true (based on consent scope). The code documents: "If consent scope excludes allergies, the backend must reject the request. The frontend does NOT independently override the consent scope."

### 10. Consent revalidation reduced to 10 seconds ✅

**Change:** Reduced from 30s to 10s for faster revocation detection. Documented that backend still validates on every API request.

### 11. Purpose note sent to backend ✅

**Change:** `RequestConsentScreen` now includes `purpose_note` in the API request body when provided.

### 12. No tokens in URLs ✅

**Already safe:** The emergency screen navigates with `request_id` and `patient_id` in URL params. The `consent_token` from break-glass is stored in React state and passed as a header, never in the URL. The `request_id` is a non-secret reference.

### 13. Backend rate-limiting is server-enforced ✅

**Already implemented:** `_break_glass_limiter` in `consent_routes.py` enforces 3/hour/provider at the API level. The UI warning says "server-enforced."

### 14. Break-glass should provide minimal access ⚠️ Documented

**Status:** ALPHA gap. Current implementation grants full record access for 15 minutes. The EmergencyAccessScreen and documentation note this. Production MUST scope break-glass to a minimum-safety dataset (allergies, current medications, major diagnoses, recent critical labs, emergency contacts, blood group).

### 15. Break-glass re-authentication ⚠️ Documented

**Status:** ALPHA gap. The JSDoc on EmergencyAccessScreen documents: "Production MUST verify that the session's MFA is recent (e.g., within 10 minutes) before issuing break-glass."

### 16. Backend should validate reason codes ⚠️ Documented

**Status:** The backend currently accepts any string as `reason_code`. It should validate against an allow-list. This is a governance decision for a future sprint.

---

## Test Summary

- **Total tests:** 1228 passed, 258 skipped
- **New tests added:** 18 (in `test_record_viewer_and_emergency.py`)
  - `TestFrontendLockIsUXOnly` (3 tests)
  - `TestConsentTokenNotDisplayed` (2 tests)
  - `TestScopeAwareTabs` (3 tests)
  - `TestJustificationMinimumLength` (3 tests)
  - `TestConsentRevalidationInterval` (1 test)
  - `TestConfidenceBadges` expanded (3 new: verification_status, clinician_verified, not_yet_verified)
  - `TestEmergencyControlledReasonCodes` expanded (3 new: incapacitated, other, system_unavailable)
- **Ruff:** Clean, zero violations

---

## Remaining Known Gaps

| Gap | Risk | Action |
|-----|------|--------|
| Per-section scope-gated API endpoints | High | Backend must implement per-section endpoints or redaction |
| Break-glass re-authentication (recent MFA) | High | Add MFA age check before break-glass |
| Break-glass minimal scope | Medium | Limit to minimum-safety dataset |
| Backend reason code validation | Medium | Add allow-list validation in `BreakGlassConsentIssueRequest` |
| Anomaly detection for break-glass | Medium | Beyond rate limiting — flag unusual patterns |
| Patient notification delivery tracking | Medium | Create and track notification events |
| Integration tests | High | Need real API-level tests, not just source checks |
| Playwright/browser tests | High | Need browser-level behavioural validation |
