# Patient App Flow Specification

**Day 14 · 2026-07-08**

---

## Overview

The patient mobile app is a Tamagui/Expo application that lets patients
manage device enrollment, approve or reject consent requests from
healthcare providers, verify approvals with biometrics, and view their
health timeline — all without direct access to raw clinical data on the
device.

---

## Screen Flow

```
┌────────────────┐
│  Patient Login │  Phone + OTP (Supabase Auth)
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Secure Device  │  NFC tap or biometric enrollment
│   Enrollment   │  Generates device keypair, registers with backend
└───────┬────────┘
        │
        ▼
┌────────────────┐
│    Device      │  Confirmation that this device is now trusted
│   Enrolled     │  Shows device fingerprint, next steps
└───────┬────────┘
        │
        │  ┌──────────────────────────────────┐
        │  │ Push notification arrives:       │
        │  │ "Dr. Mehta requests access to    │
        │  │ your lab results"                │
        │  └──────────────┬───────────────────┘
        │                 │
        ▼                 ▼
┌────────────────┐
│   Consent      │  Shows provider name, purpose, scope, expiry
│   Request      │  Patient reviews what data is being requested
└───────┬────────┘
        │
        ▼
┌────────────────┐
│  Biometric     │  Face ID / fingerprint to authorize the consent grant
│  Approval      │  Signs the consent token with device private key
└───────┬────────┘
        │
        ▼
┌────────────────┐
│   Approval     │  Success or denied. Shows consent receipt, expiry,
│   Result       │  and revocation option.
└───────┬────────┘
        │
        ▼
┌────────────────┐
│   Access       │  Log of all consent grants, provider accesses,
│   History      │  and revocations. Timestamped, auditable.
└───────┬────────┘
        │
        ▼
┌────────────────┐
│   Health       │  Timeline of clinical events (diagnoses, labs,
│   Timeline     │  prescriptions) sourced from the clinical shard.
│                │  No PII — data is de-identified + consent-gated.
└────────────────┘
```

---

## Screen Descriptions

### 1. PatientLoginScreen

**Route:** `/patient/login`
**Deep-link:** `nexacare://patient/login`

Patient enters their phone number. App sends OTP via Supabase Auth.
On verification, JWT is stored in secure storage and the app checks
whether this device is already enrolled.

| Transition | Condition |
|---|---|
| → SecureDeviceScreen | Device not yet enrolled |
| → ConsentRequestScreen | Device enrolled, pending consent exists |
| → AccessHistoryScreen | Device enrolled, no pending consent |

---

### 2. SecureDeviceScreen

**Route:** `/patient/secure-device`
**Deep-link:** `nexacare://patient/secure-device`

Guides the patient through device enrollment:
1. Prompt for biometric capability check (Face ID / fingerprint)
2. Generate an asymmetric keypair on-device (hardware-backed keystore)
3. POST public key to `POST /api/v2/devices/enroll` via `apiClient`
4. Backend registers the device and returns a `device_id`

Props:
- `phoneNumber: string` — from login
- `onEnrolled: (deviceId: string) => void` — callback on success

| Transition | Condition |
|---|---|
| → DeviceEnrolledScreen | Enrollment succeeds |

---

### 3. DeviceEnrolledScreen

**Route:** `/patient/enrolled`
**Deep-link:** `nexacare://patient/enrolled`

Confirmation screen. Shows:
- Device fingerprint (truncated hash of public key)
- Enrollment timestamp
- "You're all set" messaging
- Auto-navigates to the next screen after 3 seconds or on tap

Props:
- `deviceId: string`
- `enrolledAt: string` (ISO 8601)

| Transition | Condition |
|---|---|
| → AccessHistoryScreen | Auto after 3s or tap |

---

### 4. ConsentRequestScreen

**Route:** `/patient/consent-request`
**Deep-link:** `nexacare://patient/consent/{requestId}`

The core consent UX. Arrives via push notification or deep-link.
Displays:
- Provider name and role (e.g., "Dr. Mehta, Cardiologist")
- Purpose of access (e.g., "Review lab results")
- Data scope (e.g., "Fasting glucose, HbA1c, Blood pressure")
- Expiry (e.g., "Access expires in 24 hours")
- Two buttons: **Approve** and **Deny**

Data fetched from `GET /api/v2/consent/requests/{requestId}` via `apiClient`.

Props:
- `requestId: string`
- `providerName: string`
- `providerRole: string`
- `purpose: string`
- `scope: string[]`
- `expiresAt: string` (ISO 8601)

| Transition | Condition |
|---|---|
| → BiometricApprovalScreen | Patient taps Approve |
| → AccessHistoryScreen | Patient taps Deny |

---

### 5. BiometricApprovalScreen

**Route:** `/patient/biometric-approval`
**Deep-link:** `nexacare://patient/biometric-approval/{requestId}`

Prompts the device biometric (Face ID / fingerprint). On success:
1. Signs the consent token with the device's private key
2. Sends signed consent to `POST /api/v2/consent/approve` via `apiClient`
3. Backend verifies signature, records consent grant, returns receipt

Props:
- `requestId: string`
- `onApproved: (receipt: ConsentReceipt) => void`
- `onCancelled: () => void`

| Transition | Condition |
|---|---|
| → ApprovalResultScreen | Biometric succeeds + backend confirms |
| → ConsentRequestScreen | Biometric cancelled or fails |

---

### 6. ApprovalResultScreen

**Route:** `/patient/approval-result`
**Deep-link:** `nexacare://patient/approval-result/{requestId}`

Shows the outcome of the consent approval:
- ✅ Green check for approved, ❌ red X for denied
- Consent receipt details: grant ID, provider, scope, expiry
- "Revoke access" button (calls `DELETE /api/v2/consent/grants/{grantId}`)
- Countdown timer showing time remaining until expiry

Props:
- `requestId: string`
- `granted: boolean`
- `grantId?: string`
- `providerName: string`
- `scope: string[]`
- `expiresAt?: string` (ISO 8601)

| Transition | Condition |
|---|---|
| → AccessHistoryScreen | Tap "View History" or auto after 5s |

---

### 7. AccessHistoryScreen

**Route:** `/patient/access-history`
**Deep-link:** `nexacare://patient/access-history`

Chronological log of all consent events:
- Consent granted (who, what, when, expiry)
- Consent used (provider accessed data — audit ledger event)
- Consent revoked (patient revoked, or expired)
- Consent denied (patient rejected the request)

Data from `GET /api/v2/consent/history` via `apiClient`.

Props:
- `history: ConsentEvent[]`

| Transition | Condition |
|---|---|
| → PatientTimelineScreen | Tap "View Health Timeline" |
| → ConsentRequestScreen | New push notification arrives |

---

### 8. PatientTimelineScreen

**Route:** `/patient/timeline`
**Deep-link:** `nexacare://patient/timeline`

De-identified health timeline sourced from the clinical shard:
- Diagnosis entries
- Lab result entries (with reference range context)
- Prescription entries
- Each entry is consent-gated: data is only shown if an active consent
  grant exists for the "timeline" purpose.

Data from `GET /api/v2/patient-records/timeline` via `apiClient`.

Props:
- `timeline: TimelineEvent[]`

| Transition | Condition |
|---|---|
| → AccessHistoryScreen | Tap "Access History" |

---

## Deep-Link Scheme

**URL scheme:** `nexacare://`

| Deep-link | Screen |
|---|---|
| `nexacare://patient/login` | PatientLoginScreen |
| `nexacare://patient/secure-device` | SecureDeviceScreen |
| `nexacare://patient/enrolled` | DeviceEnrolledScreen |
| `nexacare://patient/consent/{requestId}` | ConsentRequestScreen |
| `nexacare://patient/biometric-approval/{requestId}` | BiometricApprovalScreen |
| `nexacare://patient/approval-result/{requestId}` | ApprovalResultScreen |
| `nexacare://patient/access-history` | AccessHistoryScreen |
| `nexacare://patient/timeline` | PatientTimelineScreen |

Consent deep-links are the primary entry point from push notifications.
When a provider requests access, the patient receives a push with a
`nexacare://patient/consent/{requestId}` URL. Tapping it opens the app
directly to the consent review screen, even if the app was killed.

---

## API Contracts (WS2 Coordination)

| Endpoint | Method | Used By | Payload |
|---|---|---|---|
| `/api/v2/auth/otp/send` | POST | PatientLoginScreen | `{ phone: string }` |
| `/api/v2/auth/otp/verify` | POST | PatientLoginScreen | `{ phone: string, otp: string }` → `{ jwt: string }` |
| `/api/v2/devices/enroll` | POST | SecureDeviceScreen | `{ public_key: string, device_type: string }` → `{ device_id: string }` |
| `/api/v2/consent/requests/{id}` | GET | ConsentRequestScreen | → `{ request_id, provider_name, provider_role, purpose, scope[], expires_at }` |
| `/api/v2/consent/approve` | POST | BiometricApprovalScreen | `{ request_id, signed_token: string }` → `{ grant_id, receipt }` |
| `/api/v2/consent/deny` | POST | ConsentRequestScreen | `{ request_id }` |
| `/api/v2/consent/grants/{id}` | DELETE | ApprovalResultScreen | → `{ revoked: boolean }` |
| `/api/v2/consent/history` | GET | AccessHistoryScreen | → `ConsentEvent[]` |
| `/api/v2/patient-records/timeline` | GET | PatientTimelineScreen | → `TimelineEvent[]` |

All calls go through the shared `apiClient` (WS1), which attaches the
JWT and handles 401 refresh.

---

## Navigation State

The app uses Expo Router file-based routing. Navigation state is
determined by:

1. **Auth state** — no JWT → forced to `/patient/login`
2. **Enrollment state** — no device_id → forced to `/patient/secure-device`
3. **Pending consent** — if `GET /api/v2/consent/requests/pending` returns a request, redirect to `/patient/consent-request/{requestId}`
4. **Default** — `/patient/access-history`
