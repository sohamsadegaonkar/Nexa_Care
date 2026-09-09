# Slice 6E — Lost Device + Account Recovery

Status: **implementation qualified on `e52afd55774e3dfddcab13647fa4fcc9033846e3`; final documentation-head CI remains a merge gate**

Baseline: post-Slice-6D `main` at `0db79938b339db57adf00844da03350a9f048c0a`.

## Security invariant

`VALID SUPABASE OTP + CURRENT PATIENT SESSION != FRESH DEVICE AUTHORITY`

Once any patient device history exists, the normal `/api/v2/patient/devices/enroll` path is bootstrap-ineligible. Ordinary OTP authentication may establish account/session authority, but it does not mint a bootstrap device-enrollment grant for an account with device history. Such a patient must either authorize a new logical device from a currently active device private key or complete the explicit lost-device recovery flow.

The OTP response therefore models device authority separately:

- `device_enrollment_token` is nullable;
- `device_authority_state=bootstrap_enrollment` is limited to a patient with no device history;
- `device_authority_state=existing_device_required` carries account authentication without fresh device authority.

The bootstrap route independently checks device history as a server-side defense in depth.

## Trusted-device enrollment

A current patient session requests a short-lived challenge bound to the exact patient/session, authorizer logical device, authorizer key version, operation, and proposed new public-key fingerprint. The current active private key signs the canonical versioned payload. The backend rechecks the authorizer row under the patient advisory transaction lock before inserting the new logical device.

A bearer token without the currently active private-key proof cannot authorize the new device. Revoked/replaced/compromised authorizer keys do not satisfy the proof requirement.

## All-devices-lost recovery

Recovery requires a second fresh Supabase OTP after ordinary patient login. The verified Supabase subject must exactly match the subject bound to the current Nexa patient session. A one-time Redis capability is then issued for at most one recovery attempt on that exact session and binds patient, session, upstream subject, operation, proposed new-key fingerprint, issuance, and expiry.

Recovery completion consumes that capability first, invalidates all old patient sessions by advancing the patient-wide session epoch, then transactionally revokes all currently active device keys with reason `ACCOUNT_RECOVERY` and inserts one fresh logical device/key. A fresh post-recovery patient session is issued only after the device transaction commits.

Historical private keys are never reconstructed, reactivated, or uploaded. Existing terminal key fingerprints cannot be resurrected.

## Client authority handling

The client can persist a valid patient account session without a bootstrap enrollment token and deletes any stale stored bootstrap grant when the server returns account-only authority.

Before attempting enrollment it reconciles the local public-key fingerprint against active server device rows. A uniquely matching active device repairs a stale/missing local logical `device_id` without creating new authority. If server device history exists but no local active fingerprint matches and no bootstrap grant exists, the client surfaces `RECOVERY_REQUIRED` and directs the patient to trusted-device authorization or account recovery instead of attempting ordinary bootstrap enrollment.

SecureStore usage is not evidence of hardware-backed or non-exportable key custody; that remains Slice 6H.

## Cross-store ordering

The current ordering intentionally prioritizes stale-authority invalidation:

1. consume the one-time recovery capability;
2. revoke all old patient sessions;
3. revoke old active device keys and install one fresh key in PostgreSQL;
4. issue a fresh patient session.

A later failure may require the patient to authenticate again. This is fail-closed behavior, not distributed ACID. Injected retry/idempotency/partial-failure qualification remains Slice 6G.

## Persistence and migration

Slice 6E requires no new schema migration. It uses the versioned patient-device lifecycle introduced by Slice 6C and qualified through Slice 6D. The disposable qualification database migrated successfully to Alembic head `20260909_device_trust_lifecycle`.

## Audit

6E reuses the existing governed event vocabulary:

- `PATIENT_SESSIONS_REVOKED` with `operation=account_recovery`;
- `DEVICE_KEY_REVOKED` with `reason_code=ACCOUNT_RECOVERY`;
- `DEVICE_KEY_ENROLLED` with `operation=account_recovery` or `trusted_device_enrollment`.

No OTP, recovery token, challenge nonce, signature bytes, JWT, Supabase access token, private key, or raw public key is written to audit metadata.

## Adversarial and lifecycle qualification

The Slice 6E test set covers the authority and failure boundaries introduced here, including:

- bootstrap denial after any device history exists;
- account authentication without automatic device authority;
- trusted-device proof binding to patient/session/device/key version/new fingerprint;
- invalid/revoked authorizer state and proof mismatch;
- one-time recovery capability binding, replay denial, expiry, wrong patient/session/upstream subject/new fingerprint, and Redis fail-closed behavior;
- old-session invalidation during recovery;
- transactional revocation of old active device keys plus fresh-key installation;
- terminal fingerprint resurrection denial and cross-patient/global key ownership constraints;
- client fingerprint rediscovery and recovery-required behavior when device history exists without a local active match;
- stale mocked route harnesses explicitly model no prior device history without weakening the production guard.

## Implementation-head qualification evidence

Authoritative implementation qualification head: `e52afd55774e3dfddcab13647fa4fcc9033846e3`.

Backend CI #298, run `34311464076`, completed successfully on that exact implementation tree:

- Ruff: `All checks passed!`;
- Partition A — Quality & Pure Unit: `3619 passed, 390 deselected, 203 warnings in 60.72s`; JUnit failures `0`, errors `0`, skipped `0`;
- Partition B — PostgreSQL Qualification: `272 passed, 3737 deselected, 13 warnings in 72.06s`; JUnit failures `0`, errors `0`, skipped `0`;
- Partition C — PostgreSQL + Redis Qualification: `118 passed, 3891 deselected, 21 warnings in 58.24s`; JUnit failures `0`, errors `0`, skipped `0`;
- all three explicit zero-skip qualification steps passed.

Frontend CI #247, run `34311464070`, completed successfully on the same implementation head: frontend tests, Next production build, and workspace package build all passed.

A preceding bot-authored same-tree commit produced GitHub Actions `action_required` records with zero jobs; those records are not qualification evidence. `e52afd55774e3dfddcab13647fa4fcc9033846e3` is the user-authored same-tree qualification head on which the actual CI matrix executed.

At implementation qualification time PR #15 had no submitted reviews and no inline review threads. Review hygiene and all CI gates must be rechecked on the final documentation head before merge.

## Merge gate

The governance attestation itself moves the branch head. Therefore Slice 6E is not merge-qualified until the exact documentation head again satisfies:

- Backend CI success;
- Ruff success;
- Partitions A/B/C success with each zero-skip assertion passing;
- Frontend CI success;
- no unresolved review threads or change requests;
- PR head unchanged between final qualification and merge.

## Explicit non-claims

Slice 6E does not claim complete cross-store retry/compensation closure (Slice 6G), Signed Consent V3 device/key-version binding (Slice 6F), hardware-backed or non-exportable key custody (Slice 6H), native NFC behavior (Slice 6H), or physical-device qualification (Slice 6I).

This qualification does not claim that Nexa Care is "fully secure." It establishes only the tested Slice 6E lost-device and account-recovery authority properties described above.
