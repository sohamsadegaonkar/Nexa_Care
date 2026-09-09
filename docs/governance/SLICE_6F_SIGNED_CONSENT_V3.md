# Slice 6F — Signed Consent V3

Status: implementation complete; exact-head qualification pending.

## Security boundary

Signed Consent V3 is a new, explicit protocol domain. It does not mutate the legacy V2 signing bytes in place.

Core invariant:

`VALID LEGACY V2 SIGNATURE != CURRENT V3 CONSENT AUTHORITY`

Newly created V2 challenges are tagged as `nexa-consent-v2` and cannot mint a new approved-access capability after the V3 cutover. The legacy claim path returns `410 SIGNED_CONSENT_V2_ACCESS_RETIRED` with `upgrade_protocol = nexa-consent-v3`.

## Canonical V3 decision binding

The V3 canonical decision payload binds all of the following values:

- protocol version `nexa-consent-v3`
- domain `NEXA_CARE_SIGNED_CONSENT`
- operation `CONSENT_DECISION`
- request ID
- patient ID
- provider ID
- hospital/facility ID
- challenge nonce
- decision
- purpose
- scope
- access duration
- issued timestamp
- expiry timestamp
- consent context hash
- stable logical device ID
- immutable device-key row ID
- exact key version
- public-key fingerprint

The consent context hash is SHA-256 over the immutable server-created request context and is checked before signature verification to reject field substitution.

## Device authority

V3 approval is verified only against the exact currently-active P-256 device-key version bound by:

- patient ID
- logical device ID
- key row ID
- key version
- public-key fingerprint
- active status with no revocation timestamp

A replaced, revoked, compromised, foreign, stale-version, or otherwise non-current key cannot satisfy the V3 verifier. Rotation or revocation of the approving key therefore invalidates later authority that depends on that key remaining current.

The server stores only the enrolled public key. This slice does not claim hardware-backed, non-exportable, Secure Enclave, StrongBox, or physical-device properties.

## Provider trust remains live

Signed patient intent is necessary but not sufficient for access. Provider-side authority is re-evaluated from the authoritative provider trust graph at approval and again before capability claim. Professional verification, facility verification, affiliation trust, and clinical capability can therefore invalidate access even after a patient signed the request.

## Routes

V3 uses explicit endpoints:

- `POST /api/v2/consent/v3/request`
- `GET /api/v2/consent/v3/challenge/{request_id}`
- `POST /api/v2/consent/v3/approve-signed`
- `POST /api/v2/consent/v3/{request_id}/claim-access`

Current client consent request/signing code targets these V3 contracts.

## Qualification evidence implemented

The branch contains:

- field-by-field anti-substitution/domain-separation tests for the V3 canonical payload;
- real P-256 signature verification against exact device-key versions;
- production-shaped provider -> NFC discovery -> patient signature -> claim -> record-access E2E coverage on V3;
- post-consent provider professional, facility, affiliation and capability invalidation checks;
- V2 downgrade-retirement assertions proving newly tagged V2 requests cannot mint post-cutover capabilities;
- route registration and mobile/client contract tests for the explicit V3 endpoints.

Exact-head Backend CI partitions A/B/C with zero skips and Frontend CI must pass before this slice is marked qualified or the PR is made ready for merge.

## Nonclaims

Slice 6F does not claim:

- that a signature alone grants clinical access;
- that patient consent overrides live provider/facility/affiliation/capability revocation;
- that legacy V2 signatures are equivalent to V3 authority;
- hardware-backed or non-exportable private-key storage;
- native Secure Enclave/StrongBox qualification;
- physical-device or physical-pilot qualification;
- completion of Slice 6G cross-boundary failure qualification.
