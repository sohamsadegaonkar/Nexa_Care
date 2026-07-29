# Milestone 6.0 Physical Android Qualification

Status: **READY FOR EXECUTION — no device result recorded**

Use a Redmi Note 10 or another Android device with enrolled screen lock and biometrics. Use only synthetic patients and the controlled non-production pilot environment.

## Evidence header

Record before starting:

| Field | Value |
|---|---|
| Candidate commit SHA | |
| Backend deployment/version | |
| Frontend deployment/version | |
| Device model | |
| Android version | |
| Expo/application build identifier | |
| Tester | |
| Start/end timestamps with timezone | |
| Sanitized API host | |
| Sanitized Redis service/version | |
| Sanitized PostgreSQL service/version | |
| Unique Redis test prefix | `nexa:m6:<run-id>:` |

Never record passwords, OTP seeds, OTP values, bearer/cookie values, consent capabilities, private/public key material, signatures, push tokens, document URLs, clinical values, names, phone numbers, or complete connection URLs.

For every step record: timestamp, result (`PASS`/`FAIL`), HTTP status, stable safe error code, relevant audit event ID, and Redis TTL in seconds where applicable. Capture screenshots only after confirming they contain no credentials, tokens, identifiers, or health data.

## Preconditions

- Confirm the candidate SHA matches the intended Milestone 6 worktree.
- Confirm all patients and clinical content are synthetic.
- Confirm backend readiness without printing dependency credentials.
- Confirm the Android build points to the sanitized pilot API host over the intended secure transport.
- Confirm one clinician account has MFA and one synthetic patient can authenticate.
- Confirm the test patient has no active key for this installation, or revoke only the test-owned installation key.
- Do not flush Redis. Delete only keys bearing the run prefix.

## Routine access sequence

| # | Action and expected result | Evidence |
|---|---|---|
| 1 | Sign in as the synthetic patient using the approved OTP test mechanism. Complete “Secure This Device.” Expect one active device row whose stored material is public-key-only. Verify no private key or signature appears in API bodies, logs, audit metadata, URLs, or screenshots. | Status/code; device audit ID; backend column/API inspection stating public-key-only |
| 2 | Sign in as Doctor A with password and MFA. Confirm the displayed provider name, hospital, and roles match the server session response. | Login/MFA statuses; safe audit IDs |
| 3 | Resolve the synthetic patient using manual card/identifier entry. Confirm the canonical patient identifier is resolved server-side. | Status/code; no raw identifier in URL/log evidence |
| 4 | Request the minimum routine scope for a controlled purpose and duration. Record pending-request TTL without recording the capability or nonce. | Status/code; request audit ID; TTL |
| 5 | With the app backgrounded, verify physical push receipt. Tap it and confirm the deep link opens only the matching consent request after authentication. | Device timestamp; notification delivery status; no token in deep link |
| 6 | Review the provider, hospital, purpose, scope, and duration. Approve through the physical biometric prompt. Confirm the backend accepts the canonical P-256 signature against the enrolled public key. | Status/code; approval audit ID; biometric observation |
| 7 | Confirm Doctor A receives only the granted record categories. An ungranted category must return `403` with a stable value-free code. | Allowed and denied statuses/codes; access audit IDs |
| 8 | Wait for a short granted-consent expiry. Repeat the previously allowed request and require `403`; protected UI state must clear. | TTL before expiry; denial status/code/audit ID |
| 9 | Create another request and deny it on the patient device. Doctor polling must reach a terminal denied state and no record access may be granted. | Denial and subsequent access status/code/audit IDs |
| 10 | Allow a pending request to expire without acting. Patient and doctor screens must show terminal expiry and no capability may be issued. | TTL; expiry status/code/audit ID |
| 11 | Approve another request, then revoke it from the patient UI. The next Doctor A access must return `403` and protected state must clear. | Revoke/access statuses and audit IDs |
| 12 | Attempt to reuse Doctor A’s grant from Doctor B. Require `403` without exposing either provider or capability details. | Status/code and denial audit ID |
| 13 | Revoke this test device key. Attempt approval using its retained local key and require rejection. | Revoke and approval statuses/codes; audit IDs |
| 14 | Disable notification permission or use the controlled push-unavailable condition. Confirm the doctor sees `failed`/`unavailable` honestly and the patient can open the pending request through the authenticated in-app fallback. | Delivery state; fallback result |
| 15 | Open patient access history and confirm the successful routine read appears once with correct provider/hospital fallback labels. Confirm denied/self-access noise is absent. | Visible audit event ID and device screenshot if sanitized |
| 16 | Log Doctor A out. Require successful server-side session deletion. Replay a request with the old test session only through an approved diagnostic client and require `401`. If Redis revocation is unavailable, logout must return `503 PROVIDER_SESSION_REVOCATION_UNAVAILABLE`, never false success. | Logout/replay statuses and audit ID |

## Emergency sequence

| # | Action and expected result | Evidence |
|---|---|---|
| 17 | Reauthenticate Doctor A with MFA. Submit an invalid reason, insufficient justification, and over-broad requested scope separately. Require validation rejection without a grant. | Each status/code |
| 18 | Submit a valid controlled reason and justification. Confirm the granted scope is the policy intersection, the emergency banner is unmistakable, and the general record endpoint rejects the emergency capability. | Status/code; granted category names only; audit ID |
| 19 | Read the dedicated minimal emergency summary. Confirm only permitted safety categories appear. | Status and category names; no values in this report |
| 20 | Revoke or expire the emergency grant and require subsequent denial. | TTL; revoke/expiry status/code and audit IDs |
| 21 | Refresh patient access history and confirm the emergency access is visibly distinguished and linked to the correct safe audit evidence. | Audit event ID; sanitized screenshot |

## Final privacy inspection

Search runtime logs, audit metadata, browser history/storage, Expo logs, captured request URLs, and screenshots. Record only counts:

| Forbidden class | Required count |
|---|---:|
| Credentials, OTP values/seeds, bearer/cookie values | 0 |
| Consent capabilities, session values, nonces, signatures, key material | 0 |
| Clinical values or source/document contents | 0 |
| Signed/document URLs | 0 |
| Sensitive values in query strings, fragments, analytics, browser storage | 0 |

## Stop conditions

Stop immediately on cross-provider access, revoked-device acceptance, consent access after expiry/revocation, emergency full-record access, missing required audit evidence, leaked sensitive material, fabricated push success, or any use of real patient data.

The milestone remains `READY_FOR_DEVICE_TEST` until every row has human-supplied evidence. Automated tests cannot mark this runbook passed.

