# Nexa Care — Doctor Application Flow

## Routine patient access

1. An authenticated clinician scans a card and calls
   `POST /api/v2/nfc/resolve`.
2. The client retains only `discovery_handle` and `expires_at` in memory.
   It must not render, persist, or infer a patient ID from discovery.
3. The clinician calls `POST /api/v2/consent/request` with the handle,
   purpose, one string scope (`clinical`, `full`, or `documents`), and
   requested duration. The backend derives provider and patient identity.
4. The waiting screen polls owner-scoped consent status. Approval is not a
   token response.
5. On approved status, the client calls
   `POST /api/v2/consent/{request_id}/claim-access` exactly once. Only this
   response provides `patient_id` and the scoped `consent_token`.
6. Record requests use the claimed identifier and capability with the provider
   session. UI state is never authorization.

Navigation to a patient-record screen is not authorization; every record
request remains independently authenticated, capability-checked, and audited.

## Security rules

- Discovery capability is 120-second, provider/hospital/session-bound, and
  single-use; it is audit-gated before disclosure.
- Consent challenge is 120-second and inert while `pending_audit`; the server
  promotes it to `pending` only after `CONSENT_REQUEST_CREATED` succeeds.
- Do not send `patient_id` or `provider_id` in routine creation.
- Do not save handles or capabilities in URLs, persistent browser storage, or
  logs.
- A failed or replayed claim must not be retried by requesting a direct routine
  token; direct issuers return `410 ROUTINE_DIRECT_ISSUANCE_RETIRED`.
- The provider uses the patient identifier only post-approval and post-claim.

## Break-glass

Emergency access does not use the discovery flow. It requires recent provider
MFA, a controlled reason code, and justification. The server selects a
minimum-necessary emergency scope for 15 minutes, audits access, and attempts
patient notification. The client must not present it as full-record access.
