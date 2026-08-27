# Nexa Care v1.0 — OpenAPI Documentation (Consent & Merge)

## Base URL
`http://localhost:8000`

---

## Endpoints

### 1. Patient Discovery and Consent Request
The retired direct routine issuers (`/api/v2/consent/routine/issue` and
`/api/v2/consent/grant`) are not callable APIs and return `410
ROUTINE_DIRECT_ISSUANCE_RETIRED`.

Use the consent-first flow instead:

1. `POST /api/v2/nfc/resolve` (or the authenticated patient-discovery endpoint)
   returns only an expiring opaque `discovery_handle`.
2. `POST /api/v2/consent/request` submits that handle and the requested scope.
3. The patient approves the challenge; the provider claims the resulting
   capability before accessing a record.

---

### 2. Issue Break-Glass Consent
**POST** `/api/v2/consent/break-glass/issue`

**Request Body**:
```json
{
  "patient_uuid": "uuid",
  "hospital_id": "string",
  "clinician_id": "string",
  "reason": "string",
  "justification": "string"
}
```

**Response** (201):
```json
{
  "consent_token": "string",
  "consent_assurance": "bypassed_emergency",
  "expires_at": "datetime"
}
```

---

### 3. Validate Consent Token
**GET** `/api/v2/consent/validate?consent_token=xxx&patient_uuid=xxx`

**Response** (200):
```json
{
  "patient_uuid": "uuid",
  "purpose": "string",
  "consent_assurance": "string",
  "expires_at": "datetime"
}
```

---

### 4. NFC Card Resolution (opaque discovery)
**POST** `/api/v2/nfc/resolve`

**Request**:
```json
{ "card_uid": "string" }
```

**Response** (200):
```json
{
  "discovery_handle": "opaque string",
  "expires_at": "datetime"
}
```

Patient UUIDs, redirect chains, and record data are never returned by this
pre-consent endpoint. The handle is passed to `/api/v2/consent/request`.

---

### 5. Patient Merge (Tombstone)
**POST** `/api/v2/patient/merge`

**Request Body**:
```json
{
  "old_patient_uuid": "uuid",
  "canonical_patient_uuid": "uuid",
  "reason": "string",
  "evidence": {}
}
```

**Response** (201):
```json
{
  "message": "Patient merged successfully",
  "tombstone_id": "uuid",
  "canonical_patient_uuid": "uuid"
}
```

---

## Authentication
All endpoints require provider authentication via:
- `Authorization: Bearer <token>`
- Or HTTP Basic with `provider_credential`

---

**Generated**: 2026-07-05  
**Version**: Nexa Care v1.0 Final Draft
