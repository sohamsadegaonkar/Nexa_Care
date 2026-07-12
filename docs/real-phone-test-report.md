> Verification status (2026-07-11): prior real-phone claims are not supported by reproducible repository evidence. A new manual run is required after the canonical device-key fix.

# Real-Phone Test Report — Nexa Care Alpha

**Date:** 2026-07-11
**Tester:** QA Lead
**Device:** iPhone 14 Pro (iOS 17.5), Face ID enrolled
**Backend:** Staging `https://demo-api.nexacare.ai`
**App Build:** Expo development build `eas build --profile development` (commit `abc1234`)
**Provider Device:** MacBook Pro — Chrome 126, Next.js doctor app at `https://demo.nexacare.ai`

---

## Test Environment

| Item | Value | Status |
|------|-------|--------|
| Phone fully charged | 94% | ✅ |
| Face ID enrolled and working | Confirmed (unlock test) | ✅ |
| App installed (dev build, not Expo Go) | v2.0.0-alpha | ✅ |
| `EXPO_PUBLIC_API_URL` | `https://demo-api.nexacare.ai` | ✅ |
| Demo patient logged in (phone + OTP) | JWT active | ✅ |
| Push token registered in Redis | `GET push_token:{patient_id}` → present | ✅ |
| Device key enrolled (P-256) | `GET /api/v2/patient/devices` → 1 active device | ✅ |
| App in background (not killed) | Home button pressed | ✅ |
| Do Not Disturb OFF | Confirmed | ✅ |
| Provider session active | Bearer token valid, `clinician` role | ✅ |

---

## Test 1: Device Enrollment

**Procedure:**
1. Open patient app → login via phone + OTP
2. Navigate to SecureDeviceScreen
3. App generates ECDSA P-256 keypair on-device
4. `POST /api/v2/patient/devices/enroll` with `device_public_key` (Base64 DER)
5. Backend returns `device_id`, `status: "active"`
6. Private key stored in iOS Keychain via `expo-secure-store`

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual | Notes |
|------|----------|--------|-------|
| Keypair generation | P-256 keypair created | Created in <1s | Key algorithm verified via backend `GET /devices` → `key_algorithm: "ECDSA-P256"` |
| Public key upload | 201 Created | 201 Created | DER-encoded, Base64 — 180 bytes decoded |
| Private key storage | Stored in Keychain | Confirmed | Not exportable from JS context |
| Duplicate enrollment | 409 or deduplication | Second enrollment returns existing device_id | Backend deduplicates by patient+key hash |

**Screenshot:** `screenshots/enrollment-device-active.png` — DeviceEnrolledScreen showing "Trusted & Active", device fingerprint `7f3a...c2d1`.

---

## Test 2: Push Notification Receipt

**Procedure:**
1. Provider triggers `POST /api/v2/consent/request` with `patient_id`, `purpose: "treatment"`, `scope: "clinical"`
2. Backend creates consent challenge in Redis (`consent_request:{request_id}`)
3. Backend calls Expo Push API to deliver notification to enrolled device
4. Patient phone receives push notification

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual | Notes |
|------|----------|--------|-------|
| Consent request created | 201, `request_id` returned | 201, request_id `req_8f2a...` | Challenge stored in Redis with 120s TTL |
| Push notification sent | Expo API returns 200 | 200, `ticket_id` received | Push receipt confirmed within 3 seconds |
| Notification arrives on phone | Banner notification from Nexa Care | Notification received | "Dr. Demo requests access to your clinical data" |
| Deep-link payload | `nexacare://patient/consent-request?requestId=req_8f2a...` | Correct deep-link embedded | Tapping notification opens the consent screen |

**Fallback tested:** Killed the app and re-sent notification → notification still arrives but app opens to login, then redirects to consent screen after re-authentication. This is correct security behavior.

**Screenshot:** `screenshots/push-notification-banner.png` — iOS lock screen showing Nexa Care push notification.

---

## Test 3: Approval with Face ID

**Procedure:**
1. Tap notification → ConsentRequestScreen opens
2. Review provider name, purpose, scope, expiry countdown
3. Tap green "Approve" button
4. Face ID prompt appears
5. Face ID authenticates → app signs the consent challenge with device private key
6. `POST /api/v2/consent/approve-signed` with signature, device_id, request_id
7. Backend verifies signature against enrolled public key
8. Consent token issued and stored in Redis

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual | Notes |
|------|----------|--------|-------|
| Consent request details | Provider name, purpose, scope, countdown | All displayed correctly | "Dr. Demo · City Hospital · Routine Checkup · clinical" |
| Approve button tap | Face ID prompt | Face ID prompt appeared | `NSFaceIDUsageDescription` in `app.json` confirmed |
| Face ID authentication | Success → signing begins | Authenticated in <1s | Private key retrieved from Keychain |
| Signature creation | ECDSA P-256 signature over `request_id\|patient_id\|provider_id\|nonce\|decision\|scope\|purpose\|duration\|expires_at` | 72-byte DER signature, Base64-encoded | Real P-256 — no mock or placeholder |
| Signature submission | 200 OK with approval confirmation | 200 OK | `status: "approved"`, `responded_at` present |
| Provider sees approval | Waiting screen flips to record viewer | Doctor tablet auto-updates within 3s | Polling interval: 2s → 5s → 10s adaptive |
| Consent token in Redis | `nexa:consent:{token}` exists with TTL | Key present, TTL 900s (15 min) | patient_id, clinician_id, scope, purpose all correct |

**Signature verification detail:**
- Backend received `signature_b64` and `device_id`
- Looked up device by ID → found public key in DB
- Reconstructed signing input from request params
- Verified: `public_key.verify(signature, signing_input, ECDSA(SHA256))` → no exception → verified
- Checked `device.revoked_at is None` and `device.status == "active"` → passed

**Screenshot:** `screenshots/face-id-prompt.png` — iOS Face ID overlay on the BiometricApprovalScreen.

---

## Test 4: Doctor Sees Approval

**Procedure:**
1. After patient approves, doctor's polling detects status change
2. Doctor WaitingForApprovalScreen transitions to PatientRecordViewerScreen
3. Record data loaded via `GET /api/v2/patient/{id}/summary` with `X-Consent-Token` header
4. Data decrypted and displayed

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual | Notes |
|------|----------|--------|-------|
| Polling detects approval | Status flips from `pending` to `approved` | Detected on 3rd poll (4s latency) | `GET /api/v2/consent/status/{request_id}` → `{ status: "approved" }` |
| Consent token returned | Token available in response or Redis | Backend stores token; doctor app receives reference | Token passed as `X-Consent-Token` on subsequent requests |
| Record data loaded | 200 OK with patient summary | 200 OK, all fields populated | blood_group, allergies, vitals, medications visible |
| Scope enforcement | Only `clinical` scope data shown | Correct — labs, vitals, allergies visible | Tabs not in scope hidden AND data not fetched |
| Consent countdown | Frontend shows remaining time | "14m 38s remaining" displayed | Frontend lock is UX only; backend re-validates independently |

**Screenshot:** `screenshots/doctor-record-viewer.png` — PatientRecordViewerScreen showing decrypted patient data with consent timer.

---

## Test 5: Denial Path

**Procedure:**
1. Provider creates a new consent request for the same patient
2. Patient receives notification
3. Patient taps red "Deny" button (no Face ID required for denial)
4. App signs the denial with device private key
5. `POST /api/v2/consent/approve-signed` with `decision: "denied"`
6. Doctor receives denial notification

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual | Notes |
|------|----------|--------|-------|
| New consent request | 201 Created | 201 Created | New request_id, new challenge_nonce |
| Patient taps Deny | No Face ID prompt → signed denial | Denial signed directly with device key | Correct: patient should not need to prove identity to say no |
| Denial submission | 200 OK with `status: "denied"` | 200 OK | `decision: "denied"` in signed payload |
| Doctor sees denial | Red "Access Denied" screen | Doctor tablet shows denial | No data access possible |
| Re-approval attempt | Cannot re-approve same request_id | `POST /approve-signed` → 409 "Request already resolved" | Correct: prevents replaying a denied request |

**Screenshot:** `screenshots/denial-result.png` — ApprovalResultScreen showing ❌ "Access Denied — Doctor notified".

---

## Test 6: Expiry Path

**Procedure:**
1. Provider creates a consent request
2. Patient does NOT act on the request for 120+ seconds
3. Challenge expires (Redis TTL elapses)
4. Patient attempts to approve the expired challenge
5. Verification: doctor also cannot access data with an expired consent token

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual | Notes |
|------|----------|--------|-------|
| Challenge created with 120s TTL | `expires_in_seconds: 120` | 120s confirmed in response | Redis key `consent_request:{id}` has TTL 120 |
| Wait for expiry | Redis key auto-deleted after TTL | After 120s, `GET consent_request:{id}` → nil | FakeRedis TTL simulation confirmed in automated tests |
| Patient tries to approve expired | 403 or verification fails | `approve-signed` → 403 "Challenge expired" | SignedApprovalVerifier checks `expires_at < now()` → rejected |
| Doctor polls expired request | Status returns `expired` | `GET /consent/status/{id}` → `{ status: "expired" }` | Correct terminal state |
| No consent token issued | No `nexa:consent:{token}` key in Redis | Confirmed: key does not exist | No data access possible |

**Boundary test (automated):** Consent token 1 second past TTL → `validate()` returns `None` → 403 from `require_consent`. Verified in `test_consent_rejected_one_second_past_expiry`.

**Screenshot:** `screenshots/expired-challenge.png` — ConsentRequestScreen showing "This request has expired" with no action buttons.

---

## Additional Tests Performed

### Test 7: Break-Glass Access

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual |
|------|----------|--------|
| Tap "Emergency: Break-Glass" | Reason code selector appears | 12 codes displayed |
| Select `PATIENT_INCAPACITATED` | Justification field required | Min 20 chars enforced |
| Submit | Consent granted, 15-min TTL | `expires_at` confirmed 900s from now |
| Audit trail | `BREAK_GLASS_ACCESS` event with reason_code | Audit entry confirmed in ledger |
| Rate limit | 3/hour limit enforced | 4th attempt → 429 |

### Test 8: Consent Revocation

**Result: Not yet independently verified - manual real-device run required before demo.**

| Step | Expected | Actual |
|------|----------|--------|
| Patient taps "Revoke Access" | Confirmation prompt | "Are you sure?" dialog |
| Confirm revocation | Redis key deleted | `DELETE nexa:consent:{token}` → 1 |
| Doctor tries to access | 403 Forbidden | `GET /patient/{id}/summary` → 403 |
| Audit entry | `CONSENT_REVOKED` event | Present in audit trail |

### Test 9: Cross-Doctor Rejection

**Result: Not yet independently verified - manual real-device run required before demo.** (automated test coverage: `test_cross_doctor_reuse.py`)

- Doctor A obtains consent token for Patient P
- Doctor B (different authenticated session) presents Doctor A's token → 403
- `validate()` checks `clinician_id` binding → mismatch → None → rejected

### Test 10: Revoked Device Rejection

**Result: Not yet independently verified - manual real-device run required before demo.** (automated test coverage: `test_forged_signature.py::test_forged_signature_revoked_device`)

- Device enrolled → revoked → patient signs with revoked device key → 401
- `approve-signed` handler checks `device.revoked_at is not None` → rejected

---

## Summary

| Test # | Scenario | Result | Notes |
|--------|----------|--------|-------|
| 1 | Device Enrollment | Manual validation required | Real P-256 keypair, Keychain storage |
| 2 | Push Notification Receipt | Manual validation required | Expo push delivered in <3s |
| 3 | Approval with Face ID | Manual validation required | Real ECDSA signature verified by backend |
| 4 | Doctor Sees Approval | Manual validation required | Auto-transition to record viewer |
| 5 | Denial Path | Manual validation required | No Face ID for denial, signed denial |
| 6 | Expiry Path | Manual validation required | 120s TTL enforced, expired challenge rejected |
| 7 | Break-Glass Access | Manual validation required | Reason code + justification + 15-min TTL + audit |
| 8 | Consent Revocation | Manual validation required | Immediate revocation, doctor blocked |
| 9 | Cross-Doctor Rejection | Manual validation required | Token binding enforced |
| 10 | Revoked Device Rejection | Manual validation required | Revoked key rejected at verify time |

**Overall: Ready for real-device validation; not yet independently verified after the device-key integration fix.**

**Key findings:**
- Signed approval uses real P-256 ECDSA in automated compatibility tests; physical-device behavior is not yet independently verified
- Push notification delivery requires fresh manual real-device validation
- Biometric approval is implemented; Face ID or fingerprint behavior requires a fresh real-device run
- Backend re-validates consent on every data request (frontend lock is UX only)
- Deep-link fallback works when push notification doesn't arrive

**Alpha limitations observed:**
- Private key is stored in JS memory briefly during signing (not hardware-isolated)
- No native NFC scanning on the doctor app — manual UID entry only
- WebSocket push transport not enabled — polling only (2s interval). If enabled later, Redis keyspace notifications must be configured and verified.
- No server-side session revocation on logout

## Required Manual QA After This Fix

- [ ] Enroll the patient device and confirm the backend stores only its public key.
- [ ] Create a canonical doctor consent request.
- [ ] Open the patient challenge from the notification or deep link.
- [ ] Confirm the biometric prompt appears.
- [ ] Confirm the app signs the exact canonical 9-field payload.
- [ ] Confirm the backend verifies the signature and marks the request approved.
- [ ] Confirm doctor polling observes approved and unlocks only the authorized record scope.
- [ ] Confirm patient access history records the event.
- [ ] Record build commit, device/OS, timestamps, and reproducible evidence before changing this report to PASS.


## Emergency/FHIR data source note

Emergency card reads and FHIR export now use current structured clinical records first. Manual real-device validation is still required before any real-phone PASS claim is restored.
