# Nexa Care — Canonical Authority-Critical API Contracts

**Reconciled:** 2026-09-09  
**Source baseline:** `aa091e14cf38124ca81e32438b49bdad4d79be8b`

## Scope

This document is the current canonical contract for authority-critical patient
discovery, Signed Consent V3, patient cryptographic-device lifecycle/recovery,
and consent-gated FHIR export.

Historical V2/raw-ID/direct-issuance contracts are not current authority even
when compatibility code still exists. Endpoint-specific pipeline/emergency
contracts retain their own authorization rules, but the authority invariants at
the end of this document apply to them.

Core separation:

```text
account authentication
!= provider clinical eligibility
!= patient session authority
!= patient device authority
!= patient consent
!= approved record-access capability
```

## 1. NFC discovery

**Endpoint:** `POST /api/v2/nfc/resolve`  
**Authentication:** authenticated provider under current provider/session policy.

Request:

```json
{ "card_uid": "..." }
```

Response:

```json
{ "discovery_handle": "...", "expires_at": "..." }
```

The response contains no patient UUID, public identifier, redirect chain, or
clinical data. The discovery handle is opaque, provider/hospital/session-bound,
short-lived, single-use, and audit-gated before disclosure authority becomes
active.

## 2. Signed Consent V3 request

**Endpoint:** `POST /api/v2/consent/v3/request`  
**Authentication:** provider with current `CONSENT_REQUEST` clinical capability
and initiation assurance.

Request:

```json
{
  "protocol_version": "nexa-consent-v3",
  "discovery_handle": "...",
  "purpose": "routine_checkup",
  "scope": "clinical",
  "access_duration_seconds": 900
}
```

Rules:

- `protocol_version` must be exactly `nexa-consent-v3`;
- routine scopes are `clinical` or `full`;
- `documents` scope is valid only with the server-governed
  `document_processing` purpose, and that purpose requires `documents` scope;
- access duration is server-clamped to the permitted range;
- patient identity comes only from the consumed discovery handle;
- an active patient device key is required;
- provider/facility/affiliation/capability assurance is captured server-side;
- the Redis request is `pending_audit` until the request audit succeeds, then
  atomically becomes `pending`.

Response:

```json
{
  "protocol_version": "nexa-consent-v3",
  "request_id": "...",
  "status": "pending",
  "expires_in_seconds": 120,
  "challenge_nonce": "...",
  "notification_dispatch": "queued",
  "notification_queued": true,
  "delivery_status": "queued"
}
```

Push delivery is availability-sensitive. Failure to deliver a notification does
not create access authority and does not bypass the pending patient decision.

## 3. Patient challenge retrieval

**Endpoint:** `GET /api/v2/consent/v3/challenge/{request_id}`  
**Authentication:** authoritative current patient session matching the request.

The challenge exposes the information required for the patient to understand
and sign the request, including:

```json
{
  "protocol_version": "nexa-consent-v3",
  "request_id": "...",
  "patient_id": "...",
  "provider_id": "...",
  "hospital_id": "...",
  "provider_name": "...",
  "hospital_name": "...",
  "purpose": "...",
  "scope": "...",
  "access_duration": 900,
  "challenge_nonce": "...",
  "issued_at": "...",
  "expires_at": "...",
  "consent_context_hash": "...",
  "status": "pending"
}
```

The patient ID in this authenticated patient-facing challenge is not a provider
discovery result and must not be exposed by the NFC resolve endpoint.

## 4. Signed patient decision

**Endpoint:** `POST /api/v2/consent/v3/approve-signed`  
**Authentication:** authoritative current patient session.

Request:

```json
{
  "protocol_version": "nexa-consent-v3",
  "request_id": "...",
  "patient_id": "...",
  "decision": "approved",
  "challenge_nonce": "...",
  "consent_context_hash": "...",
  "signature": "...",
  "device_id": "...",
  "key_id": "...",
  "key_version": 1,
  "public_key_fingerprint": "..."
}
```

The canonical V3 signing domain binds all of the following:

- protocol version `nexa-consent-v3`;
- domain `NEXA_CARE_SIGNED_CONSENT`;
- operation `CONSENT_DECISION`;
- request ID;
- patient ID;
- provider ID;
- hospital/facility ID;
- challenge nonce;
- decision;
- purpose;
- scope;
- access duration;
- issued and expiry timestamps;
- immutable `consent_context_hash`;
- stable logical `device_id`;
- exact immutable device-key row ID;
- exact `key_version`;
- public-key fingerprint.

The backend verifies ECDSA P-256 against the exact active/current enrolled public
key version. Replaced, revoked, compromised, foreign, or stale key versions do
not satisfy the verifier.

A valid patient signature is necessary but not sufficient for provider access.
Current provider professional verification, facility verification, affiliation,
and clinical capability are re-evaluated before protected authority is issued.

Response intentionally remains minimal:

```json
{
  "protocol_version": "nexa-consent-v3",
  "request_id": "...",
  "status": "approved",
  "responded_at": "..."
}
```

Approval itself does not disclose a provider record-access capability.

## 5. Approved-access claim

**Endpoint:** `POST /api/v2/consent/v3/{request_id}/claim-access`  
**Authentication:** the provider that owns the approved V3 request and remains
clinically eligible.

Response:

```json
{
  "protocol_version": "nexa-consent-v3",
  "patient_id": "...",
  "consent_token": "...",
  "purpose": "...",
  "scope": "...",
  "expires_at": "..."
}
```

The claim is one-time. Provider trust is re-evaluated at claim time. The exact
approving device key must still satisfy the V3 authority rules when required by
the current claim path.

## 6. Legacy consent compatibility boundary

Legacy Signed Consent V2 bytes are not mutated in place. New V2 requests are
explicitly tagged `nexa-consent-v2` and cannot mint current approved-access
authority after the V3 cutover.

The retired V2 claim boundary returns:

`410 SIGNED_CONSENT_V2_ACCESS_RETIRED`

with an upgrade direction to `nexa-consent-v3`.

`POST /api/v2/consent/grant` and
`POST /api/v2/consent/routine/issue` remain retired direct-routine issuers and
are not recovery shortcuts.

## 7. Patient cryptographic-device authority

Base path: `/api/v2/patient/devices`  
**Authentication:** authoritative current patient session unless the endpoint
states an additional proof requirement.

The backend stores enrolled P-256 public keys only. It never receives or stores
the patient private key.

### 7.1 Bootstrap enrollment

**Endpoint:** `POST /api/v2/patient/devices/enroll`

Request fields:

- `device_public_key`: Base64 DER P-256 public key;
- `device_label`;
- `platform`;
- optional `expo_push_token`;
- exact-session `device_enrollment_token`.

Bootstrap enrollment is first-device-only. Any prior device history causes
`409 DEVICE_RECOVERY_REQUIRED`; ordinary account login does not reset this
boundary.

Response contains `device_id`, immutable `key_id`, `key_version`, `status`,
`patient_id`, and `enrolled_at`.

### 7.2 Device inventory

**Endpoint:** `GET /api/v2/patient/devices`

Each returned key version includes:

- stable logical `device_id`;
- immutable `key_id`;
- `key_version`;
- device label/platform;
- lifecycle status;
- enrollment/revocation metadata;
- canonical `public_key_fingerprint`.

Raw public DER and private-key material are not returned.

### 7.3 Trusted-device enrollment

A current trusted device may authorize a new logical device through:

- `POST /api/v2/patient/devices/{device_id}/trusted-enrollment/challenge`;
- `POST /api/v2/patient/devices/{device_id}/trusted-enrollment/authorize`.

The challenge is bound to patient, exact current session, authorizer logical
device, exact authorizer key version, operation, prospective new-key
fingerprint, issuance/expiry, and one-time nonce. Authorization requires a valid
signature by the exact current authorizer key. No private key is transferred.

### 7.4 Lost-device recovery

Recovery is explicit and separate from account login:

- `POST /api/v2/patient/devices/recovery/otp/send`;
- `POST /api/v2/patient/devices/recovery/otp/verify`;
- `POST /api/v2/patient/devices/recovery/complete`.

A fresh upstream OTP verification issues a short-lived one-time recovery
capability bound to the current patient/session/identity. Completion consumes
that capability, invalidates old patient sessions, revokes/replaces old device
authority according to policy, installs one fresh P-256 public key, and issues a
fresh patient session. Recovery never reconstructs an old private key.

### 7.5 Key rotation

Routine rotation requires current-key proof-of-possession:

- `POST /api/v2/patient/devices/{device_id}/rotation/challenge`;
- `POST /api/v2/patient/devices/{device_id}/rotate`.

The one-time challenge binds the exact patient session, logical device, current
key version, operation, and new-key fingerprint. Successful rotation advances
the key version and makes the old version non-current.

### 7.6 Revocation

**Endpoint:** `POST /api/v2/patient/devices/{device_id}/revoke`

Revocation is server-first. Once the backend revokes the current key, the client
may remove local native authority. Local deletion alone is not server
revocation.

## 8. Native client custody boundary

Routine mobile signing uses native key aliases via `NexaDeviceSecurity`; the
canonical client path does not generate or sign with a JavaScript-readable raw
P-256 scalar.

Source/CI compilation does not prove that a particular physical phone executed
StrongBox, hardware-backed Android Keystore, or Secure Enclave. Physical claims
remain governed by the Slice 6I evidence contract.

## 9. FHIR R4 export

**Endpoint:** `GET /api/v2/fhir/export/{patient_id}`  
**Authentication:** provider with current `RECORD_READ` clinical capability and
active consent for the patient.

The export reads current structured patient records first and uses the legacy
clinical shard only as a backward-compatible fallback when structured records
are absent. Audit failure aborts export with a server error; an unaudited export
must not succeed.

The returned object is a FHIR Bundle-shaped response with strict top-level
validation. Internal validation does not constitute external FHIR certification
or partner interoperability qualification.

## 10. Pipeline authority invariants

Pipeline endpoint schemas evolve independently, but these authority rules are
canonical:

- patient ownership is derived from authoritative server-side job/field rows,
  never trusted from a caller-supplied patient ID;
- provider clinical capability and patient consent remain separate required
  authorities where the route performs protected clinical work;
- HIGH/CRITICAL-risk `auto_approved` fields are rejected at commit;
- low-confidence/unsafe fields are routed to review by the canonical
  auto-approval policy;
- runtime `AUTO_COMMIT` remains disabled for pilot qualification;
- source/evidence provenance is preserved and failure/quarantine states do not
  become clinical authority by retry alone.

## 11. Operational invariants

- A discovery handle creates at most one current consent challenge.
- A V3 approved request has at most one successful access claim.
- Security-critical Redis/audit failures fail closed.
- Account authentication cannot silently create replacement device authority.
- Device/recovery/rotation one-time authority is not resurrected after a later
  PostgreSQL failure; retry requires a fresh authority according to the
  qualified operation ordering.
- Arbitrary UUID, bearer token, legacy role, consent alone, or stale device key
  possession does not independently authorize clinical access.
- The current single database migration head is
  `20260909_device_trust_lifecycle`.

## 12. Explicit nonclaims

This contract does not claim:

- completion of Slice 6I physical handset qualification;
- physical StrongBox/Secure Enclave/native NFC execution;
- external FHIR certification;
- live official ABDM/NHA HPR/HFR qualification;
- generic production deployment certification;
- that Nexa Care is "fully secure".
