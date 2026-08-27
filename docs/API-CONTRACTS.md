# Nexa Care — Canonical API Contracts

## Scope

This document is the canonical contract for Phase 1C routine patient discovery,
consent, and access claim. It replaces historical raw-ID consent creation,
direct routine issuance, and pipe-delimited signing descriptions. Other API
surfaces retain their endpoint-specific authorization requirements.

## NFC discovery

**Endpoint:** `POST /api/v2/nfc/resolve`
**Authentication:** authenticated clinician; provider rate limiting and audit are
server-enforced.

```json
{ "card_uid": "..." }
```

```json
{ "discovery_handle": "...", "expires_at": "..." }
```

The response contains no patient UUID, public identifier, masked ID, redirect
chain, or clinical data. The discovery handle is a 256-bit CSPRNG capability,
bound to provider/hospital/session, valid for 120 seconds, single-use, and
inert until its terminal audit event succeeds.

## Consent challenge

**Endpoint:** `POST /api/v2/consent/request`
**Authentication:** authenticated clinician.

```json
{
  "discovery_handle": "...",
  "purpose": "routine_checkup",
  "scope": "clinical",
  "access_duration_seconds": 900
}
```

The only valid scope values are the strings `clinical`, `full`, and
`documents`. Patient and provider identities are derived by the server. The
challenge is stored for 120 seconds in `pending_audit`, audited as
`CONSENT_REQUEST_CREATED`, then atomically promoted to `pending`.
Notification is attempted after promotion only.

**Status:** `GET /api/v2/consent/status/{request_id}` returns minimal
owner-scoped status. It does not return a provider capability or patient ID.

## Signed patient approval

**Endpoint:** `POST /api/v2/consent/approve-signed`
**Authentication:** authenticated patient.

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

The patient ID must match the authenticated patient and challenge. Signing uses
UTF-8 JSON serialized with `sort_keys=true`, `separators=(",", ":")`, and
`ensure_ascii=false`, covering `access_duration`, `challenge_nonce`,
`decision`, `device_id`, `expires_at`, `issued_at`, `patient_id`,
`protocol_version` (`nexa-consent-v2`), `provider_id`, `purpose`,
`request_id`, and `scope`.

The backend verifies ECDSA P-256 over the prehashed SHA-256 digest with the
active, non-revoked `PatientDeviceKey` selected by `device_id`. No DEK is
used for signature verification. Private-key material is SecureStore-protected
on the current client; hardware-non-exportability is not established.

## Approved-access claim

**Endpoint:** `POST /api/v2/consent/{request_id}/claim-access`
**Authentication:** the provider who owns the approved request.

```json
{
  "patient_id": "...",
  "consent_token": "...",
  "purpose": "...",
  "scope": "...",
  "expires_at": "..."
}
```

Approval itself does not issue a provider token. This atomic claim succeeds at
most once. It is the routine boundary at which the provider learns and may use
the patient ID for consent-gated record operations.

## Retired and emergency routes

`POST /api/v2/consent/grant` and
`POST /api/v2/consent/routine/issue` return
`410 ROUTINE_DIRECT_ISSUANCE_RETIRED`; they are never reissue or recovery
routes.

`POST /api/v2/consent/break-glass/issue` is a separate emergency path. It
requires recent provider MFA, a controlled reason code, and justification; the
server grants an approved minimum-necessary scope for 15 minutes, audits the
event, and attempts patient notification. It is not a discovery-flow shortcut.

## Operational invariants

- A discovery handle creates at most one challenge.
- An approved request has at most one successful claim.
- Discovery and challenge storage are inert before audit; cleanup failure can
  leave bytes but never authority.
- Arbitrary UUID possession does not authorize routine access.
- Production cutover also requires legacy routine-capability purge or expiry
  before this invariant can be declared live.
