# Nexa Care — Patient Application Flow

## Device enrollment

The patient application enrolls a public signing key through:

`POST /api/v2/patient/devices/enroll`

The authenticated patient sends `device_public_key` (base64 DER ECDSA P-256),
`device_label`, `platform`, optional `expo_push_token`, and the
single-use `device_enrollment_token` obtained during patient authentication.
The server returns `device_id`, status, patient ID, and enrollment time.
`GET /api/v2/patient/devices` lists active devices without public keys, and
`POST /api/v2/patient/devices/{device_id}/revoke` revokes an enrolled key.

The client stores its private key with platform SecureStore. This is not a
claim of Secure Enclave, StrongBox, or hardware-non-exportable signing.

## Consent approval

A patient opens a challenge, reviews the provider, purpose, scope, requested
duration, and expiry, then signs the challenge context — not a consent token.
The client sends `request_id`, authenticated `patient_id`, `decision`,
`challenge_nonce`, `signature`, and `device_id` to:

`POST /api/v2/consent/approve-signed`

The signing bytes are canonical UTF-8 JSON using `sort_keys=true`,
`separators=(",", ":")`, and `ensure_ascii=false`, protocol
`nexa-consent-v2`. They bind the access duration, nonce, decision, device,
issued/expiry time, patient/provider, purpose, request, and scope. The backend
verifies the prehashed SHA-256 digest with the active, non-revoked enrolled
device public key.

Approval is a patient decision; it does not return a provider consent token.
The provider must claim approved access once after approval.

## Patient self-service

Authenticated patient self-service includes `GET /api/v2/patient/me/timeline`
and `GET /api/v2/patient/me/access-history`. These are separate from the
provider routine-access capability and must not be represented as invented
grant receipts, delete-grant endpoints, or unauthenticated history routes.
