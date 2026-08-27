# Nexa Care — Current Security State

**Last reconciled:** 2026-08-27
**Source baseline:** `ef45b88233710339ec157e9132808e94e2aa6161`

## Routine patient discovery and consent

NFC resolution now issues a 256-bit CSPRNG, 120-second, provider/hospital/
session-bound discovery handle. It is single-use and audit-gated: discovery
storage begins inert and the handle is disclosed only after the audit write
succeeds. It carries no patient UUID, public identifier, redirect detail, or
clinical data.

Routine consent creation consumes that handle and stores a 120-second Redis
challenge as `pending_audit`. The `CONSENT_REQUEST_CREATED` audit event must
succeed before an atomic `pending_audit -> pending` transition; notification
is attempted afterwards. One discovery handle can make at most one challenge.

Patient approval signs UTF-8 canonical JSON with `sort_keys=true`,
`separators=(",", ":")`, and `ensure_ascii=false`, protocol
`nexa-consent-v2`. The SHA-256 digest is verified with ECDSA P-256 using the
active, non-revoked `PatientDeviceKey` selected by `device_id`; a patient
DEK is not used.

Approval does not disclose a provider capability. The owner-scoped provider
claims approved access through `/api/v2/consent/{request_id}/claim-access`.
That atomic, one-time transition returns the patient ID and scoped capability
only after approval. Failed cleanup can leave bytes but never authority.

Direct routine issuers `/api/v2/consent/grant` and
`/api/v2/consent/routine/issue` are retired with
`410 ROUTINE_DIRECT_ISSUANCE_RETIRED`.

## Identity and device state

The current forward migration adds `public_patient_id`: an opaque public
identifier, not an authorization credential or replacement for the internal
patient UUID. Routine authority still comes only from the discovery,
approval, and one-time claim sequence.

Patient device enrollment records an ECDSA P-256 public key in
`PatientDeviceKey`. Client private-key material is stored using platform
SecureStore, but hardware-non-exportable signing has not been proven. Physical
device validation remains outstanding.

## Emergency access

Break-glass is separate from routine discovery. It requires recent provider
MFA, a controlled reason code, and a justification; the server approves a
minimum-necessary scope for 15 minutes, audits access, and attempts patient
notification.

## Remaining operational conditions

- Production cutover requires legacy routine-capability purge or expiry before
  the new routine invariant is declared live.
- Local tests do not establish fresh production Redis, device, or deployment
  evidence.
- Existing KMS, FHIR, pipeline, and device-validation limitations retain their
  separate qualification requirements.
