# Slice 9A UI Handoff Contract

Status: **backend contract; merge subject to PR #43 exact-head qualification**

This note is the compact consumer contract for the UI work that follows Slice 9A. Runtime schemas and server code remain authoritative.

## Patient-facing flow

The existing patient registration-recovery endpoints remain:

- `POST /api/v2/auth/registration-recovery/otp/send`
- `POST /api/v2/auth/registration-recovery/otp/verify`
- `POST /api/v2/auth/registration-recovery/complete`

When verified recovery classification requires manual review, OTP verification returns HTTP `409` with:

- `error_code = REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED`
- opaque durable `case_reference`

The UI must not treat `case_reference` as a bearer repair credential, login token, device credential, consent token, provider subject, or patient UUID.

Patient status is:

- `GET /api/v2/auth/registration-recovery/review/cases/{case_reference}`

Response fields:

- `case_reference`
- `status`: `PENDING | IN_REVIEW | RESOLVED | REJECTED | SECURITY_ESCALATED`
- `terminal`: boolean
- `next_action`: `WAIT_FOR_REVIEW | RESTART_ACCOUNT_RECOVERY | CONTACT_SUPPORT`
- `created_at`
- `resolved_at` nullable

The patient status response intentionally contains no graph fingerprint, reviewer identity, provider subject, internal reason code, patient UUID, reviewer session binding, repair token, access token, device token, or consent authority.

UI behavior:

- `PENDING` / `IN_REVIEW` -> show review in progress and poll only by the opaque case reference;
- `RESOLVED` + `RESTART_ACCOUNT_RECOVERY` -> send the patient through the ordinary registration-recovery/authentication flow again; do not assume the review response logged the patient in;
- `REJECTED` or `SECURITY_ESCALATED` + `CONTACT_SUPPORT` -> terminal support/escalation state; do not offer a generic retry that could imply repair authority.

## Reviewer flow

Reviewer routes are independently authorized by the backend and must never be unlocked by a frontend role string alone:

- `GET /api/v2/auth/registration-recovery/review/reviewer/cases`
- `GET /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}`
- `POST /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}/claim`
- `POST /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}/recover-session`
- `POST /api/v2/auth/registration-recovery/review/reviewer/cases/{case_reference}/resolve`

Claim/session-recovery requests carry `expected_version`. Terminal resolution carries `expected_version`, a durable `idempotency_key`, one closed `outcome`, and closed `reason_codes`.

Reviewer responses never provide patient login, device, or consent authority. The backend remains authoritative for assignment, current session binding, recent MFA, live affiliation, optimistic version, graph revalidation and repair policy.

## UI security invariants

- Never place recovery attempt/capability tokens in URLs, navigation state, localStorage, logs or analytics.
- Treat the case reference as an opaque support/workflow identifier, not proof of identity.
- Do not render internal UUIDs or reviewer metadata on the patient status screen.
- Do not infer that a terminal review repaired the account unless `status=RESOLVED` and `next_action=RESTART_ACCOUNT_RECOVERY`; even then, the patient must authenticate/recover normally.
- Do not create frontend-only repair outcomes or reason codes.
- Preserve stable error codes from the server and show explicit loading, in-review, terminal, stale-version/session and unavailable states.
