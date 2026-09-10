# Slice 9A — Reviewer Authorization Plan

Status: **DESIGN ESTABLISHED / IMPLEMENTATION NEXT / NOT QUALIFIED**

Base: `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`

Active branch: `slice-9a-registration-recovery-review`

## Finding

The existing document `identity_reviewer` authorization gate cannot be reused unchanged for patient registration recovery because it requires the reviewer's own live patient/hospital document-processing capability and therefore active patient consent. Account-recovery review must remain possible when patient account/device authority is unavailable, and the platform must not fabricate consent merely to unlock administrative repair.

## Dedicated authority contract

`registration_recovery_reviewer` is an administrative recovery-review authority, not a clinical capability.

A reviewer request must satisfy all of the following server-side conditions:

1. authenticate through the existing current provider bearer/cookie session dependency; Basic/password-only authentication is not sufficient for mutation routes;
2. resolve a current active provider identity and current active hospital affiliation from PostgreSQL;
3. require the server-owned affiliation role literal `registration_recovery_reviewer`;
4. require a live provider session whose Redis session context records a non-null MFA verification timestamp;
5. require that MFA proof is recent enough for the high-risk mutation window; initial policy is 15 minutes;
6. require the affiliation's `trust_status` to be `ACTIVE`, not merely `is_active=true`;
7. require the affiliation validity window to include the current server time;
8. reject missing/invalid session binding and authority backend failures closed;
9. audit denied reviewer operations with stable value-free reason codes.

The reviewer role does not itself grant clinical patient access, document-processing permission, consent authority, device authority, or patient-session authority.

## Operation classes

- Read/list case metadata: authenticated current provider session + server-owned reviewer role + active trusted affiliation.
- Claim/recover review session: same authority plus recent MFA.
- Terminal disposition/repair: same authority plus recent MFA and exact assigned-reviewer/session/version binding.

## Prohibited shortcuts

The following must never substitute for reviewer authority:

- frontend role state;
- a caller-supplied provider UUID or hospital UUID;
- patient OTP or patient access token;
- provider clinical capability;
- document consent capability;
- legacy `provider_identity.role` string by itself;
- possession of a case reference;
- a stale/expired provider session;
- Basic credentials on a high-risk mutation route.

## Qualification gates

Tests must prove missing role, inactive affiliation, non-ACTIVE trust status, expired affiliation, missing/stale MFA, wrong reviewer, wrong session binding, stale case version, and Redis/session-authority loss all fail closed. Successful reviewer authorization must still not create patient/device/consent authority.
