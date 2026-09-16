# Nexa Care — Canonical Authority-Critical API Contracts

**Reconciled:** 2026-09-16  
**Source:** Slice 10A merged/qualified authority contract plus active Slice 10B bounded clinical-access-session hardening on `main`.

## Scope

This document is the current canonical contract for authority-critical patient
discovery, Signed Consent V3, bounded routine clinical read authority, patient
cryptographic-device lifecycle/recovery, and consent-gated FHIR export.

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
!= patient discovery identifier match
!= patient discovery capability
!= patient consent
!= approved Redis record-access capability
!= durable ClinicalAccessSession authority
!= encounter/write authority
```

## 1. Patient discovery — Slice 10A

### 1.1 Provider-facing exact discovery

**Endpoint:** `POST /api/v2/patient-discovery`  
**Authentication:** provider with current server-owned `PATIENT_DISCOVER`
clinical capability.

The request vocabulary is closed to the qualified Slice 10A modes:

```text
NEXA_PUBLIC_ID
PHONE
QR_PUBLIC_ID
```

Example request:

```json
{
  "identifier_type": "NEXA_PUBLIC_ID",
  "value": "NC-..."
}
```

Success is deliberately minimum-disclosure for every mode:

```json
{ "discovery_handle": "...", "expires_at": "..." }
```

The response contains no patient UUID, phone, public identifier, name,
demographic summary, redirect chain, clinical data, candidate list, or result
count. A successful identifier match is still only identification; it is not
patient authentication, consent, or record-access authority.

Every supported mode resolves to one active canonical, unerased patient and
then converges on the same provider/hospital/session-bound short-lived Redis
discovery handle. The handle is staged as `PENDING_AUDIT`, mandatory success
audit must complete, activation is atomic and does not extend TTL, and
consumption is atomic and single-use.

Name-only, prefix, fuzzy, ranked-candidate, broad-directory, MRN, generic
external-ID and caller-selected patient-UUID discovery remain prohibited.

### 1.2 Exact Nexa public ID and QR transport

`NEXA_PUBLIC_ID` accepts only the opaque `NC-...` public identifier under the
server parser and canonical merge/deletion/erasure checks.

`QR_PUBLIC_ID` is only a transport wrapper for that same opaque public ID. Its
payload must match the versioned form:

```text
nexa://patient-discovery/v1/NC-...
```

QR never carries a raw patient UUID, access token, consent token, clinical
capability, device credential, or profile payload. Malformed public-ID/QR input
is exposed only as the generic discovery no-match contract, not parser detail.

### 1.3 Exact phone discovery

`PHONE` is a deliberately higher-assurance, lower-rate discovery mode. It is
available only when the patient has explicitly opted in to phone discoverability
and the current indexed authority is still valid.

Before a PHONE lookup is permitted, the server requires all ordinary discovery
authority plus:

- the exact live provider session to match the server-resolved session binding;
- a current provider identity matching that live session;
- recent provider MFA evidence within the qualified freshness window;
- the patient-side phone search binding to have been created from a fresh
  upstream-verified phone event tied to the patient's exact Supabase subject.

PHONE uses exact server normalization only. Absent, opted-out, revoked, stale,
or otherwise nonmatching phone authority is exposed as the same generic
`DISCOVERY_NO_MATCH` result. Integrity ambiguity fails closed as unavailable;
the service never chooses a patient by ranking or first-match behavior.

Low-entropy discovery is throttled with server-owned provider/hospital/type
budgets plus an aggregate cross-type budget. Rate-limit keys never contain the
searched phone/public ID/QR value.

### 1.4 Patient-controlled phone discoverability

Authenticated patient-self controls are:

- `GET /api/v2/patient/me/discoverability/phone`
- `POST /api/v2/patient/me/discoverability/phone/enable`
- `DELETE /api/v2/patient/me/discoverability/phone`

GET returns only `{ "enabled": true|false }` and never returns the phone.

Enable requires the current patient session plus a fresh SMS OTP verification.
The authoritative Supabase response must return the same normalized phone and
the same external subject as the current patient session. The client cannot
submit a patient UUID or choose the patient binding.

The searchable authority stores no raw or normalized phone. It stores only
patient/auth-identity provenance, normalization/key versions, a dedicated
domain-separated HMAC-SHA256 exact-match fingerprint, verification timestamp,
and lifecycle state. Discovery-index HMAC keys are independent of OTP, provider
registration, contact assurance, and other application secrets. Multiple key
versions support controlled rotation while active rows on retired versions fail
closed until reverified/reindexed.

A verified phone collision across patient identities never silently reassigns
search authority. Implicated active bindings are quarantined/revoked and the
patient receives a generic conflict. Disabling discoverability revokes the
search binding without changing the patient's ability to sign in by phone.

### 1.5 NFC discovery

**Endpoint:** `POST /api/v2/nfc/resolve`  
**Authentication:** authenticated provider under current provider/session and
clinical-capability policy.

Request:

```json
{ "card_uid": "..." }
```

Response:

```json
{ "discovery_handle": "...", "expires_at": "..." }
```

The response contains no patient UUID, public identifier, redirect chain, or
clinical data. The NFC path converges on the same opaque,
provider/hospital/session-bound, short-lived, single-use, audit-gated discovery
capability boundary.

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
discovery result and must not be exposed by provider discovery endpoints.

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

The current V3 signing domain does not bind an explicit clinical-write operation
set. It therefore cannot be reinterpreted as consent to create an encounter,
prescription, diagnosis, vital, clinical note, investigation order, or any
other clinical write. Current routine V3 treatment authority is read-only.

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

## 5. Approved-access claim and bounded routine read session

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

For canonical routine V3 clinical/full scopes, a successful claim creates two
cooperating server authorities:

1. a short-lived Redis `clinical_access_session` capability bound to patient,
   provider, hospital, request, exact provider session, policy version, and
   closed operation set; and
2. a durable PostgreSQL `clinical_access_sessions` lifecycle row containing
   only the SHA-256 bearer digest plus those server-owned bindings and lifecycle
   metadata.

The raw bearer capability and the raw provider-session binding are not stored in
PostgreSQL. Routine clinical reads require the Redis capability and durable
PostgreSQL row to agree exactly on the session/request/patient/provider/hospital,
policy, operation set, token digest, provider-session binding hash, expiry, and
revocation state. Either store missing, stale, revoked, tampered, or disagreeing
causes access to fail closed.

The current policy version permits exactly:

```text
READ_CLINICAL_HISTORY
```

It does not permit `CREATE_ENCOUNTER`, `WRITE_PRESCRIPTION`, `WRITE_DIAGNOSIS`,
`WRITE_VITALS`, `WRITE_CLINICAL_NOTES`, or `ORDER_INVESTIGATION`.

The document-processing purpose remains a separate grant type and operation
vocabulary; it does not silently become a routine `ClinicalAccessSession`.

Patient revocation must invalidate live Redis capability state and durable
routine authority. Claim-finalization failures invalidate Redis and compensate
any durable session/grant state fail-closed; an unaudited successful claim must
not remain usable.

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
- Arbitrary UUID, bearer token, legacy role, consent alone, stale device key,
  search-index match, Redis capability alone, or durable session row alone does
  not independently authorize routine clinical access.
- The current single database migration head is
  `20260917_treatment_session_operations`.

### Registration recovery manual review (Slice 9A)

Verified manual-review classification returns a durable opaque `case_reference`.
`GET /api/v2/auth/registration-recovery/review/cases/{case_reference}` accepts
that handle without a patient session and returns only status, terminal flag,
next action and timestamps. It grants no repair or authentication authority.

The separate `/api/v2/auth/registration-recovery/review/reviewer/cases` surface
requires the live dedicated reviewer gate. List/detail expose pending cases and
the reviewer's assigned cases only. Claim and recover-session POSTs accept
`expected_version`; resolve accepts that version, `idempotency_key` (8–192
characters), a closed `outcome`, and closed `reason_codes`. All mutation schemas
reject unknown fields. Terminal replay requires the assigned reviewer and exact
claimed session as well as the original operation tuple. A changed operation
conflicts. Disposition, permitted repair and audit commit atomically.

See [the UI handoff](governance/SLICE_9A_UI_HANDOFF.md) for exact routes, response
fields and patient next actions. Reviewer resolution never issues patient,
trusted-device, provider-access or consent authority.

## 12. Explicit nonclaims

This contract does not claim:

- name-only, fuzzy/prefix, ranked-candidate, broad-directory, MRN or generic
  external-ID patient discovery;
- that phone discoverability is enabled for a patient who has not explicitly
  opted in or whose current binding cannot be revalidated;
- current Signed Consent V3 write authority, encounter creation authority, or
  prescription/diagnosis/vitals/clinical-note write authority;
- Slice 10B completion before its revocation and exact-head adversarial gates are
  green;
- completion of Slice 6I physical handset qualification;
- physical StrongBox/Secure Enclave/native NFC execution;
- external FHIR certification;
- live official ABDM/NHA HPR/HFR qualification;
- generic production deployment certification;
- that Nexa Care is "fully secure".
