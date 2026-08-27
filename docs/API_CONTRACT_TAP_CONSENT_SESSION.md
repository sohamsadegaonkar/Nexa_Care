# Nexa Care — Tap, Consent, and Access-Claim Contract

This document is the canonical routine provider-access contract. It supersedes
older automatic-token, push/respond, and biometric/verify descriptions.

## 1. NFC discovery

`POST /api/v2/nfc/resolve`

```json
{ "card_uid": "..." }
```

Success returns only:

```json
{ "discovery_handle": "...", "expires_at": "..." }
```

The 256-bit CSPRNG discovery capability has a 120-second TTL, is bound to the
authenticated provider, hospital, and session, and is single-use. It is staged
inert until the terminal audit event succeeds. It does not disclose a patient
UUID, public ID, masked ID, redirect chain, or clinical data.

## 2. Create an approval challenge

`POST /api/v2/consent/request`

```json
{
  "discovery_handle": "...",
  "purpose": "routine_checkup",
  "scope": "clinical",
  "access_duration_seconds": 900
}
```

`scope` is one string: `clinical`, `full`, or `documents`. Patient and
provider identities are server-derived; callers do not select them in this
request. The challenge has a 120-second TTL, begins in Redis as
`pending_audit`, emits `CONSENT_REQUEST_CREATED`, and is atomically
promoted to `pending` only after that audit succeeds. Notification is
attempted only after promotion.

## 3. Patient signed approval

`POST /api/v2/consent/approve-signed` receives
`request_id`, `patient_id`, `decision`, `challenge_nonce`,
`signature`, and `device_id`. The patient ID is checked against the
authenticated patient and challenge; it is not provider-selected authority.

The signed bytes are UTF-8 canonical JSON produced with
`sort_keys=true`, `separators=(",", ":")`, and `ensure_ascii=false`.
The fields are `access_duration`, `challenge_nonce`, `decision`,
`device_id`, `expires_at`, `issued_at`, `patient_id`,
`protocol_version`, `provider_id`, `purpose`, `request_id`, and
`scope`; `protocol_version` is `nexa-consent-v2`.

The backend verifies ECDSA P-256 over SHA-256 of these bytes using the active,
non-revoked `PatientDeviceKey` selected by `device_id`. A patient DEK is
not involved. The current client private key is protected by platform
SecureStore; hardware-non-exportability is not yet proven.

## 4. Provider status, claim, and record use

The provider polls `GET /api/v2/consent/status/{request_id}`. Approval does
not return a provider token. The provider instead calls:

`POST /api/v2/consent/{request_id}/claim-access`

The single successful claim returns `patient_id`, `consent_token`,
`purpose`, `scope`, and `expires_at`. A request is claimable at most once.
The provider learns and uses the patient ID only at this post-approval
boundary, then supplies the scoped capability to the relevant record endpoint.

## 5. Retired and emergency paths

`POST /api/v2/consent/grant` and
`POST /api/v2/consent/routine/issue` return
`410 ROUTINE_DIRECT_ISSUANCE_RETIRED`; neither is a recovery or reissue path.

Break-glass is a separate emergency path. It requires recent provider MFA, a
controlled reason code, and a justification; the server approves a
minimum-necessary scope for 15 minutes, audits the event, and attempts patient
notification. It does not follow discovery and does not imply full access.

## 6. Operational invariants

- Discovery capabilities and consent challenges are inert before audit.
- Cleanup failure can leave bytes, never authority.
- UUID possession alone cannot authorize routine access.
- One discovery handle creates at most one challenge; one approved request has
  at most one claim.
- Production cutover additionally requires legacy routine-capability purge or
  expiry before this invariant is declared operationally live.
