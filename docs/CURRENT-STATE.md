# Nexa Care — Current State (Sprint 2 Hardened)

**Purpose:** Ground-truth document reflecting the state of the system after the Security Hardening Sprint.

**Verification method:** Every claim below is mapped to a specific implementation file or integration test.

**Last updated:** 2026-07-12 (source and automated tests verified; manual device validation pending)

---

## 1. Consent & Assurance Layer

**Hardened Status:** The system now enforces server-side verified assurance levels. Consent is no longer granted blindly; it requires cryptographic proof of patient approval.

| Feature | implementation | Status | Verification |
|---|---|---|---|
| **Canonical Signed Approval** | `app/api/v2/consent_routes.py` (`/approve-signed`) and `app/services/signed_approval_verifier.py` | **Implemented and test-covered** | `tests/test_signed_approval.py`, `tests/integration/test_consent_flow_qa.py` |
| **Biometric Signatures** | `services/deviceKeys.ts` and `SignedApprovalVerifier` | **ECDSA P-256 Enabled** | Canonical 9-field cross-contract tests |
| **Assurance Verifier** | `app/services/assurance_verifier.py` | **Integrated** | `tests/integration/test_full_chain.py` |
| **Consent Revocation** | `app/services/consent_engine.py` (`revoke`) | **Enabled** | `tests/integration/test_consent_revoke_integration.py` |
| **Device Enrollment** | `app/api/v2/device_routes.py`, `services/deviceKeys.ts` | **Implemented** | Public key only; private key remains in SecureStore |
| **Client-Side Signing** | `services/deviceKeys.ts` | **Reachable through canonical biometric screens** | Manual real-device validation required |

**Verification Details:**
- `ConsentEngine.issue()` now calls `AssuranceVerifier.verify()` to validate that the claimed level (e.g., `push_biometric`) matches a verified record in Redis.
- Canonical approval signs `request_id|patient_id|provider_id|challenge_nonce|decision|scope|purpose|access_duration|expires_at`; the backend verifies the SHA-256 digest with ECDSA P-256 and uses `biometric_nonce:{nonce}:used` for replay protection.

---

## 2. Cryptographic Architecture

**Hardened Status:** Migrated from a single global Fernet key to a per-patient envelope encryption model (KMS).

| Feature | implementation | Status | Technical Proof |
|---|---|---|---|
| **Per-Patient DEKs** | `app/models/dek_store.py` | **Live** | `patient_dek_store` table exists with unique versioning. |
| **Envelope Encryption** | `app/services/crypto_kms.py` | **Local Provider** | `LocalEnvelopeProvider` uses system KEK to wrap patient DEKs. |
| **Cryptographic Erasure** | `app/services/crypto_kms.py` (`destroy_dek`) | **Verified** | `tests/test_cryptographic_erasure.py` |
| **Auto-Migration** | `app/services/sharding.py` | **Active** | Legacy Fernet data is re-encrypted on first read using patient DEK. |

**Known Limitations:**
- **Cloud KMS**: Implementation of `KMSProvider` (AWS KMS/Azure Key Vault) is currently a **stub**. Production still uses `LocalEnvelopeProvider`.
- **Key Rotation**: Infrastructure is built for rotation, but an automated schedule is not yet implemented.

---

## 3. High-Risk Action Guardrails

| Action | Control | Implementation |
|---|---|---|
| **Patient Merge** | Fresh TOTP Challenge | `app/api/v2/auth_routes.py` (`/challenge/merge`) |
| **Admin Access** | Role Enforcement | `app/core/dependencies.py` (`require_role("admin")`) |
| **PII Retrieval** | Consent-Gated Decrypt | `app/services/consent_gated_crypto.py` |

**Verification Details:**
- `X-Merge-Challenge` header is mandatory for all merge operations. A challenge token is verified via TOTP and valid for 120 seconds (`tests/test_merge_challenge_security.py`).
- Merge/tombstone integrity now rejects self-merges, duplicate tombstones, and cyclical merge chains; old card redirects fail closed with explicit integrity errors (`tests/test_merge_tombstone_integrity.py`).
- Merged patient redirects are surfaced in the UI via `MergedPatientBanner` in `screen.tsx`.

---

## 4. Structured Records, Emergency, and FHIR

- **Emergency snapshot source of truth**: `app/services/emergency_snapshot_service.py` reads current structured records first (Allergy, Medication, Vitals, LabResult, TimelineEvent). The legacy `nexa_emergency_snapshot` projection is a fallback only and is not required for demo-visible emergency data.
- **FHIR export source of truth**: `app/api/v2/fhir_routes.py` reads current structured records first and maps them to lightweight FHIR R4 resources. The legacy `nexa_clinical` table is deprecated fallback support for older rows only.
- **FHIR coverage limitation**: mapping is partial (MedicationRequest, Observation, AllergyIntolerance, Condition from timeline/legacy diagnoses). Full FHIR profile validation remains future work.

## 5. AI Pipeline Safety Layer

- **Unknown lab ranges fail closed**: generic labs such as `lab_result`, `lab_value`, `cbc`, and `lipid_panel` are validated for numeric value and recognized unit, but no fabricated `0-100` normal range is assigned. They carry `reference_range_known=false`, `unknown_reference_range=true`, and `requires_review=true`, which blocks auto-approval and escalates risk to review.
- **Conflict detection expanded**: intra-job discrepancy checks now cover sugar, blood pressure, HbA1c, pulse/heart rate, SpO2, temperature, weight, same-unit generic labs, and incompatible generic lab units. Any conflict marks involved fields `has_conflict=true`.
- **Commit safety**: `needs_review` fields block pipeline commit; `rejected` fields are skipped and never ingested.

## 6. Audit & Observability

- **Chain Integrity**: Every audit write is hash-chained. The sequence `Record(N).previous_hash = SHA256(Record(N-1))` is enforced.
- **Tamper Evidence**: `scripts/verify_audit_chain.py` successfully detects unauthorized database modifications.
- **Privacy**: Audit logs contain only UUIDs and event types; PII is strictly forbidden from the `metadata` JSONB column.

---

## 7. What's NOT Done / Remaining Risks

1. **WebSocket Stability**: The push status transport defaults to `poll` (2s interval). The `websocket` transport is feature-flagged, requires Redis keyspace notifications (`notify-keyspace-events` including keyspace and set/generic/expiry events), and returns a clear polling fallback error if Redis cannot support it.
2. **FHIR R4 Coverage**: The FHIR export uses current structured records but only maps a subset of FHIR R4 resources. Full R4 validation is deferred to Sprint 3.
3. **Fail-Open Policy**: Rate limiters (Redis outages) still fail-open. While safe for availability, this remains a minor brute-force vector during infra failure.
4. **Manual Real-Device Validation Required**: enrollment and canonical signed approval are implemented and test-covered, but no fresh physical-device run is evidenced after this fix.
5. **Signing Key Is Not Hardware-Isolated**: `signConsentChallenge()` signs the canonical 9-field SHA-256 digest with ECDSA P-256. However, the private key is briefly resident in JS memory during signing rather than never leaving a Secure Enclave/StrongBox. It is encrypted at rest by the OS keystore (`expo-secure-store`) and gated by a biometric prompt before each use, but this is a weaker guarantee than true hardware-isolated signing. If that stronger guarantee is required, it needs a native module and is a separate, larger task — not a quick swap.