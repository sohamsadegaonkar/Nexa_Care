# Nexa Care V2 — Cryptographic Consent Payloads & Handshake Specification

**Module Owner:** Cryptographic Consent Backend (Workstream 2)  
**Coordinating Partners:** Lead Architect (WS1), Patient Mobile App (WS6)  
**Version:** `v2.0.0-alpha`  
**Status:** LOCKED & ENFORCED

---

## 1. Architectural & Security Governance

1. **Strict Cryptographic Contract:** All push consent approvals rely on ECDSA P-256 signatures generated inside the patient device's Secure Enclave (iOS) or Hardware-Backed Keystore (Android).
2. **Ambiguity & Boundary Protection:** Canonical payload serialization uses pipe (`|`) delimiters. This prevents field-boundary collision attacks where concatenation of adjacent variable-length strings could otherwise yield identical pre-hash inputs (e.g. `req1` + `23` vs `req` + `123`).
3. **No Private Key Persistence:** Only DER-encoded public keys are stored server-side. Private signing keys never leave the secure hardware boundary.

---

## 2. Consent Request Payload (`ConsentRequest`)

Submitted by the provider dashboard to initiate a cryptographic challenge.

```json
{
  "patient_id": "123e4567-e89b-12d3-a456-426614174001",
  "provider_id": "987fcdeb-51a2-43d7-9012-345678901234",
  "purpose": "routine_checkup",
  "requested_scope": "clinical",
  "access_duration_seconds": 3600
}
```

| Attribute | Type | Description |
| :--- | :--- | :--- |
| `patient_id` | UUID string | Target patient record identifier |
| `provider_id` | UUID string | Requesting clinician or data steward identifier |
| `purpose` | String | Justification (`routine_checkup`, `specialist_consult`, `emergency`) |
| `requested_scope` | String | Authorized data boundary (`clinical` or `full`) |
| `access_duration_seconds` | Integer | Requested capability TTL in seconds |

---

## 3. Challenge Delivery Payload (`ChallengePayload`)

Transmitted from the backend to the patient's mobile device via push notification and websocket polling.

```json
{
  "request_id": "888e4567-e89b-12d3-a456-426614174888",
  "patient_id": "123e4567-e89b-12d3-a456-426614174001",
  "provider_name": "Dr. Meera Joshi",
  "hospital_name": "CityCare Hospital",
  "purpose": "routine_checkup",
  "scope": "clinical",
  "access_duration": 3600,
  "challenge_nonce": "a8f902c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "created_at": "2026-07-07T16:00:00Z",
  "expires_at": "2026-07-07T16:01:30Z"
}
```

---

## 4. Signed Payload Specification (Exact Canonical Bytes)

Before signing, the mobile app constructs a strict UTF-8 string by concatenating 9 canonical attributes with pipe (`|`) delimiters. Direct inclusion of provider, scope, purpose, duration, and expiration ensures the patient explicitly signs the full context of authorization rather than a simple boolean decision:

```text
signing_input = SHA-256(
  request_id + "|" +
  patient_id + "|" +
  provider_id + "|" +
  challenge_nonce + "|" +
  decision + "|" +
  requested_scope + "|" +
  purpose + "|" +
  access_duration_seconds + "|" +
  expires_at
)
signature = ECDSA_P256_sign(device_private_key, signing_input)
```

### Exact Byte Construction Example
Given:
- `request_id`: `888e4567-e89b-12d3-a456-426614174888`
- `patient_id`: `123e4567-e89b-12d3-a456-426614174001`
- `provider_id`: `987fcdeb-51a2-43d7-9012-345678901234`
- `challenge_nonce`: `a8f902c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- `decision`: `approved`
- `requested_scope`: `clinical`
- `purpose`: `routine_checkup`
- `access_duration_seconds`: `3600`
- `expires_at`: `2026-07-07T16:01:30Z`

Canonical pre-hash ASCII input string:
```text
888e4567-e89b-12d3-a456-426614174888|123e4567-e89b-12d3-a456-426614174001|987fcdeb-51a2-43d7-9012-345678901234|a8f902c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|approved|clinical|routine_checkup|3600|2026-07-07T16:01:30Z
```
The device computes the SHA-256 digest of this byte array and signs it using ECDSA SECP256R1 (P-256).

---

## 5. Security & Invariant Verification Matrix

To ensure defense-in-depth across Workstream 2 and Workstream 6, the backend enforces 7 mandatory cryptographic invariants:

| # | Verification Check | Enforcement Mechanism | Code / Schema Reference |
| :---: | :--- | :--- | :--- |
| **1** | Valid ECDSA P-256 DER Public Key | `register-device-key` validates DER ASN.1 syntax and checks `isinstance(key.curve, ec.SECP256R1)` before insertion. | `app/api/v2/assurance_routes.py` |
| **2** | Revoked Device Rejection | Signature verifier filters out or rejects keys where `revoked_at IS NOT NULL`. | `app/services/biometric_signature_verifier.py` |
| **3** | Patient Identity Binding | Public key lookup strictly queries by the target `patient_id` parameter matching the challenge. | `app/services/biometric_signature_verifier.py` |
| **4** | Full Grant Scope Binding | Consent token payload in Redis binds `patient_id`, `clinician_id`, `purpose`, `scope`, and `ttl`. | `app/services/consent_engine.py` |
| **5** | Single-Use Approval ID | `signed_approval_id` is persisted and indexed; resolved challenges are immediately consumed. | `app/models/consent_grant.py` |
| **6** | Replay & Nonce Protection | `challenge_nonce` is verified against short-lived Redis key `biometric_nonce:{nonce}:used`. | `app/services/biometric_signature_verifier.py` |
| **7** | Explicit Expiration Binding | `expires_at` is enforced by Redis TTL and signed explicitly within the 9-attribute digest. | `docs/consent-payloads.md` |

---

## 5. Signed Approval Response (`SignedApproval`)

Submitted by the patient mobile app back to the verification endpoint (`POST /api/v2/consent/approve-signed`).

```json
{
  "request_id": "888e4567-e89b-12d3-a456-426614174888",
  "decision": "approved",
  "signature": "MEUCIQDx4XY9zK1A...base64-der-encoded-ecdsa-sig...",
  "challenge_nonce": "a8f902c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

| Attribute | Type | Description |
| :--- | :--- | :--- |
| `request_id` | UUID string | Corresponds to active challenge ID |
| `decision` | String | `approved` or `denied` |
| `signature` | String | Base64 DER-encoded ECDSA P-256 signature |
| `challenge_nonce` | String | 32-byte hex nonce matching challenge payload |
