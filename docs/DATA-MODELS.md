# Nexa Care Alpha Demo — Canonical Shared Data Models

**Version:** `v2.0.0-alpha`  
**Status:** LOCKED (Specification Freeze for 10-Squad Alpha Milestone)

---

## Strict Architectural & Governance Rules

1. **Single Source of Truth:** The schemas defined below are the authoritative models across all frontend (`nexa-client`) and backend (`app/`) squads. Squads 4, 5, and 8 must use `ExtractedField` without modification or extension.
2. **Encrypted at Rest:** PII attributes (`patient_name`, `phone`, `aadhaar_abha_id`) are never stored in plaintext. They are encrypted via Envelope Encryption (per-patient DEK wrapped by system KEK).

---

## 1. ConsentRequest

Represents an active or resolved request from a healthcare provider to access a patient's clinical record.

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `request_id` | UUID | No | Unique identifier for the consent request challenge | Squad 1 (Auth), Squad 2 (Consent) | Squad 2 (Consent Engine) |
| `patient_id` | UUID | No | Target patient identifier | Squad 2, Squad 3 (Mobile App) | Squad 2 (Consent Engine) |
| `provider_id` | UUID | No | Requesting physician or steward ID | Squad 1, Squad 2 | Squad 2 (Consent Engine) |
| `hospital_id` | UUID | No | Healthcare facility ID where request originated | Squad 1, Squad 2 | Squad 2 (Consent Engine) |
| `purpose` | String | No | Clinical justification (`routine_checkup`, `specialist_consult`, `emergency`, `ai_ingestion`) | Squad 2, Squad 9 (Audit) | Squad 2 (Consent Engine) |
| `scope` | String | No | Requested data scope (`clinical` or `full`) | Squad 2, Squad 6 (Sharding) | Squad 2 (Consent Engine) |
| `challenge_nonce`| String | No | 32-byte hex high-entropy cryptographic challenge | Squad 2, Squad 3 | Squad 2 (Consent Engine) |
| `status` | String | No | State: `pending`, `approved`, `denied`, `timeout` | Squad 2, Squad 3 | Squad 2 (Consent Engine) |
| `created_at` | DateTime | No | Timestamp of request initiation | All Squads | Squad 2 (Consent Engine) |
| `expires_at` | DateTime | No | Expiration timestamp (default +90 seconds) | Squad 2, Squad 3 | Squad 2 (Consent Engine) |

---

## 2. SignedApproval

Represents the cryptographic proof submitted by the patient's mobile device to authorize a consent request.

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `approval_id` | UUID | No | Unique record ID for the approval receipt | Squad 2, Squad 9 (Audit) | Squad 2 (Consent Engine) |
| `request_id` | UUID | No | Foreign key linking to `ConsentRequest` | Squad 2, Squad 9 | Squad 2 (Consent Engine) |
| `patient_id` | UUID | No | Patient who generated the signature | Squad 2, Squad 6 | Squad 2 (Consent Engine) |
| `device_id` | UUID | No | Enrolled device hardware identifier | Squad 2, Squad 3 | Squad 3 (Mobile App / Client) |
| `decision` | String | No | Patient resolution (`approved` or `denied`) | Squad 2, Squad 9 | Squad 3 (Mobile App) |
| `signature` | String | No | Base64 DER-encoded ECDSA P-256 signature | Squad 2 (Verifier), Squad 9 | Squad 3 (Mobile App) |
| `signed_payload_hash` | String | No | SHA-256 hash of exact canonical pipe-delimited bytes: `<request_id>\|<patient_id>|<provider_id>|<challenge_nonce>|<decision>|<requested_scope>|<access_duration_seconds>|<expires_at>` | Squad 2, Squad 9 | Squad 2 (Consent Engine) |
| `verified` | Boolean | No | Verification result against registered device public key | Squad 2, Squad 9 | Squad 2 (Biometric Verifier) |
| `responded_at` | DateTime | No | Timestamp of signature reception | Squad 2, Squad 9 | Squad 2 (Consent Engine) |

---

## 3. DeviceKey

Represents a patient's enrolled hardware cryptographic public key bound to their mobile device.

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `device_id` | UUID | No | Unique device registration identifier | Squad 2, Squad 3 | Squad 3 (Mobile App) |
| `patient_id` | UUID | No | Patient account owning the hardware key | Squad 2, Squad 3 | Squad 3 (Mobile App) |
| `platform` | String | No | Mobile operating system (`ios` or `android`) | Squad 3 | Squad 3 (Mobile App) |
| `public_key` | Bytes | No | DER-encoded ECDSA SECP256R1 public key | Squad 2 (Biometric Verifier) | Squad 3 (Mobile App) |
| `expo_push_token`| String | No | Expo push token string for notification delivery | Squad 2 (Push Service) | Squad 3 (Mobile App) |
| `is_active` | Boolean | No | Whether device is authorized for signing | Squad 2, Squad 3 | Squad 3 (Mobile App) |
| `enrolled_at` | DateTime | No | Initial registration timestamp | Squad 2, Squad 3 | Squad 3 (Mobile App) |

---

## 4. ConsentGrant

Represents the active, time-bound Redis access token minted upon successful signature verification.

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `token` | String | No | UUID string acting as Bearer / X-Consent-Token | Squad 6, Squad 7, Squad 8 | Squad 2 (Consent Engine) |
| `masked_internal_id` | UUID | No | Shard link identifier for patient record | Squad 6 (Sharding Engine) | Squad 2 (Consent Engine) |
| `scope` | String | No | Access boundary (`clinical` or `full`) | Squad 6 (Sharding Engine) | Squad 2 (Consent Engine) |
| `issued_at` | DateTime | No | Token mint timestamp | Squad 2, Squad 9 | Squad 2 (Consent Engine) |
| `ttl_seconds` | Integer | No | Time to live (default 1800s / 30 mins) | Squad 2 | Squad 2 (Consent Engine) |

---

## 5. ExtractedField (Strict Canonical Specification)

Authoritative data schema representing an atomic data point extracted from an uploaded medical document by the AI pipeline. Squads 4, 5, and 8 must adhere strictly to this definition.

```typescript
interface ValidationResult {
  is_valid: boolean;
  validation_errors: string[];
  reference_range?: {
    min?: number;
    max?: number;
    unit: string;
    is_abnormal?: boolean | null;
    reference_range_known?: boolean;
    unknown_reference_range?: boolean;
    requires_review?: boolean;
  };
}

interface ExtractedField {
  field_id: string;          // UUIDv4 identifier for the extracted field
  job_id: string;            // UUIDv4 linking to parent ExtractionJob
  field_name: string;        // Canonical clinical name e.g. "medication", "bp", "hba1c"
  raw_value: string;         // Exact string extracted from OCR/PyTorch text
  normalized_value: string | null; // Standardized representation (e.g. SNOMED/LOINC/numeric)
  confidence: number;        // Float between 0.0 and 1.0 indicating model assurance
  risk_level: "LOW_RISK" | "MEDIUM_RISK" | "HIGH_RISK" | "CRITICAL_RISK"; // Clinical risk severity
  validation_result: ValidationResult; // Automated reference range & clinical validation check
  source_page: number;       // 1-indexed page number in the source PDF/image
  source_bbox: [number, number, number, number] | null; // [x, y, width, height] normalized coordinates
  status: "auto_approved" | "needs_review" | "approved" | "rejected" | "edited"; // Workflow adjudication status
  corrected_value: string | null; // Human-steward corrected string if status === 'edited'
}
```

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `field_id` | UUID | No | Unique identifier for extracted observation | Squad 4, 5, 8 | Squad 4 (PyTorch Ingestion) |
| `job_id` | UUID | No | Parent ingestion job reference | Squad 4, 5, 8 | Squad 4 (PyTorch Ingestion) |
| `field_name` | String | No | Clinical property label (`medication`, `bp`, etc.) | Squad 4, 5, 8 | Squad 4 (PyTorch Ingestion) |
| `raw_value` | String | No | Literal OCR text representation | Squad 4, 5, 8 | Squad 4 (PyTorch Ingestion) |
| `normalized_value`| String | Yes | Standardized unit/code value | Squad 4, 5, 8 | Squad 4 (PyTorch Ingestion) |
| `confidence` | Float | No | AI confidence score `[0.0, 1.0]` | Squad 4, 5, 8 | Squad 4 (PyTorch Ingestion) |
| `risk_level` | Enum | No | Clinical risk tier (`LOW_RISK` to `CRITICAL_RISK`)| Squad 5 (Review Queue), 8 | Squad 4 (Scoring Engine) |
| `validation_result`| JSON | No | Range check and validity diagnostics | Squad 5, Squad 8 | Squad 4 (Validation Rules) |
| `source_page` | Integer | No | Page number in uploaded document | Squad 5 (Review UI), 8 | Squad 4 (PyTorch Ingestion) |
| `source_bbox` | Array | Yes | OCR bounding box `[x, y, w, h]` | Squad 5 (Review UI), 8 | Squad 4 (PyTorch Ingestion) |
| `status` | Enum | No | Lifecycle status (`auto_approved`, `needs_review`, etc.). **Strict Auto-Approval Threshold:** `status` may only be set to `auto_approved` when `risk_level == LOW_RISK` AND `confidence >= 0.95` AND `validation_result.is_valid == true` AND `source_page` exists AND `source_bbox` or text span exists. `MEDIUM_RISK`, `HIGH_RISK`, and `CRITICAL_RISK` always mandate `needs_review`. | Squad 5, Squad 8 | Squad 4 (init) / Squad 5 (Review) |
| `corrected_value`| String | Yes | Steward override string when edited | Squad 5, Squad 8 | Squad 5 (Steward UI) |

---

## 6. ReviewQueueItem

Represents a document extraction job or subset of fields flagged for human clinical adjudication.

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `review_item_id`| UUID | No | Unique queue item identifier | Squad 5 (Review UI) | Squad 4 (Ingestion Pipeline) |
| `job_id` | UUID | No | Foreign key to `ExtractionJob` | Squad 5 | Squad 4 (Ingestion Pipeline) |
| `patient_id` | UUID | No | Target patient account | Squad 5 | Squad 4 (Ingestion Pipeline) |
| `flagged_fields`| JSON | No | List of `field_id` strings requiring review | Squad 5 | Squad 4 (Scoring Engine) |
| `status` | String | No | Queue item state (`pending`, `assigned`, `resolved`)| Squad 5 | Squad 5 (Review Service) |
| `assigned_to` | UUID | Yes | Steward provider ID currently reviewing | Squad 5 | Squad 5 (Review Service) |
| `queued_at` | DateTime | No | Creation timestamp | Squad 5 | Squad 4 (Ingestion Pipeline) |

---

## 7. ExtractionJob

Represents the end-to-end background processing lifecycle of an uploaded medical file.

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `job_id` | UUID | No | Unique batch job identifier | Squad 4, 5, 7, 8 | Squad 4 (Ingestion Pipeline) |
| `patient_id` | UUID | No | Owner patient account | Squad 4, 5, 7, 8 | Squad 4 (Ingestion Pipeline) |
| `file_path` | String | No | Temporary secure file storage path | Squad 4 | Squad 4 (Ingestion Pipeline) |
| `status` | String | No | State (`queued`, `processing`, `review_required`, `auto_approved`, `committed`)| Squad 4, 5, 7, 8 | Squad 4 / Squad 5 / Squad 8 |
| `overall_confidence`| Float| No | Weighted mean confidence across extracted fields | Squad 4, 5, 8 | Squad 4 (Scoring Engine) |
| `created_at` | DateTime | No | Job submission timestamp | Squad 4, 5, 8 | Squad 4 (Ingestion Pipeline) |

---

## 8. PatientRecord & Sharding Architecture

The patient record is split into two disjoint database tables linked only by an unguessable `masked_internal_id`.

### 8.1 Vault Shard (`nexa_vault`) — Protected PII
| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `masked_internal_id` | UUID | No | Shard link key | Squad 6 (Sharding) | Squad 6 (Document Processor) |
| `patient_name` | Bytes | No | KMS DEK-encrypted patient name | Squad 6 | Squad 6 (Sharding Engine) |
| `phone` | Bytes | No | KMS DEK-encrypted phone number | Squad 6 | Squad 6 (Sharding Engine) |
| `aadhaar_abha_id`| Bytes | No | KMS DEK-encrypted Aadhaar/ABHA ID | Squad 6 | Squad 6 (Sharding Engine) |

### 8.2 Clinical Shard (`nexa_clinical`) — De-Identified Data
| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `masked_internal_id` | UUID | No | Shard link key | Squad 6, Squad 7, Squad 8 | Squad 6 / Squad 8 |
| `clinical_data` | JSONB | No | Observations, vitals, allergies, conditions | Squad 6, Squad 7, Squad 8 | Squad 6 / Squad 8 |

---

## 9. TimelineEvent

Represents an immutable historical event appended to the patient clinical timeline.

| Field Name | Type | Nullable | Description | Read Ownership | Write Ownership |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `event_id` | UUID | No | Unique timeline event identifier | Squad 7 (Clinical UI) | Squad 8 (Timeline Committer) |
| `patient_id` | UUID | No | Target patient account | Squad 7 | Squad 8 |
| `event_type` | String | No | Category (`ENCOUNTER`, `LAB_RESULT`, `DOCUMENT_INGESTED`) | Squad 7 | Squad 8 |
| `title` | String | No | Event summary headline | Squad 7 | Squad 8 |
| `data_payload` | JSONB | No | Structured clinical snapshot or committed fields | Squad 7 | Squad 8 |
| `created_at` | DateTime | No | Event occurrence timestamp | Squad 7 | Squad 8 |
