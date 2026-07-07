# Nexa Care Alpha Demo — Canonical Sequence Diagrams

**Version:** `v2.0.0-alpha`  
**Status:** LOCKED & ENFORCED (Specification Freeze & Implementation Wiring)

---

## Architectural Notes & Deviations from Day 1 Plan

During Days 3–11 integration and security hardening, the actual implementation incorporated four critical deviations and refinements beyond the initial Day 1 architectural sketch:

1. **Precision Access Gate Routing:**
   - *Day 1 Plan:* Envisioned a single `require_consent` dependency applied blindly to all protected routes.
   - *Real Implementation:* Divided access control into three specialized gates: `require_consent(purpose)` for clinicians accessing clinical records; `require_self_patient_access()` for patients accessing their own dashboard logs (preventing IDOR without synthetic token generation); and `require_role(role)` for data stewards and operators managing AI ingestion jobs and review queues.
2. **Audit Event Separation (`PATIENT_RECORD_READ_SUCCESS`):**
   - *Day 1 Plan:* Recorded only `CONSENT_GATED_DECRYPT_STARTED` during middleware access gating.
   - *Real Implementation:* Emits `CONSENT_GATED_DECRYPT_STARTED` upon entering the gate and an explicit **`PATIENT_RECORD_READ_SUCCESS`** event after the decrypted clinical data is verified and returned to the provider.
3. **Canonical Pipe-Delimited Signing Contract:**
   - *Day 1 Plan:* Proposed a minimal colon-delimited string (`nonce:request_id:patient_id:decision`).
   - *Real Implementation:* Enforces an 8-attribute pipe-delimited payload hash (`SHA256(request_id|patient_id|provider_id|challenge_nonce|decision|requested_scope|access_duration_seconds|expires_at)`) signed via ECDSA P-256 inside the mobile Secure Enclave.
4. **Strict Medical Adjudication Thresholds (`0.95 + LOW_RISK`):**
   - *Day 1 Plan:* Allowed auto-approval at `confidence >= 0.85`.
   - *Real Implementation:* Enforces that only observations meeting all 6 criteria (`LOW_RISK`, `confidence >= 0.95`, `is_valid == true`, no clinical conflict, valid `source_page`, and valid `source_bbox`) receive `status: "auto_approved"`. Any observation flagged as `MEDIUM_RISK`, `HIGH_RISK`, or `CRITICAL_RISK` routes strictly to the human review queue.
5. **Pipeline Upload Authorization Boundary (Alpha vs. Future Pilot):**
   - *Alpha Behavior:* Pipeline upload (`POST /api/v2/pipeline/documents/upload`) requires a scoped patient consent token (`X-Consent-Token`), simulating a doctor upload during an encounter.
   - *Future Pilot Behavior:* Pipeline upload will use role-based organization authorization (`require_role("data_operator")`) plus patient linkage policy and audit logging, decoupling administrative archival ingestion from interactive doctor consent tokens.

---

## 1. Real-Phone Cryptographic Consent Flow

```mermaid
sequenceDiagram
    autonumber
    actor Doc as Provider Web Dashboard
    participant API as FastAPI Backend (v2)
    participant Redis as Redis Consent Store
    participant Push as Expo Push Service
    actor Pat as Patient Mobile App (Expo)
    participant Enclave as Mobile Secure Enclave
    participant KMS as KMS Sharding Engine
    participant Audit as Supabase Audit Ledger

    Note over Doc, API: Phase 1: Challenge Initiation
    Doc->>API: POST /api/v2/consent/request (patient_id, purpose, scope)
    API->>API: Verify Provider Auth & Concurrency Limits
    API->>Redis: SET nexa:push_request:{request_id} (nonce, ttl=90s)
    API->>Audit: Append Log (CONSENT_REQUEST_CREATED)
    API-->>Doc: 201 Created (request_id, status=pending)

    API->>Push: Trigger Push Notification (canonical JSON payload)
    Push->>Pat: Delivery of Push Alert (deep_link: nexacare://push-approval/{request_id})

    Note over Pat, Enclave: Phase 2: Biometric Signing
    Pat->>Pat: Patient opens alert & verifies purpose/scope
    Pat->>Enclave: Biometric Authentication (FaceID / Fingerprint)
    Enclave->>Enclave: Sign SHA-256(req_id|patient_id|provider_id|nonce|decision|scope|duration|expires_at) with ECDSA P-256
    Enclave-->>Pat: Base64 DER Signature

    Note over Pat, API: Phase 3: Verification & Token Minting
    Pat->>API: POST /api/v2/consent/approve-signed (signature, nonce, decision=approved)
    API->>API: Load Patient Enrolled Device Public Key
    API->>API: Verify ECDSA Signature & Check Anti-Replay Nonce
    API->>Redis: Mint Scoped Access Token (token -> {masked_internal_id, scope})
    API->>Redis: DEL nexa:push_request:{request_id} (Resolve Challenge)
    API->>Audit: Append Log (CONSENT_APPROVED_SIGNED / CONSENT_GRANT_SUCCESS)
    API-->>Pat: 200 OK (status=approved, consent_token)

    Note over Doc, KMS: Phase 4: Consent-Gated Data Retrieval
    Doc->>API: GET /api/v2/patient/{id}/record (X-Consent-Token, X-Consent-Purpose)
    API->>API: require_consent() Gate (Validate token binding, purpose, scope)
    API->>Audit: Append Log (CONSENT_GATED_DECRYPT_STARTED)
    API->>KMS: Retrieve Sharded Data (Unwrap DEK via KEK)
    KMS-->>API: Decrypted & Scope-Filtered Clinical Record
    API->>Audit: Append Log (PATIENT_RECORD_READ_SUCCESS)
    API-->>Doc: 200 OK (Patient Record JSON)
```

---

## 2. AI Data Ingestion & Human-in-the-Loop Pipeline Flow

```mermaid
sequenceDiagram
    autonumber
    actor Steward as Clinical Data Steward / Operator
    participant API as FastAPI Pipeline Routes
    participant Ingest as PyTorch Ingestion Engine
    participant Score as Scoring & Validation Engine
    participant Queue as Review Queue Service
    participant Commit as Timeline Committer
    participant Shard as Sharding Vault & Clinical DB
    participant Audit as Supabase Audit Ledger

    Note over Steward, Ingest: Phase 1: Upload & Asynchronous Extraction
    Steward->>API: POST /api/v2/pipeline/documents/upload (PDF/Image, require_role("admin"))
    API->>API: Validate Upload Cap (<= 20 MB) & Operator Role
    API->>Audit: Append Log (DOCUMENT_UPLOADED)
    API->>Ingest: Dispatch Background Extraction Task (job_id)
    API-->>Steward: 202 Accepted (job_id, status=processing)

    Ingest->>Ingest: PyTorch Classification & OCR Field Extraction
    Ingest->>Score: Evaluate Confidence Scores & Clinical Risk Levels
    Score->>Score: Run Range Check Validation (ValidationResult)

    alt All Fields Confidence >= 0.95 & Risk == LOW_RISK & Valid & Explicit Span
        Score->>Commit: Auto-Approve Job (status=auto_approved)
        Score->>Audit: Append Log (EXTRACTION_FIELD_AUTO_APPROVED)
    else Any Field Confidence < 0.95 OR Risk >= MEDIUM_RISK OR Invalid
        Score->>Queue: Flag Job for Adjudication (status=review_required)
    end

    Note over Steward, Queue: Phase 2: Human-in-the-Loop Adjudication
    Steward->>API: GET /api/v2/pipeline/review-queue (require_role("admin"))
    API->>Queue: Query Flagged Extraction Jobs
    Queue-->>Steward: List of ReviewQueueItems with ExtractedField Diagnostic BBoxes

    Steward->>API: POST /api/v2/pipeline/fields/{field_id}/review (action=edit, corrected_value="120/80")
    API->>Queue: Update ExtractedField Status (status=edited, corrected_value)
    API->>Audit: Append Log (EXTRACTION_FIELD_REVIEWED)
    API-->>Steward: 200 OK (Field Adjudicated)

    Note over Steward, Shard: Phase 3: Timeline Commit & Sharded Storage
    Steward->>API: POST /api/v2/pipeline/jobs/{job_id}/commit (require_role("admin"))
    API->>API: Enforce Invariant: Validate numeric confidence & canonical risk_level
    API->>Commit: Commit Final Adjudicated Fields
    Commit->>Shard: Split PII vs Clinical Fields
    Commit->>Shard: Encrypt PII via KMS DEK -> Insert nexa_vault
    Commit->>Shard: Insert De-Identified Data -> Insert nexa_clinical
    Commit->>Shard: Append Immutable TimelineEvent
    Commit->>Audit: Append Log (PIPELINE_COMMITTED_TO_TIMELINE)
    API-->>Steward: 201 Created (Committed to Patient Record)
```
