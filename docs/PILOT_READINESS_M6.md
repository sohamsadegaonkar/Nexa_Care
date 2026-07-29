# Milestone 6.0 Pilot Readiness Reconciliation

**Candidate base:** `e144be22a883e5b21d0c9cee0f7c1c5ec763b783`  
**Pilot scope:** one hospital department, three doctors, and 20–50 synthetic patients  
**Evidence boundary:** repository and automated evidence only. Physical-device results remain pending.

## Classification

| Area | Classification | Repository evidence | Qualification evidence |
|---|---|---|---|
| Patient device-enrollment entry point | `FIXED_IN_CODE` | `PatientLoginScreen.tsx` stores the one-time enrollment token and calls `ensureCurrentDeviceEnrollment()` after OTP verification. `BiometricApprovalScreen.tsx` routes an unenrolled installation to `/patient/secure-device`; Expo declares that route. | `tests/test_patient_screens.py`; `currentDeviceEnrollment.test.ts` |
| Real P-256 signed approval | `IMPLEMENTED_NOT_LIVE_VERIFIED` | `deviceKeys.ts`, `consentSigning.ts`, `BiometricApprovalScreen.tsx`, `signed_approval_verifier.py`, and `/api/v2/consent/approve-signed` implement the canonical nine-field P-256 contract. Only the public key is enrolled. | `tests/test_signed_approval.py`; `tests/test_signed_approval_contract.py`; `tests/security/test_forged_signature.py`; physical Android proof pending |
| Push receipt and deep-link handling | `IMPLEMENTED_NOT_LIVE_VERIFIED` | `pushNotifications.ts` handles foreground receipt, notification taps, cold start, authentication deferral, and request-ID-only navigation. Expo scheme is declared in `apps/expo/app.json`. | `pushNotifications.test.ts`; `tests/test_push_notification.py`; real Expo delivery pending |
| Provider identity and role source | `FIXED_IN_CODE` | `/api/v2/auth/web/session` returns identity, hospital, and roles from the authenticated server-side affiliation. `ProviderAuthContext.tsx` renders those values and derives only a display-primary role from the returned role set. | `DoctorLoginScreen.test.tsx`; `ProviderRouteGuard.test.tsx`; `tests/test_provider_auth_service.py` |
| Server-side logout and revocation | `FIXED_IN_CODE` | `/api/v2/auth/web/logout` and `/api/v2/auth/logout` delete the current Redis session. Milestone 6 makes session-store failure return safe `503 PROVIDER_SESSION_REVOCATION_UNAVAILABLE` instead of falsely reporting logout success. | `tests/test_provider_auth_service.py`; live Redis logout proof pending |
| Authenticated 401 and expired-session handling | `FIXED_IN_CODE` | `apiClient.ts` emits `REAUTH_REQUIRED`; `ProviderAuthContext.tsx` now registers the handler and clears provider state, capabilities, access grants, and adjudication workflows. `ProviderRouteGuard.tsx` redirects unauthenticated users. Patient expiry redirects in Expo `_layout.tsx`. | `apiClient.test.ts`; `DoctorLoginScreen.test.tsx`; `ProviderRouteGuard.test.tsx`; `patientAuthSession.test.ts` |
| Break-glass reason, scope, MFA freshness, and audit | `FIXED_IN_CODE` | `BreakGlassReasonCode`, `approved_break_glass_scope()`, recent-MFA enforcement, bounded justification, emergency-only capability, dedicated minimal summary endpoint, expiry/revoke, and audit are implemented in `break_glass_policy.py`, `consent_routes.py`, and `patient_routes.py`. | `tests/test_break_glass_policy.py`; `tests/test_emergency_summary.py`; `tests/test_break_glass_revoke.py`; `tests/test_record_viewer_and_emergency.py` |
| Consent expiry and revocation | `IMPLEMENTED_NOT_LIVE_VERIFIED` | Consent capabilities are Redis TTL-bound and patient revocation deletes the capability and updates durable consent state. Frontends expose terminal expiry/revocation states. | `tests/security/test_consent_expiry.py`; `tests/test_patient_consent_revoke.py`; `tests/integration/test_consent_revoke_integration.py`; live flow pending |
| Cross-provider capability rejection | `FIXED_IN_CODE` | Provider, hospital, patient, purpose, operation, and scope are reconstructed server-side at access time. | `tests/security/test_cross_doctor_reuse.py`; `tests/security/test_unauthorized_access.py`; `tests/test_routine_consent.py` |
| Revoked-device rejection | `FIXED_IN_CODE` | Signed approval resolves an active patient-owned device key and rejects revoked or wrong-patient keys. | `tests/test_signed_approval.py`; `tests/security/test_forged_signature.py` |
| Patient audit-history visibility | `IMPLEMENTED_NOT_LIVE_VERIFIED` | `/api/v2/patient/me/access-history` projects canonical successful provider access and break-glass events. Expo links to `AccessHistoryScreen.tsx`. | `tests/test_access_history.py`; `AccessHistoryScreen.test.tsx`; physical refresh proof pending |
| Automated browser E2E | `ACCEPTED_PILOT_LIMITATION` | Playwright exists at `apps/next/e2e`, but covers generic hydration/navigation rather than the authenticated clinical-access journey. Vitest exercises the doctor flow at component/transport level. | No automated browser proof of provider → patient-device → provider completion |
| Document-processing separation | `FIXED_IN_CODE` | Ordinary consent and emergency routes do not invoke document extraction. Pipeline and SOURCE_ONLY adjudication remain under dedicated routes, operations, screens, and authorization gates. | `tests/test_frontend_integration_guardrails.py`; pipeline/adjudication contract and authorization suites |

## Confirmed pilot blockers

1. Physical Android evidence is absent for enrollment, Expo push delivery, deep linking, biometric prompt, P-256 signing, denial, expiry, revocation, and audit-history refresh.
2. The deployed pilot candidate has not yet been proven against its final frontend/backend origins and physical-device network path.
3. The JS signing implementation stores the private key encrypted by platform SecureStore but briefly materializes it in JS memory. This is not StrongBox-backed non-exportable signing and requires explicit security-owner acceptance for this constrained synthetic-data pilot.

## Stale documentation

- `docs/known-issues.md` ISS-01, ISS-03, ISS-04, ISS-07, ISS-08, ISS-09, and ISS-11 describe defects already corrected in code.
- `docs/doctor-app-flow.md` still claims hardcoded provider roles, absent server logout, absent 401 navigation, full-record break-glass, and no browser tests.
- `docs/test-strategy.md` describes security tests as release-critical `xfail` skeletons and Playwright as wholly future work; current tests are active and a small Playwright smoke suite exists.
- `docs/real-phone-test-report.md` contains historical device/deployment claims that are explicitly non-reproducible and are not accepted as current evidence.
- `docs/demo-env-config.md` names obsolete example demo domains and a setup script as if they were current live qualification evidence. Configuration must be resolved from the actual controlled-pilot environment without printing secrets.

## Accepted pilot limitations

- Manual patient identifier entry is acceptable; native NFC is not required.
- Polling is the supported consent-status transport; WebSocket remains optional.
- Automated browser E2E does not span the physical-device boundary.
- Cloud KMS, complete FHIR conformance, automated key rotation, and hardware-isolated native signing are outside this implementation pass. Hardware-isolated signing still needs explicit pilot risk acceptance.
- Document processing and SOURCE_ONLY adjudication remain separate from routine access and are not automatically committed.

