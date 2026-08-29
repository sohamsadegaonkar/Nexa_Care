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

## Qualification status

This flow is **ALPHA**. Integration validation pending while deployed HTTPS
and physical-device qualification remain unfinished. Known gaps are
qualification and deployment gaps, not authorization bypasses.

Provider roles come from authenticated server/provider context. Client UI,
navigation, and role state are never authorization; every request is evaluated
by the server against the authenticated provider and the relevant capability.
`nexa-client` is the canonical production frontend and applies Zod runtime
validation to backend responses.

The current screen map is `DoctorLoginScreen` → `DoctorDashboardScreen` →
`PatientSearchScreen` → `RequestConsentScreen` →
`WaitingForApprovalScreen` → `PatientRecordViewerScreen`; emergency access is
handled by `EmergencyAccessScreen`. `ProviderAuthContext` carries the
authenticated provider session. The client does not treat `provider_id` as a
request authority; the server derives it from that authenticated context.

Discovery handles and access capabilities are memory-scoped and must not be
persisted across reloads. Logout invokes the server logout path; server-side
session invalidation remains a server responsibility.

The waiting view uses adaptive polling with backoff. The provider can cancel a
pending consent request. Routine consent creation uses controlled purpose and
scope values, and server-derived identity rejects mismatches (an IDOR guard).
The remaining qualification milestone is a deployed end-to-end live flow with
HTTPS and a physical device; it does not alter these authorization controls.
