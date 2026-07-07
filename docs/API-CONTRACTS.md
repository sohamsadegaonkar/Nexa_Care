# Nexa Care Alpha Demo — Canonical API Contracts (`v2`)

**Base Base URL:** `${NEXT_PUBLIC_API_URL}`  
**Version:** `v2.0.0-alpha`  
**Status:** LOCKED (Specification Freeze for 10-Squad Alpha Milestone)

---

## Strict Architectural & Security Governance

1. **Mandatory Dual-Gating (Auth + Consent):** Every patient data endpoint requires valid **Provider Authentication** (`Authorization: Bearer <token>`) AND a cryptographically scoped **Consent Token** (`X-Consent-Token: <token>`, `X-Consent-Purpose: <purpose>`). Any call lacking either header must fail immediately with `401 Unauthorized` or `403 Forbidden` respectively.
2. **Cryptographic Payload Precision:** Biometric push consent verification requires ECDSA P-256 signatures over SHA-256 hashes of exact canonical byte sequences.
3. **No Localhost Endpoints:** All base URLs must use the environment variable placeholder `${NEXT_PUBLIC_API_URL}`.

---

## Table of Contents
1. [Device Enrollment Endpoints](#1-device-enrollment-endpoints)
2. [Consent Flow Endpoints](#2-consent-flow-endpoints)
3. [Patient Records Endpoints](#3-patient-records-endpoints)
4. [AI Pipeline & Ingestion Endpoints](#4-ai-pipeline--ingestion-endpoints)

---

## 1. Device Enrollment Endpoints

### 1.1 Enroll Patient Device
Registers a patient's mobile device public key (ECDSA P-256 / Secure Enclave) and push notification token for biometric consent signing.

- **URL:** `POST ${NEXT_PUBLIC_API_URL}/api/v2/patient/devices/enroll`
- **Authentication:** Required (`Authorization: Bearer <patient_session_token>`)
- **Consent Requirement:** Required (`X-Consent-Token: <self_consent_token>`, `X-Consent-Purpose: device_enrollment`)

#### Request Headers
```http
Authorization: Bearer <patient_session_token>
X-Consent-Token: <token>
X-Consent-Purpose: device_enrollment
Content-Type: application/json
```

#### Request Body (TypeScript Interface / JSON Schema)
```typescript
interface DeviceEnrollmentRequest {
  patient_id: string; // UUIDv4
  device_name: string; // e.g., "iPhone 15 Pro"
  platform: "ios" | "android";
  expo_push_token: string; // Regex: /^ExponentPushToken\[.*\]$/
  public_key: string; // Base64 DER-encoded ECDSA P-256 public key
}
```

#### Success Response (`201 Created`)
```typescript
interface DeviceEnrollmentResponse {
  device_id: string; // UUIDv4
  patient_id: string; // UUIDv4
  status: "active";
  enrolled_at: string; // ISO 8601 Timestamp
}
```

#### Error Responses
- `400 Bad Request`: Invalid public key encoding or unsupported algorithm.
- `401 Unauthorized`: Missing or invalid patient bearer session.
- `403 Forbidden`: Missing or invalid consent scope.
- `409 Conflict`: Device key already enrolled for another active record.

---

### 1.2 List Enrolled Devices
Retrieves active biometric signing devices associated with the patient record.

- **URL:** `GET ${NEXT_PUBLIC_API_URL}/api/v2/patient/devices`
- **Authentication:** Required (`Authorization: Bearer <session_token>`)
- **Consent Requirement:** Required (`X-Consent-Token: <token>`, `X-Consent-Purpose: security_audit`)

#### Request Headers
```http
Authorization: Bearer <session_token>
X-Consent-Token: <token>
X-Consent-Purpose: security_audit
```

#### Success Response (`200 OK`)
```typescript
interface EnrolledDevicesListResponse {
  patient_id: string; // UUIDv4
  devices: Array<{
    device_id: string; // UUIDv4
    device_name: string;
    platform: "ios" | "android";
    public_key_fingerprint: string; // SHA-256 hex fingerprint of DER key
    is_active: boolean;
    last_used_at: string | null; // ISO 8601 Timestamp
    enrolled_at: string; // ISO 8601 Timestamp
  }>;
}
```

#### Error Responses
- `401 Unauthorized`: Provider/Patient authentication failed.
- `403 Forbidden`: Consent token invalid or expired.

---

## 2. Consent Flow Endpoints

### 2.1 Request Push Consent Challenge
Initiates a push-based cryptographic consent request from a provider to a patient's mobile device.

- **URL:** `POST ${NEXT_PUBLIC_API_URL}/api/v2/consent/request`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Gated by Provider Scope (`X-Hospital-Id: <hospital_uuid>`)

#### Request Headers
```http
Authorization: Bearer <provider_session_token>
X-Hospital-Id: <hospital_uuid>
Content-Type: application/json
```

#### Request Body
```typescript
interface ConsentChallengeRequest {
  patient_id: string; // UUIDv4
  provider_id: string; // UUIDv4
  purpose: "routine_checkup" | "specialist_consult" | "emergency" | "ai_ingestion";
  scope: "clinical" | "full";
}
```

#### Success Response (`201 Created`)
```typescript
interface ConsentChallengeResponse {
  request_id: string; // UUIDv4
  challenge_nonce: string; // High-entropy 32-byte hex string
  expires_in_seconds: number; // Default 90 seconds
  notification_sent: boolean;
  status: "pending";
}
```

#### Error Responses
- `401 Unauthorized`: Invalid provider credential.
- `429 Too Many Requests`: Concurrent consent request pending for this patient or rate limit exceeded.

---

### 2.2 Approve Signed Consent Challenge
Submits the patient's Secure Enclave cryptographic signature over the challenge payload.

- **URL:** `POST ${NEXT_PUBLIC_API_URL}/api/v2/consent/approve-signed`
- **Authentication:** Required (`Authorization: Bearer <patient_session_token>`)
- **Consent Requirement:** Implicit in cryptographic challenge verification.

#### Cryptographic Signature Specification
The mobile client must compute the SHA-256 digest of the UTF-8 byte representation of the exact canonical pipe-delimited string:
```
<request_id>|<patient_id>|<provider_id>|<challenge_nonce>|<decision>|<requested_scope>|<access_duration_seconds>|<expires_at>
```
Example string: `123e4567-e89b-12d3-a456-426614174000|987fcdeb-51a2-43d7-9012-345678901234|555e4567-e89b-12d3-a456-426614174000|a8f902...|approved|clinical|1800|2026-07-07T16:00:00Z`  
The signature is generated using ECDSA P-256 over this exact digest and base64-encoded. Any mismatch in attribute ordering, delimiters, or timestamp formatting will result in immediate signature verification failure.

#### Request Headers
```http
Authorization: Bearer <patient_session_token>
Content-Type: application/json
```

#### Request Body
```typescript
interface SignedApprovalRequest {
  request_id: string; // UUIDv4
  patient_id: string; // UUIDv4
  decision: "approved" | "denied";
  challenge_nonce: string;
  signature: string; // Base64 DER-encoded ECDSA P-256 signature
  device_id: string; // UUIDv4 matching enrolled device
}
```

#### Success Response (`200 OK`)
```typescript
interface SignedApprovalResponse {
  request_id: string; // UUIDv4
  status: "approved" | "denied";
  consent_token: string | null; // Minted Redis scoped token if approved, else null
  scope: "clinical" | "full" | null;
  expires_at: string | null; // ISO 8601 Timestamp
}
```

#### Error Responses
- `400 Bad Request`: Malformed payload or expired challenge nonce.
- `401 Unauthorized`: Cryptographic signature verification failed against registered public key.
- `404 Not Found`: Request ID expired or not found.
- `409 Conflict`: Consent request already resolved.

---

### 2.3 Poll Consent Request Status
Allows the provider web dashboard to poll or subscribe to consent resolution.

- **URL:** `GET ${NEXT_PUBLIC_API_URL}/api/v2/consent/status/{request_id}`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Provider Session Check (`X-Hospital-Id: <hospital_uuid>`)

#### Request Headers
```http
Authorization: Bearer <provider_session_token>
X-Hospital-Id: <hospital_uuid>
```

#### Success Response (`200 OK`)
```typescript
interface ConsentStatusResponse {
  request_id: string; // UUIDv4
  patient_id: string; // UUIDv4
  status: "pending" | "approved" | "denied" | "timeout";
  consent_token?: string; // Present when status === 'approved'
  scope?: "clinical" | "full";
  resolved_at?: string; // ISO 8601 Timestamp
}
```

---

## 3. Patient Records Endpoints

### 3.1 Get Patient Clinical Summary
Retrieves de-identified or full clinical summary depending on consent token scope.

- **URL:** `GET ${NEXT_PUBLIC_API_URL}/api/v2/patient/{id}/summary`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: clinical_summary`)

#### Request Headers
```http
Authorization: Bearer <provider_session_token>
X-Consent-Token: <token>
X-Consent-Purpose: clinical_summary
```

#### Success Response (`200 OK`)
```typescript
interface PatientSummaryResponse {
  patient_id: string; // UUIDv4
  pii: {
    patient_name: string | "[REDACTED]";
    phone: string | "[REDACTED]";
    aadhaar_abha_id: string | "[REDACTED]";
  };
  clinical_summary: {
    blood_group: string;
    allergies: string[];
    chronic_conditions: string[];
    active_medications: Array<{
      name: string;
      dosage: string;
      frequency: string;
    }>;
  };
  shard_scope: "clinical" | "full";
}
```

#### Error Responses
- `401 Unauthorized`: Provider authentication failed.
- `403 Forbidden`: Consent token missing, expired, or revoked.
- `410 Gone`: Patient data has been cryptographically erased (`PATIENT_DATA_ERASED`).

---

### 3.2 Get Patient Clinical Timeline
Retrieves chronological timeline of clinical events and document ingestion commits.

- **URL:** `GET ${NEXT_PUBLIC_API_URL}/api/v2/patient/{id}/timeline`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: timeline_view`)

#### Request Parameters
- `limit`: int (default 20, max 100)
- `cursor`: string (ISO timestamp pagination cursor)

#### Success Response (`200 OK`)
```typescript
interface PatientTimelineResponse {
  patient_id: string; // UUIDv4
  events: Array<{
    event_id: string; // UUIDv4
    event_type: "ENCOUNTER" | "LAB_RESULT" | "PRESCRIPTION" | "DOCUMENT_INGESTED";
    title: string;
    description: string;
    event_date: string; // ISO 8601 Timestamp
    provider_name: string;
    hospital_name: string;
    data_payload: Record<string, any>;
  }>;
  next_cursor: string | null;
}
```

---

### 3.3 Append Clinical Record / Vitals
Appends a structured clinical observation to the patient's encrypted record shard.

> **Write Authorization Policy Notice:**
> - **Alpha Behavior (`v2.0.0-alpha`):** Provider write routes require an authenticated provider session and enforce an **audit-before-write guarantee** (`PATIENT_RECORD_APPEND_ATTEMPT` / `PATIENT_RECORD_APPEND_SUCCESS`).
> - **Pilot Behavior (`v2.1.0-pilot`):** Provider write routes must also require an active treatment relationship, signed consent grant (`clinical_append`), or organization-approved care context.

- **URL:** `POST ${NEXT_PUBLIC_API_URL}/api/v2/patient/{id}/record/vitals`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: clinical_append`)

#### Request Body
```typescript
interface AppendVitalsRequest {
  encounter_id: string; // UUIDv4
  systolic_bp: number;
  diastolic_bp: number;
  heart_rate: number;
  temperature_celsius: number;
  sp_o2_percentage: number;
  recorded_at: string; // ISO 8601 Timestamp
}
```

#### Success Response (`201 Created`)
```typescript
interface AppendRecordResponse {
  record_id: string; // UUIDv4
  patient_id: string;
  status: "committed";
  audit_ledger_hash: string;
}
```

---

## 4. AI Pipeline & Ingestion Endpoints

### Strict Medical Adjudication & Auto-Approval Rules
To ensure patient safety and clinical integrity, automated approval of extracted medical observations is strictly governed by the following safety thresholds:
- `LOW_RISK` + `confidence >= 0.95` $\rightarrow$ `auto_approved`
- `MEDIUM_RISK` $\rightarrow$ `needs_review` by default
- `HIGH_RISK` $\rightarrow$ `needs_review` always
- `CRITICAL_RISK` $\rightarrow$ `needs_review` always

An `ExtractedField` is assigned `status: "auto_approved"` **only if all of the following conditions hold**:
1. `risk_level == LOW_RISK`
2. `confidence >= 0.95`
3. `validation_result.is_valid == true`
4. No clinical conflict exists with active patient records
5. `source_page` exists (is explicitly identified)
6. `source_bbox` exists or exact source text span exists

Any field failing even one of these checks defaults to `status: "needs_review"` and is placed in the Review Queue.

---

### 4.1 Upload Document for Ingestion
Accepts clinical PDF/image files up to 20 MB, initiates background PyTorch classification & AI extraction.

> **Architectural Note (Upload Authorization Boundary):**  
> - **Alpha Behavior (`v2.0.0-alpha`):** Pipeline upload requires a scoped patient consent token (`X-Consent-Token`), representing a doctor-initiated upload during an active patient-authorized encounter.  
> - **Future Pilot Behavior:** Pipeline upload will use role-based organization authorization (`require_role("data_operator")`) plus patient linkage policy and immutable audit logging, enabling batch ingestion of historical records without requiring interactive doctor-style consent tokens.

- **URL:** `POST ${NEXT_PUBLIC_API_URL}/api/v2/pipeline/documents/upload`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: ai_document_ingestion`)

#### Request Headers
```http
Authorization: Bearer <provider_session_token>
X-Consent-Token: <token>
X-Consent-Purpose: ai_document_ingestion
Content-Type: multipart/form-data
```

#### Request Payload
- Form Data Field `file`: Binary file upload (`.pdf`, `.png`, `.jpg`, max 20 MB).
- Form Data Field `patient_id`: string (UUIDv4).

#### Success Response (`202 Accepted`)
```typescript
interface DocumentUploadResponse {
  job_id: string; // UUIDv4
  patient_id: string;
  filename: string;
  status: "processing";
  estimated_completion_seconds: number;
}
```

#### Error Responses
- `413 Payload Too Large`: File exceeds 20 MB cap (`PAYLOAD_TOO_LARGE`).

---

### 4.2 Get Extraction Job Status
Returns real-time progress and structured extraction results for an AI ingestion job.

- **URL:** `GET ${NEXT_PUBLIC_API_URL}/api/v2/pipeline/jobs/{job_id}`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: pipeline_status`)

#### Success Response (`200 OK`)
```typescript
interface ExtractionJobStatusResponse {
  job_id: string; // UUIDv4
  patient_id: string;
  status: "queued" | "processing" | "review_required" | "auto_approved" | "failed";
  document_type: "PRESCRIPTION" | "LAB_REPORT" | "DISCHARGE_SUMMARY" | "UNKNOWN";
  overall_confidence: number; // 0.0 to 1.0
  extracted_fields: ExtractedField[]; // Exact schema matching DATA-MODELS.md
  created_at: string; // ISO 8601 Timestamp
}
```

---

### 4.3 Get Review Queue Items
Fetches extracted fields flagged for human-in-the-loop verification (low confidence or clinical anomalies).

- **URL:** `GET ${NEXT_PUBLIC_API_URL}/api/v2/pipeline/review-queue`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: clinical_review`)

#### Request Parameters
- `hospital_id`: UUIDv4
- `status`: "needs_review" | "in_progress"

#### Success Response (`200 OK`)
```typescript
interface ReviewQueueListResponse {
  items: Array<{
    review_item_id: string; // UUIDv4
    job_id: string;
    patient_id: string;
    document_title: string;
    flagged_fields_count: number;
    highest_risk_level: "LOW_RISK" | "MEDIUM_RISK" | "HIGH_RISK" | "CRITICAL_RISK";
    queued_at: string; // ISO 8601 Timestamp
  }>;
}
```

---

### 4.4 Review / Edit Extracted Field
Steward or physician approves, rejects, or edits an AI-extracted field.

- **URL:** `POST ${NEXT_PUBLIC_API_URL}/api/v2/pipeline/fields/{field_id}/review`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: field_adjudication`)

#### Request Body
```typescript
interface FieldReviewRequest {
  action: "approve" | "reject" | "edit";
  corrected_value?: string; // Required when action === 'edit'
  review_notes?: string;
}
```

#### Success Response (`200 OK`)
```typescript
interface FieldReviewResponse {
  field_id: string; // UUIDv4
  job_id: string;
  previous_status: string;
  new_status: "approved" | "rejected" | "edited";
  final_value: string;
  adjudicated_by: string; // Provider UUID
  adjudicated_at: string; // ISO 8601 Timestamp
}
```

---

### 4.5 Commit Extraction Job to Patient Record
Commits all approved/edited fields from a completed extraction job into the patient's permanent encrypted vault and timeline.

- **URL:** `POST ${NEXT_PUBLIC_API_URL}/api/v2/pipeline/jobs/{job_id}/commit`
- **Authentication:** Required (`Authorization: Bearer <provider_session_token>`)
- **Consent Requirement:** Mandatory (`X-Consent-Token: <token>`, `X-Consent-Purpose: pipeline_commit`)

#### Request Body
```typescript
interface CommitJobRequest {
  patient_id: string; // UUIDv4
  encounter_summary?: string;
}
```

#### Success Response (`201 Created`)
```typescript
interface CommitJobResponse {
  job_id: string; // UUIDv4
  patient_id: string; // UUIDv4
  committed_fields_count: number;
  timeline_event_id: string; // UUIDv4
  ledger_tx_hash: string;
  committed_at: string; // ISO 8601 Timestamp
}
```
