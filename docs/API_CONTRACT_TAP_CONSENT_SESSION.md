# Nexa Care v1.0 — Tap → Consent → Session API Contract

## Overview
This document defines the API flow from NFC card tap to clinical session creation.

---

## 1. NFC Card Tap (Layer 1 – Card Authentication)

**Endpoint**: `POST /api/v2/nfc/resolve`

**Request**:
```json
{
  "card_uid": "string"
}
```

**Success Response** (200):
```json
{
  "patient_uuid": "uuid",
  "authentication_grant": "string",
  "expires_in": 30
}
```

---

## 2. Consent Assurance Evaluation (Layer 2)

After successful card authentication, the system evaluates the patient’s `consent_assurance_policy`.

### 2.1 Standard Flow
- Automatically proceeds if policy is `STANDARD`
- Issues 30-minute consent token

### 2.2 Push Approval Flow
- Sends real-time notification to patient app
- Endpoint for patient response:
  `POST /api/v2/consent/push/respond`

### 2.3 Biometric Confirmation
- Requires mobile app confirmation
- Endpoint: `POST /api/v2/consent/biometric/verify`

---

## 3. Issue Consent Token

**Endpoint**: `POST /api/v2/consent/routine/issue`

**Request**:
```json
{
  "patient_uuid": "uuid",
  "hospital_id": "string",
  "clinician_id": "string",
  "purpose": "ROUTINE_CHECKUP"
}
```

**Response** (201):
```json
{
  "consent_token": "string",
  "expires_at": "2026-07-04T10:30:00Z",
  "consent_assurance": "standard"
}
```

---

## 4. Break-Glass Emergency Access

**Endpoint**: `POST /api/v2/consent/break-glass/issue`

**Request**:
```json
{
  "patient_uuid": "uuid",
  "hospital_id": "string",
  "clinician_id": "string",
  "reason": "UNCONSCIOUS",
  "justification": "Patient is unconscious and requires immediate care"
}
```

**Response**:
```json
{
  "consent_token": "string",
  "consent_assurance": "bypassed_emergency",
  "expires_at": "2026-07-04T09:45:00Z"
}
```

---

## 5. Fetch Patient Record (with Consent)

**Endpoint**: `GET /api/v2/patient/{patient_uuid}/record`

**Headers**:
```
X-Consent-Token: <token>
X-Consent-Purpose: ROUTINE_CHECKUP
```

**Response**:
```json
{
  "demographics": { ... },
  "clinical": { ... }
}
```

---

## 6. Session Revalidation (Background)

Every 2–5 minutes the terminal should call:

`POST /api/v2/session/validate`

```json
{
  "consent_token": "string"
}
```

---

## Error Codes
- `CONSENT_ASSURANCE_FAILED`
- `PUSH_TIMEOUT`
- `CARD_REVOKED`
- `EMERGENCY_BYPASS_DENIED`
- `SESSION_EXPIRED`

---

**Status**: Ready for implementation in FastAPI.