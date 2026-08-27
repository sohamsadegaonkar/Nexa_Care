# Nexa Care — Canonical Discovery, Consent, and Claim Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Provider as Authenticated clinician
    participant NFC as NFC resolve API
    participant Discovery as Discovery store
    participant Audit as Audit ledger
    participant Consent as Consent API
    participant Redis as Consent store
    actor Patient as Authenticated patient
    participant Device as Enrolled device public key
    participant Access as consent_access capability store

    Provider->>NFC: POST /nfc/resolve {card_uid}
    NFC->>Discovery: stage 256-bit handle (120s, pending audit)
    NFC->>Audit: NFC_CARD_RESOLVED
    Audit-->>NFC: success
    NFC->>Discovery: atomically activate handle
    NFC-->>Provider: discovery_handle, expires_at only

    Provider->>Consent: POST /consent/request {handle, purpose, scope, duration}
    Consent->>Discovery: consume bound handle once
    Consent->>Redis: store challenge pending_audit (120s)
    Consent->>Audit: CONSENT_REQUEST_CREATED
    Audit-->>Consent: success
    Consent->>Redis: atomic pending_audit -> pending
    Consent-->>Provider: request_id, pending status
    Consent-->>Patient: notification attempted after promotion

    Patient->>Patient: review challenge
    Patient->>Consent: approve-signed {request_id, patient_id, decision, nonce, signature, device_id}
    Consent->>Device: select active, non-revoked PatientDeviceKey
    Consent->>Consent: SHA-256(canonical UTF-8 JSON), ECDSA P-256 verify
    Consent->>Redis: resolve approved request
    Consent-->>Patient: approval status only

    Provider->>Consent: GET /consent/status/{request_id}
    Consent-->>Provider: approved status only
    Provider->>Consent: POST /consent/{request_id}/claim-access
    Consent->>Access: atomic one-time claim
    Access-->>Consent: patient_id, scoped consent_access capability
    Consent-->>Provider: patient_id, consent_token, purpose, scope, expires_at
```

The canonical JSON signing fields are `access_duration`, `challenge_nonce`,
`decision`, `device_id`, `expires_at`, `issued_at`, `patient_id`,
`protocol_version` (`nexa-consent-v2`), `provider_id`, `purpose`,
`request_id`, and `scope`; serialization uses `sort_keys=true`,
`separators=(",", ":")`, and `ensure_ascii=false`.

Discovery and consent challenges are inert before their audit transitions.
Failed cleanup can leave bytes but never authority. The direct routine issuer
routes are retired; a claim replay cannot be replaced by a direct routine
capability.

## Separate emergency sequence

Break-glass does not consume discovery. Recent provider MFA, a controlled reason
code, and justification are required before the server selects a
minimum-necessary emergency scope with a 15-minute TTL, audits it, and attempts
patient notification.
