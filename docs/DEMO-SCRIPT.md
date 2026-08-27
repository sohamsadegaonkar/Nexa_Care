# Nexa Care — Consent Security Demonstration

This rehearsal demonstrates the implemented routine flow. It is not evidence
of a production or physical-device deployment.

## Routine consent

1. A provider taps a card and calls `POST /api/v2/nfc/resolve`.
2. The scanner receives only `discovery_handle` and `expires_at`, never a
   patient identifier or record.
3. The provider creates a request with that handle, purpose, one string scope,
   and requested duration. The 120-second challenge starts `pending_audit`;
   only an audit-successful promotion makes it usable or eligible for
   notification.
4. The patient reviews the challenge and signs the canonical JSON challenge
   context with the enrolled device key. The backend verifies ECDSA P-256 over
   the prehashed SHA-256 digest using the active, non-revoked device public key.
   A patient DEK is not part of this verification.
5. The provider observes approved status, then explicitly calls
   `POST /api/v2/consent/{request_id}/claim-access`. The single claim returns
   the patient ID and scoped consent capability used for the record request.

## Negative cases

- A second use of a discovery handle cannot create another challenge.
- A second claim for an approved request fails; no replacement capability is
  issued by the routine flow.
- A raw patient UUID cannot create routine authorization.
- The direct routes `/consent/grant` and `/consent/routine/issue` return
  `410 ROUTINE_DIRECT_ISSUANCE_RETIRED`.

## Break-glass

Break-glass is demonstrated separately from NFC discovery. It requires recent
provider MFA, a controlled reason code, and a justification. The server grants
only the approved minimum-necessary emergency scope for 15 minutes, records an
audit event, and attempts patient notification. It is not a routine-flow
fallback and does not promise full access.
