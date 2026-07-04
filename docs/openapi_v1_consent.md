# Nexa Care v1.0 — OpenAPI Documentation (Consent & Merge)

## Base URL
`http://localhost:8000`

---

## Endpoints

### 1. Issue Routine Consent
**POST** `/api/v2/consent/routine/issue`

**Request Body**:
```json
{
  "patient_uuid": "string (uuid)",
  "hospital_id": "string",
  "clinician_id": "string",
  "purpose": "string",
  "consent_assurance": "standard | push_approved | biometric_confirmed"
}
```

**Response** (201):
```json
{
  "consent_token": "string",
  "patient_uuid": "uuid",
  "purpose": "string",
  "consent_assurance": "string",
  "granted_at": "datetime",
  "expires_at": "datetime"
}
```

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

### 4. NFC Card Resolution (with Tombstone Redirect)
**POST** `/api/v2/nfc/resolve`

**Request**:
```json
{ "card_uid": "string" }
```

**Enhanced Response** (when using `CardRedirectService`):
```json
{
  "canonical_patient_uuid": "uuid",
  "redirect_chain": [
    { "from": "uuid", "to": "uuid", "merged_at": "datetime" }
  ],
  "is_redirected": true
}
```

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