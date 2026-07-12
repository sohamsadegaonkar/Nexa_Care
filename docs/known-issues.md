# Known Issues — Nexa Care Alpha

**Date:** 2026-07-11
**Version:** v2.0.0-alpha
**Severity Scale:** P0 = blocking/demo-stopper, P1 = high/ship-blocker, P2 = medium/sprint-backlog, P3 = low/backlog

---

## P0 — Blocking / Demo-Stopper

*No P0 issues at this time.*

---

## P1 — High Priority (Ship-Blocker)

### ISS-01: Device Key Enrollment Has No UI Entry Point

| Field | Detail |
|-------|--------|
| **Severity** | P1 |
| **Category** | Missing Feature |
| **Owner** | WS2 (Patient App) |
| **Description** | `/api/v2/push/register-device-key` is deprecated legacy biometric-registry compatibility. Canonical enrollment uses `patient_device_keys` through `/api/v2/patient/devices/enroll` and the active device-key screens/services; the legacy frontend `registerDeviceKey()` function intentionally throws to prevent accidental use. |
| **Impact** | No real patient will have a usable signing key until a screen calls `enrollDeviceKey()`. Demo must use a pre-seeded test device or manual API call to enroll. |
| **Mitigation** | For demo: seed a test device via direct API call before the demo. For production: wire SecureDeviceScreen to call `enrollDeviceKey()` on first login. |
| **Evidence** | `docs/CURRENT-STATE.md §5 item 4` |

### ISS-02: Signing Key Is Not Hardware-Isolated

| Field | Detail |
|-------|--------|
| **Severity** | P1 |
| **Category** | Security Limitation |
| **Owner** | WS2 (Patient App) |
| **Description** | `signPushChallenge()` produces a real ECDSA-P256 signature, but the private key is briefly resident in JS memory during signing rather than never leaving the Secure Enclave/StrongBox. The key is encrypted at rest by `expo-secure-store` and gated by a biometric prompt, but this is weaker than true hardware-isolated signing. |
| **Impact** | A compromised JS runtime could extract the private key during the signing window. Acceptable for alpha/academic demo; not acceptable for hospital pilot. |
| **Mitigation** | For hospital pilot: implement a native module that generates and uses the key exclusively within the Secure Enclave (iOS) or StrongBox (Android). The architecture is designed for this upgrade path. |
| **Evidence** | `docs/CURRENT-STATE.md §5 item 5` |

### ISS-03: No Server-Side Session Revocation

| Field | Detail |
|-------|--------|
| **Severity** | P1 |
| **Category** | Security Gap |
| **Owner** | WS1 (Auth) |
| **Description** | Frontend `POST /api/v2/auth/logout` endpoint exists but the patient app does not call it. Doctor app clears local state but tokens remain valid on the server until natural expiry (8 hours). A stolen JWT is valid for the full session duration. |
| **Impact** | Device theft or XSS token extraction gives 8 hours of unauthorized access with no revocation mechanism. |
| **Mitigation** | Add server-side session blacklist in Redis. Frontend must call `/auth/logout` on explicit logout. Shorter session TTL (4 hours) would reduce the window. |
| **Evidence** | `docs/doctor-app-flow.md § Known Gaps` |

### ISS-04: Role Not Extracted from Signed JWT Claim

| Field | Detail |
|-------|--------|
| **Severity** | P1 |
| **Category** | Authorization Gap |
| **Owner** | WS1 (Auth) |
| **Description** | Dashboard displays `role: 'clinician'` as a hardcoded default. The role is not verified from a signed JWT payload. A client-side role value could be tampered with to gain admin access. |
| **Impact** | Role-based access control (`require_role("admin")`) depends on server-side session resolution, but the frontend trust of role is client-controlled. |
| **Mitigation** | Extract role from verified JWT payload server-side. Frontend should only display role from the server response, not from local state. |
| **Evidence** | `docs/doctor-app-flow.md § Known Gaps` |

---

## P2 — Medium Priority (Sprint Backlog)

### ISS-05: WebSocket Push Transport Feature-Flagged

| Field | Detail |
|-------|--------|
| **Severity** | P2 |
| **Category** | Reliability |
| **Owner** | WS2 (Push) |
| **Description** | The push status transport defaults to HTTP polling (2s interval). WebSocket transport is feature-flagged and requires more load testing. Polling adds latency to the doctor's "waiting for approval" screen. |
| **Impact** | Doctor sees up to 2s delay before approval status updates. Not ideal for demo flow but acceptable. |
| **Mitigation** | Current: adaptive polling (2s → 5s → 10s) reduces overhead. Future: enable WebSocket after load testing. |
| **Evidence** | `docs/CURRENT-STATE.md §5 item 1` |

### ISS-06: Token Persistence Across Page Reload Not Implemented

| Field | Detail |
|-------|--------|
| **Severity** | P2 |
| **Category** | UX Gap |
| **Owner** | WS1 (Auth) |
| **Description** | Auth tokens are stored in-memory only. Page refresh loses the session, forcing re-login. Production should use `httpOnly` Secure cookies or encrypted `SecureStore`. |
| **Impact** | Doctor or patient must re-login after any page refresh. Annoying but not a security issue. |
| **Mitigation** | For alpha: inform demo presenters not to refresh. For production: implement `httpOnly` Secure cookie storage. |
| **Evidence** | `docs/doctor-app-flow.md § Known Gaps` |

### ISS-07: 401 Response Does Not Redirect to Login

| Field | Detail |
|-------|--------|
| **Severity** | P2 |
| **Category** | UX Gap |
| **Owner** | WS1 (Frontend) |
| **Description** | `apiClient` clears the JWT on 401 but does not redirect to the login screen. The user sees a blank state instead of a login prompt. |
| **Impact** | After session expiry, user must manually navigate to login. |
| **Mitigation** | Add 401 interceptor in `apiClient` that redirects to `/doctor/login` or `/patient/login`. |
| **Evidence** | `docs/doctor-app-flow.md § Known Gaps` |

### ISS-08: Break-Glass Reason Code Not Validated Server-Side

| Field | Detail |
|-------|--------|
| **Severity** | P2 |
| **Category** | Security Gap |
| **Owner** | WS6 (Emergency Access) |
| **Description** | Frontend enforces a controlled selector for break-glass reason codes (12 categories). Backend accepts any string. A compromised client could submit an arbitrary reason code. |
| **Impact** | Compliance audit trail could contain invalid reason codes. |
| **Mitigation** | Add server-side validation against the 12-code allow-list. Reject with 422 if reason_code is not in the set. |
| **Evidence** | `docs/threat-model.md § Assumptions item 6`, `docs/API-CONTRACT-DEVIATIONS.md § Critical Gaps item 5` |

### ISS-09: Break-Glass Grants Full Record Scope

| Field | Detail |
|-------|--------|
| **Severity** | P2 |
| **Category** | Over-Privilege |
| **Owner** | WS6 (Emergency Access) |
| **Description** | Break-glass currently grants full record access for 15 minutes. Best practice is to limit scope to the minimum-safety dataset needed for the emergency. |
| **Impact** | Broader access than necessary during emergencies. |
| **Mitigation** | Implement minimal-safety scope definition per reason code. Example: `CARDIAC_ARREST` → vitals + medications + allergies only. |
| **Evidence** | `docs/doctor-app-flow.md § Known Gaps` |

### ISS-10: Fail-Open Rate Limiter Policy

| Field | Detail |
|-------|--------|
| **Severity** | P2 |
| **Category** | Availability vs Security |
| **Owner** | WS1 (Infrastructure) |
| **Description** | Rate limiters fail open when Redis is unavailable. This is safe for availability but creates a brute-force vector during infrastructure failure. |
| **Impact** | If Redis goes down, there are no rate limits on any endpoint. |
| **Mitigation** | Implement fail-closed option for critical endpoints (consent, break-glass). Add circuit-breaker pattern with degraded-mode limits. |
| **Evidence** | `docs/CURRENT-STATE.md §5 item 1` |

### ISS-11: Pre-Existing Test Suite Failures

| Field | Detail |
|-------|--------|
| **Severity** | P2 |
| **Category** | Test Hygiene |
| **Owner** | QA |
| **Description** | 14 test failures in the broader suite (not in security, integration, or pipeline QA tests). Causes: async test compatibility, stale alpha smoke tests, and `test_route_registration.py` which doesn't traverse `_IncludedRouter` objects. |
| **Impact** | CI noise. Does not affect security or integration coverage. |
| **Mitigation** | Fix async test compatibility. Update `test_route_registration.py` to traverse `_IncludedRouter`. Remove stale alpha smoke tests. |
| **Evidence** | `pytest tests/ -q` → 14 failed, 1995 passed, 10 xfailed |

---

## P3 — Low Priority (Backlog)

### ISS-12: FHIR R4 Coverage Incomplete

| Field | Detail |
|-------|--------|
| **Severity** | P3 |
| **Category** | Feature Incomplete |
| **Owner** | WS3 (Patient Records) |
| **Description** | FHIR export in `fhir_converter.py` only maps a subset of fields. Full R4 validation deferred to Sprint 3. |
| **Impact** | FHIR consumers get partial data. Not needed for alpha demo. |
| **Mitigation** | Complete R4 mapping in Sprint 3. |

### ISS-13: Cloud KMS Provider Is a Stub

| Field | Detail |
|-------|--------|
| **Severity** | P3 |
| **Category** | Infrastructure |
| **Owner** | WS3 (Crypto) |
| **Description** | `KMSProvider` (AWS KMS/Azure Key Vault) is a stub. Production uses `LocalEnvelopeProvider` with system KEK. |
| **Impact** | Alpha deployment is secure with local KEK. Cloud KMS needed for production multi-tenant deployment. |
| **Mitigation** | Implement cloud KMS provider before hospital pilot. |

### ISS-14: No Automated Key Rotation Schedule

| Field | Detail |
|-------|--------|
| **Severity** | P3 |
| **Category** | Infrastructure |
| **Owner** | WS3 (Crypto) |
| **Description** | Key rotation infrastructure is built (DEK versioning) but no automated schedule exists. |
| **Impact** | Keys remain static until manually rotated. |
| **Mitigation** | Add automated rotation cron job or scheduled Lambda. |

### ISS-15: Native NFC Scanning Not Implemented

| Field | Detail |
|-------|--------|
| **Severity** | P3 |
| **Category** | Feature Incomplete |
| **Owner** | WS1 (Frontend) |
| **Description** | Doctor app uses manual UID entry instead of native NFC scanning. |
| **Impact** | Demo must manually type patient UID. Functional but less impressive. |
| **Mitigation** | Implement NFC scanning via `expo-nfc` or custom native module. |

### ISS-16: API Contract Field Name Mismatches

| Field | Detail |
|-------|--------|
| **Severity** | P3 |
| **Category** | Contract Deviation |
| **Owner** | WS2 |
| **Description** | Device enrollment uses `device_public_key` and `device_label` instead of `public_key` and `device_name` as specified in the API contract. |
| **Impact** | Frontend integration may break if using contract-specified names. |
| **Mitigation** | Add Pydantic aliases or update contract to match implementation. |
| **Evidence** | `docs/API-CONTRACT-DEVIATIONS.md D-01, D-02` |

---

## Summary

| Severity | Count | Blocking Demo? |
|----------|-------|----------------|
| P0 | 0 | No |
| P1 | 4 | No (all mitigated for alpha demo) |
| P2 | 7 | No |
| P3 | 5 | No |
| **Total** | **16** | |

**All P1 issues have documented mitigations for the alpha demo. No issue blocks the demo flow.**
