# Nexa Care — Canonical Consent Payloads

## Routine request

The authenticated provider submits no caller-authoritative patient or provider
identifier:

```json
{
  "discovery_handle": "...",
  "purpose": "routine_checkup",
  "scope": "clinical",
  "access_duration_seconds": 900
}
```

`scope` is exactly one of `clinical`, `full`, or `documents`. The
server consumes the provider/hospital/session-bound discovery handle once,
derives the patient and provider, and clamps the requested access duration.

## Challenge state machine

The server writes a 120-second Redis challenge in `pending_audit`, appends
`CONSENT_REQUEST_CREATED`, then atomically promotes it to `pending` while
preserving remaining TTL. Only then is notification attempted. Failed cleanup
may leave storage bytes but must never create authority.

## Patient signing input

The patient approval request contains:

```json
{
  "request_id": "...",
  "patient_id": "...",
  "decision": "approved",
  "challenge_nonce": "...",
  "signature": "...",
  "device_id": "..."
}
```

The patient ID is authenticated and challenge-bound; it is not provider
authority. Before signing, client and server produce UTF-8 JSON with
`sort_keys=true`, `separators=(",", ":")`, and `ensure_ascii=false` from:

```text
access_duration
challenge_nonce
decision
device_id
expires_at
issued_at
patient_id
protocol_version = "nexa-consent-v2"
provider_id
purpose
request_id
scope
```

The server computes SHA-256 of those canonical JSON bytes and verifies the
prehashed digest with ECDSA P-256 using the enrolled, active, non-revoked
`PatientDeviceKey` selected by `device_id`. No patient DEK is used for
signature verification. The current private key is SecureStore-protected; do
not treat this as proof of Secure Enclave, StrongBox, or hardware-backed
non-exportability.

## Approval and claim

Approval resolves the challenge but does not return a provider capability. The
authenticated provider polls status and calls
`POST /api/v2/consent/{request_id}/claim-access` once. Its sole successful
response includes `patient_id`, `consent_token`, `purpose`, `scope`,
and `expires_at`. The Redis claim transition is atomic, so replay fails.

Direct routine issuers `/api/v2/consent/grant` and
`/api/v2/consent/routine/issue` are retired with
`410 ROUTINE_DIRECT_ISSUANCE_RETIRED`.
