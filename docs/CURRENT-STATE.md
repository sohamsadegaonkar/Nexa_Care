# Nexa Care — Current State (Sprint 2 Hardened)

**Purpose:** Ground-truth document reflecting the state of the system after the Security Hardening Sprint.

**Verification method:** Every claim below is mapped to a specific implementation file or integration test.

**Last updated:** 2026-07-07 (verified against source directly, not against prior test/doc claims)

---

## 1. Consent & Assurance Layer

**Hardened Status:** The system now enforces server-side verified assurance levels. Consent is no longer granted blindly; it requires cryptographic proof of patient approval.

| Feature | implementation | Status | Verification |
|---|---|---|---|
| **Async Push Flow** | `app/services/assurance_service.py` | **Complete** | `tests/integration/test_push_roundtrip.py` |
| **Biometric Signatures** | `app/services/biometric_signature_verifier.py` | **ECDSA P-256 Enabled** | `tests/test_biometric_signature.py` |
| **Assurance Verifier** | `app/services/assurance_verifier.py` | **Integrated** | `tests/integration/test_full_chain.py` |
| **Consent Revocation** | `app/services/consent_engine.py` (`revoke`) | **Enabled** | `tests/integration/test_consent_revoke_integration.py` |
| **Device Key Registration (backend)** | `app/api/v2/assurance_routes.py` (`/register-device-key`), `app/services/biometric_registry.py` (`update_device_public_key`) | **Complete** | Update-only; requires an existing provider-enrolled biometric binding, cannot create one |
| **Client-Side Signing** | `nexa-client/packages/app/utils/deviceKey.ts` | **Real ECDSA P-256, no UI entry point yet** | See §5 item 4 — implemented but unreachable from any screen today |

**Verification Details:**
- `ConsentEngine.issue()` now calls `AssuranceVerifier.verify()` to validate that the claimed level (e.g., `push_biometric`) matches a verified record in Redis.
- Biometric signatures are verified using ECDSA P-256 over a 32-byte nonce challenge (`biometric_nonce:{nonce}:used` prevents replay).

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
- Merged patient redirects are surfaced in the UI via `MergedPatientBanner` in `screen.tsx`.

---

## 4. Audit & Observability

- **Chain Integrity**: Every audit write is hash-chained. The sequence `Record(N).previous_hash = SHA256(Record(N-1))` is enforced.
- **Tamper Evidence**: `scripts/verify_audit_chain.py` successfully detects unauthorized database modifications.
- **Privacy**: Audit logs contain only UUIDs and event types; PII is strictly forbidden from the `metadata` JSONB column.

---

## 5. What's NOT Done / Remaining Risks

1. **WebSocket Stability**: The push status transport defaults to `poll` (2s interval). The `websocket` transport is feature-flagged and requires more load testing before production rollout.
2. **FHIR R4 Coverage**: The FHIR export in `fhir_converter.py` only maps a subset of fields. Full R4 validation is deferred to Sprint 3.
3. **Fail-Open Policy**: Rate limiters (Redis outages) still fail-open. While safe for availability, this remains a minor brute-force vector during infra failure.
4. **Device Key Enrollment Has No UI Entry Point**: `enrollDeviceKey()` (`nexa-client/packages/app/utils/deviceKey.ts`) is implemented and correct — real P-256 keypair generation, `expo-secure-store` for the private key, `POST /api/v2/push/register-device-key` on the backend — but nothing in the app currently calls it. No patient will have a usable signing key until this is wired into a real screen (first-login flow or a dedicated security settings screen; this project has not yet decided which). Until that's done, `respondToPushRequest`'s signature verification can only succeed for manually-seeded test data, not a real patient.
5. **Signing Key Is Not Hardware-Isolated**: `signPushChallenge()` produces a real ECDSA-P256 signature, not a placeholder — this replaces what was previously a `sig_v1_...` mock string. However, the private key is briefly resident in JS memory during signing rather than never leaving a Secure Enclave/StrongBox. It is encrypted at rest by the OS keystore (`expo-secure-store`) and gated by a biometric prompt before each use, but this is a weaker guarantee than true hardware-isolated signing. If that stronger guarantee is required, it needs a native module and is a separate, larger task — not a quick swap.