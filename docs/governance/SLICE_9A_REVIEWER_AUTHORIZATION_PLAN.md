# Slice 9A — Reviewer Authorization Plan

Status: **IMPLEMENTED / RE-ESTABLISHED / FINAL QUALIFICATION PENDING**

Base: `54351f9a55ba94665420961cfe766bdcc84a5398`

Active branch: `slice-9a-registration-recovery-review-r2`

## Finding

The existing document `identity_reviewer` authorization gate cannot be reused unchanged for patient registration recovery because it requires document-processing/consent authority. Account-recovery review must remain independent from patient consent and must not fabricate consent merely to unlock administrative repair.

## Dedicated authority contract

`registration_recovery_reviewer` is an administrative recovery-review authority, not a clinical capability.

A reviewer mutation must satisfy all of the following server-side conditions:

1. authenticate through the existing current provider bearer/cookie session dependency; Basic/password-only authentication is not sufficient;
2. bind the request to the exact live provider session;
3. resolve the current affiliation from PostgreSQL instead of trusting a stale context snapshot;
4. require the server-owned affiliation role `registration_recovery_reviewer`;
5. require a live provider session whose Redis context records MFA verification;
6. require MFA proof within the initial 15-minute high-risk mutation window;
7. require affiliation `trust_status=ACTIVE`, `is_active=true`, and a current validity window;
8. reject session, role, affiliation, MFA, or authority-backend failure closed;
9. audit denied reviewer operations with stable value-free reason codes.

The reviewer role does not itself grant clinical patient access, document-processing permission, consent authority, device authority, or patient-session authority.

## Operation classes

- Read/list case metadata: authenticated reviewer authority; route-specific read policy may relax recent-MFA while retaining live session and trusted affiliation.
- Claim/recover reviewer session: reviewer authority plus recent MFA.
- Terminal disposition/repair: reviewer authority plus recent MFA and exact assigned-reviewer/session/version binding.

## Prohibited shortcuts

Frontend role state, caller-supplied provider/hospital IDs, patient OTP/access tokens, clinical capability, document consent, legacy provider role strings, possession of a case reference, stale sessions, or Basic credentials cannot substitute for reviewer authority.

## Qualification gates

Tests must prove missing role, inactive/non-ACTIVE/expired affiliation, stale MFA, wrong provider/session binding, wrong reviewer, stale case version, and authority-backend loss fail closed. Successful reviewer authorization must still not create patient/device/consent authority.
