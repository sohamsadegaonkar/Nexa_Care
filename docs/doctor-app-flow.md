# Doctor App Flow Specification

**Day 14 · 2026-07-10**  
**Platform:** Next.js web app (`nexa-client/apps/next/`)  
**Components:** Tamagui + shared `apiClient`  
**Auth:** Provider session via `ProviderAuthContext`  

> **Classification:** Doctor Portal Authentication and Patient Discovery —
> implementation complete, build verified (2026-07-10 pass with prior yarn state;
> re-install required for zod/tamagui peer deps — OOM-killed in CI sandbox),
> integration validation pending.

---

## Overview

The doctor web app is the provider-facing interface for Nexa Care. It enables clinicians to search for patients, request consent for data access, wait for patient approval (via push notification + Face ID), and view consent-gated patient records. Emergency break-glass access is available for life-threatening situations with mandatory audit trailing.

**Critical invariant:** No `provider_id` placeholder or hardcoded localhost anywhere. The real provider ID comes exclusively from the session context established at login.

### Repository Structure

```
nexa-client/                    ← CANONICAL production frontend (and test source)
  apps/next/                    ← Built and deployed
  packages/app/features/doctor/ ← Production doctor screens
  packages/app/schemas/         ← Zod runtime validation schemas
  packages/app/services/        ← Shared services (nfcResolve, deviceKeys, etc.)
  packages/app/utils/           ← Shared API client and helpers
```

All frontend code lives under `nexa-client/`. The root-level `apps/` and
`packages/` directories (which previously existed as test scaffolding)
have been removed. Python tests now read source files from `nexa-client/`.

---

## Security Architecture

### The frontend lock is NOT the security boundary

The consent expiry countdown is a UX indicator only. A user can:
- pause JavaScript
- modify browser state
- alter the system clock
- interfere with timers
- replay old requests
- manually call APIs after the UI locks

Every data request must check consent server-side. The required invariant is:

```
Every data request
→ authenticate provider
→ validate consent grant
→ verify provider binding
→ verify patient binding
→ verify requested scope
→ verify expiry
→ write audit entry
→ return data
```

The frontend lock is presentation only.

### Scope-restricted data access

A consent grant may only permit certain categories. The UI:
- Only shows tabs for approved scopes
- Does NOT fetch data for unauthorized scopes
- The backend independently validates scope per endpoint

Two safe approaches for the backend:
1. **Per-section endpoints** — each validates its own scope
2. **Redacted unified response** — returns only allowed categories with `null` for unauthorized

The frontend does NOT merely hide unauthorized tabs while loading all data.

### Consent token handling

The consent token is an access credential. It must NEVER be:
- Displayed in the UI (only a masked reference is shown)
- Stored in URLs (URLs leak through history, logs, referrers, screenshots)
- Logged client-side

The token is passed as `X-Consent-Token` header on every data API call.
The server resolves authorization using the authenticated session + request reference.

### AI provenance and verification

AI-extracted fields show:
- Provenance (AI-extracted vs manual vs clinician-verified)
- Model confidence percentage (when available)
- Verification status ("Not yet verified" or "Clinician verified")

For high-risk fields (allergies, medication dosage, critical labs),
AI confidence should NEVER replace human verification.

### Allergies policy

Allergies are always visible when in the consent scope for clinical safety.
If consent scope excludes allergies, the backend must reject the request.
This is a POLICY decision that must be documented and enforced server-side.
The frontend does NOT independently override the consent scope.

---

## Screen Flow

```
┌────────────────────┐
│   Doctor Login      │  Email + password → session token
│   (MFA optional)    │  If MFA: TOTP step → verifyMfa
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│   Dashboard        │  Provider name, hospital, role, quick actions
│                    │  NFC scan button → ?mode=nfc
└───┬────┬────┬──────┘
    │    │    │
    │    │    └──────────────────────┐
    │    │                           │
    │    ▼                           ▼
    │  ┌──────────────────┐   ┌──────────────────┐
    │  │  Patient Search  │   │ Emergency Access  │
    │  │  (Manual / NFC)  │   │ (Break-Glass)    │
    │  │  NFC resolve →   │   │ 12 reason codes  │
    │  │  merged redirect │   │ min 20 char just. │
    │  └────────┬─────────┘   └────────┬─────────┘
    │           │                      │
    │           ▼                      │
    │  ┌──────────────────┐            │
    │  │  Request Consent │            │
    │  │  (purpose, scope)│            │
    │  │  purpose_note    │            │
    │  └────────┬─────────┘            │
    │           │                      │
    │           ▼                      │
    │  ┌──────────────────┐            │
    │  │  Waiting for     │            │
    │  │  Approval        │            │
    │  │  (adaptive poll) │            │
    │  └───┬──────┬───────┘            │
    │      │      │                    │
    │  Approved  Denied                │
    │      │      │                    │
    │      ▼      ▼                    │
    │  ┌──────┐  ┌──────┐             │
    │  │Record│  │Deny  │             │
    │  │Viewer│  │Screen│             │
    │  └──────┘  └──────┘             │
    │                                │
    └────────────────────────────────┘
         (break-glass → record viewer)
```

---

## Backend Contract (Verified from Source)

### Login

**Endpoint:** `POST /api/v2/auth/login`  
**Request:** `{ login_identifier: string, password: string, hospital_id?: UUID }`  
**Direct success response** (Pydantic `ProviderLoginResponse`):
```json
{
  "access_token": "string",
  "token_type": "bearer",
  "expires_at": "datetime",
  "provider_uid": "string",
  "hospital_id": "UUID"
}
```
**MFA required response** (Pydantic `ProviderLoginMfaRequiredResponse`):
```json
{
  "detail": "Multi-factor authentication required.",
  "mfa_token": "string"
}
```
**Failure:** 401 (invalid credentials), 429 (rate limited), 500 (MFA not configured)

### MFA Verify

**Endpoint:** `POST /api/v2/auth/mfa/verify`  
**Request:** `{ mfa_token: string, totp_code: string, provider_id?: UUID, hospital_id?: UUID }`  
**Response:** Same shape as `ProviderLoginResponse`  
**Security:** `provider_id` is NOT authoritative — identity is resolved exclusively from the server-side Redis `mfa_token`. If a caller supplies a `provider_id` that doesn't match, it's treated as an IDOR probe and rejected.

### NFC Resolve

**Endpoint:** `POST /api/v2/nfc/resolve`  
**Request:** `{ card_uid: string }` (card_uid is normalized: trimmed, uppercased)  
**Response** (Pydantic `NFCResolveResponse`):
```json
{
  "patient_id": "string",
  "canonical_patient_id": "string | null",
  "is_redirected": false
}
```
**Error codes:** 404 (card not found), 403 (card lost/revoked), 429 (rate limit: 30/provider/min), 503 (service unavailable)  
**Security:** Provider must be authenticated. Rate limited. Audited.

### Consent Request

**Endpoint:** `POST /api/v2/consent/request`  
**Request:** `{ patient_id, provider_id?, purpose, scope, access_duration_seconds, purpose_note? }`  
**Response** (Pydantic `ConsentChallengeResponsePayload`):
```json
{
  "request_id": "string",
  "status": "pending",
  "expires_in_seconds": 120,
  "challenge_nonce": "string | null"
}
```
**Security:**
- `provider_id` is derived from the authenticated Bearer session. If a caller supplies `provider_id` in the body, it must match the session identity or the request is rejected as an IDOR probe (403).
- `purpose` must be a controlled code (`treatment`, `emergency_care`, `diagnostic_review`, `follow_up`, `referral`). The backend validates against policy.
- `scope` must be a controlled category (`patient_summary`, `vitals`, `medications`, `allergies`, `lab_results`, `clinical_record`). The backend validates independently.
- `access_duration_seconds` is clamped server-side to `[300, 3600]` (5 min – 60 min).
- `purpose_note` is an optional free-text explanation that enriches the audit trail.

### Consent Status

**Endpoint:** `GET /api/v2/consent/status/{request_id}`  
**Response** (Pydantic `ConsentStatusResponsePayload`):
```json
{
  "request_id": "string",
  "status": "pending | approved | denied | expired | cancelled",
  "responded_at": "string | null"
}
```
**Security:**
- Only the provider who created the request may poll it (owner check).
- `Cache-Control: no-store` prevents browser/CDN caching of consent state.
- Returns minimal data (status + responded_at) — no consent tokens.

### Consent Cancel

**Endpoint:** `POST /api/v2/consent/request/{request_id}/cancel`  
**Response** (Pydantic `ConsentCancelResponsePayload`):
```json
{
  "request_id": "string",
  "status": "cancelled",
  "cancelled_at": "datetime"
}
```
**Security:**
- Only the requesting provider may cancel.
- Only `pending` requests can be cancelled (approved/denied/expired are terminal).
- Cancellation prevents the patient from later approving an abandoned request.
- Cancelled requests are kept briefly for audit, then expire.

### Break-Glass

**Endpoint:** `POST /api/v2/consent/break-glass/issue`  
**Request:** `{ patient_id, reason_code, free_text }`  
**Response:** `{ consent_token, expires_at }`  
**Security:**
- Rate-limited: 3 per provider per hour (server-enforced, not just UI warning).
- TTL: 15 minutes (server-enforced).
- Reason codes are controlled values (12 categories including incapacitated patients,
  unidentified patients, system failures, and "other" with mandatory review).
- Clinical justification required (minimum 20 chars; 50 for "other" reason).
- "Other" reason triggers mandatory compliance review.
- Consent token is NEVER displayed in UI — only a masked reference.
- ALPHA: Should require high-assurance session (recent MFA). Not yet implemented.
- ALPHA: Should limit scope to minimum-safety dataset. Currently grants full record.

### Session Refresh

**Endpoint:** `POST /api/v2/auth/refresh`  
**Response:** `{ access_token: string, token_type: "bearer", expires_at: datetime }`  
**Note:** Frontend does NOT yet implement automatic refresh. This is an ALPHA gap.

### Logout

**Endpoint:** `POST /api/v2/auth/logout` → 204  
**Note:** Frontend does NOT yet call this endpoint. This is an ALPHA gap — tokens remain valid on the server until they expire naturally.

---

## Runtime Validation

All backend responses are validated against Zod schemas at runtime before the frontend trusts them. See:

- `nexa-client/packages/app/schemas/authNfcSchemas.ts` (production)

Schemas include:
- `ProviderLoginSuccessSchema` / `ProviderLoginMfaRequiredSchema` / `ProviderMfaVerifySuccessSchema`
- `NfcResolveResponseSchema`
- `ConsentChallengeResponseSchema` / `ConsentStatusResponseSchema` / `ConsentCancelResponseSchema`

If the backend contract changes, the Zod validation will fail with a `SchemaValidationError` instead of silently corrupting application state.

---

## Security Invariants

1. **No `provider_id` placeholder** — the real ID comes from `useProviderAuth().providerId`.
2. **No hardcoded localhost** — API URL from `NEXT_PUBLIC_API_URL` env var via apiClient.
3. **Session token in `Authorization: Bearer`** — attached automatically by apiClient.
4. **Consent token in `X-Consent-Token` header** — passed on every data API call.
5. **Frontend lock is UX only** — the backend validates consent on every request independently.
6. **Scope-restricted tabs** — tabs not in consent scope are hidden AND their data is not fetched.
7. **Consent tokens never displayed** — only masked references shown in Access Status.
8. **Break-glass is audited** — every emergency access is permanently flagged with a red BREAK-GLASS badge.
9. **Break-glass reason codes are controlled** — 12 categories, not free-text. "Other" triggers mandatory review.
10. **Break-glass justification validated** — minimum 20 chars (50 for "other"), no whitespace-only.
11. **Patient notification is not guaranteed** — UI says "may trigger notifications" honestly.
12. **AI provenance shown with verification status** — not just confidence percentages.
13. **Role is NOT from a signed claim yet** — dashboard displays `role: 'clinician'` as a default. Production MUST extract role from verified JWT payload.
14. **Tokens are in-memory only** — they do NOT survive page reload. Production MUST use SecureStore or httpOnly cookies.
15. **Logout does not invalidate server-side** — frontend clears local state but does not call `POST /api/v2/auth/logout`. Production MUST add this.
16. **MFA tokens are single-use and TTL-bounded** — the backend stores them in Redis with expiry. The frontend cannot replay or forge them.
17. **NFC canonical_patient_id is enforced server-side** — the frontend banner is informational.
18. **NFC rate limiting is server-side** — 30 scans per provider per minute.
19. **Consent request IDOR guard** — the server derives `provider_id` from the Bearer session and rejects body mismatches.
20. **Purpose and scope are controlled values** — free-text purpose/scope is not permitted.
21. **Duration is server-clamped** — the backend enforces `[300, 3600]` regardless of client input.
22. **Consent cancel is server-side** — the cancel endpoint marks the request cancelled in Redis.
23. **Polling is owner-scoped** — only the provider who created a consent request may poll its status.
24. **Consent state is never cached** — `Cache-Control: no-store` on status endpoint.
25. **Adaptive polling backoff** — 2s → 5s → 10s. Consent revalidation every 10s.
26. **Error-aware polling** — 401→login, 403→forbidden, 404→expired, 429/5xx→retry with backoff.
27. **Navigation ≠ authorization** — navigating to `/doctor/patient-record` does not grant data access.
28. **Purpose note sent to backend** — enriches audit trail, not a substitute for purpose code.

---

## Known Gaps (ALPHA)

| Area | Status | Risk |
|------|--------|------|
| Token persistence across reload | Not implemented — in-memory only | Medium: user logged out on refresh |
| Automatic token refresh | Not implemented | Medium: 8-hour session hard-limit |
| Server-side logout | Not called from frontend | Medium: tokens valid until natural expiry |
| Role from signed claim | Hardcoded 'clinician' default | High: client-controlled role value |
| Native NFC scanning | Manual UID entry only | Low: alpha demo limitation |
| 401 redirect to login | apiClient clears JWT on 401 but no redirect | Medium: blank state instead of login prompt |
| Break-glass re-authentication | No check that MFA is recent (e.g., <10 min) | High: stolen unattended workstation can invoke break-glass |
| Break-glass minimal scope | Currently grants full record for 15 min | Medium: should limit to minimum-safety dataset |
| Per-section scope-gated API | Backend summary endpoint returns all data | High: scope restriction only frontend-enforced |
| Break-glass anomaly detection | No detection beyond rate limiting | Medium: repeated use from low-acuity settings |
| Patient notification delivery | Backend creates audit event but notification delivery not guaranteed | Medium: UI claims "may trigger" honestly |
| Consent token in X-Consent-Token | Frontend passes it; backend requires it via require_consent dependency | Medium: must verify end-to-end |
| Playwright/browser integration tests | None — all tests are structural source-code checks | High: no behavioural validation |

---

## Break-Glass Reason Codes

| Code | Label | Category |
|------|-------|----------|
| `IMMEDIATE_THREAT_TO_LIFE` | Immediate Threat to Life | Acute emergency |
| `PATIENT_INCAPACITATED` | Patient Incapacitated | Consent incapacity |
| `EMERGENCY_DIAGNOSTIC_DECISION` | Emergency Diagnostic Decision | Urgent care |
| `EMERGENCY_MEDICATION_SAFETY` | Emergency Medication Safety | Medication safety |
| `UNIDENTIFIED_PATIENT` | Unidentified Patient | Identity |
| `SURGICAL_EMERGENCY` | Surgical Emergency | Acute emergency |
| `SEVERE_BLEEDING` | Severe Bleeding | Acute emergency |
| `CARDIAC_ARREST` | Cardiac Arrest | Acute emergency |
| `ANAPHYLAXIS` | Anaphylaxis | Acute emergency |
| `ACUTE_RESPIRATORY_FAILURE` | Respiratory Failure | Acute emergency |
| `SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE` | System / Consent Service Down | System failure |
| `OTHER_CLINICALLY_JUSTIFIED_EMERGENCY` | Other Clinically Justified Emergency | Catch-all (mandatory review) |

---

## Screen Reference

| Screen Component | Route | Source File |
|---|---|---|
| DoctorLoginScreen | `/doctor/login` | `nexa-client/packages/app/features/doctor/DoctorLoginScreen.tsx` |
| DoctorDashboardScreen | `/doctor/dashboard` | `nexa-client/packages/app/features/doctor/DoctorDashboardScreen.tsx` |
| PatientSearchScreen | `/doctor/patient-search` | `nexa-client/packages/app/features/doctor/PatientSearchScreen.tsx` |
| RequestConsentScreen | `/doctor/request-consent` | `nexa-client/packages/app/features/doctor/RequestConsentScreen.tsx` |
| WaitingForApprovalScreen | `/doctor/waiting` | `nexa-client/packages/app/features/doctor/WaitingForApprovalScreen.tsx` |
| PatientRecordViewerScreen | `/doctor/patient-record` | `nexa-client/packages/app/features/doctor/PatientRecordViewerScreen.tsx` |
| EmergencyAccessScreen | `/doctor/emergency-access` | `nexa-client/packages/app/features/doctor/EmergencyAccessScreen.tsx` |

---

## Next Milestone: End-to-End Live Flow

The next milestone should NOT add more screens. It must prove one complete live flow:

```
Real provider account
→ password login
→ real MFA (TOTP)
→ doctor dashboard
→ real NFC or manual search
→ canonical patient resolution
→ consent request (controlled purpose/scope, server-clamped duration)
→ patient approval (cryptographic signature)
→ doctor record access (X-Consent-Token header + authenticated session)
→ audit-history verification
```

Run using the real frontend, a deployed or local integrated backend, PostgreSQL, Redis, actual provider and patient records, and preferably one physical Android device or NFC reader. Record every API request, response status, generated audit event, and failure path.

Additionally, add real integration tests:

```
Provider requests consent for vitals only
→ patient approves
→ vitals endpoint returns 200
→ labs endpoint returns 403
→ consent expires
→ next vitals request returns 403
→ audit contains successful and denied access events

Provider invokes break-glass
→ missing justification returns 422
→ valid emergency request returns grant
→ unauthorized provider cannot use grant
→ grant expires
→ patient notification event exists
→ compliance audit includes reason and justification
```
