# Slice 6E — Lost Device + Account Recovery

Status: **implementation in qualification**

Baseline: post-Slice-6D `main` at `0db79938b339db57adf00844da03350a9f048c0a`.

## Security invariant

`VALID SUPABASE OTP + CURRENT PATIENT SESSION != FRESH DEVICE AUTHORITY`

Once any patient device history exists, the normal `/api/v2/patient/devices/enroll` path is bootstrap-ineligible. A patient must either authorize a new logical device from a currently active device private key or complete the explicit lost-device recovery flow.

## Trusted-device enrollment

A current patient session requests a short-lived challenge bound to the exact patient/session, authorizer logical device, authorizer key version, operation, and proposed new public-key fingerprint. The current active private key signs the canonical versioned payload. The backend rechecks the authorizer row under the patient advisory transaction lock before inserting the new logical device.

## All-devices-lost recovery

Recovery requires a second fresh Supabase OTP after ordinary patient login. The verified Supabase subject must exactly match the subject bound to the current Nexa patient session. A one-time Redis capability is then issued for at most one recovery attempt on that exact session and binds patient, session, upstream subject, operation, issuance, and expiry.

Recovery completion consumes that capability first, invalidates all old patient sessions by advancing the patient-wide session epoch, then transactionally revokes all currently active device keys with reason `ACCOUNT_RECOVERY` and inserts one fresh logical device/key. A fresh post-recovery patient session is issued only after the device transaction commits.

Historical private keys are never reconstructed, reactivated, or uploaded. Existing terminal key fingerprints cannot be resurrected.

## Cross-store ordering

The current ordering intentionally prioritizes stale-authority invalidation:

1. consume one-time recovery capability;
2. revoke all old patient sessions;
3. revoke old active device keys and install one fresh key in PostgreSQL;
4. issue a fresh patient session.

A later failure may require the patient to authenticate again. This is fail-closed behavior, not distributed ACID. Injected retry/idempotency/partial-failure qualification remains Slice 6G.

## Audit

6E reuses the existing governed event vocabulary:

- `PATIENT_SESSIONS_REVOKED` with `operation=account_recovery`;
- `DEVICE_KEY_REVOKED` with `reason_code=ACCOUNT_RECOVERY`;
- `DEVICE_KEY_ENROLLED` with `operation=account_recovery` or `trusted_device_enrollment`.

No OTP, recovery token, challenge nonce, signature bytes, JWT, Supabase access token, private key, or raw public key is written to audit metadata.

## Explicit non-claims

Slice 6E does not claim complete cross-store retry closure (6G), hardware-backed/non-exportable key custody (6H), native NFC (6H), Signed Consent V3 device/key-version binding (6F), or physical-device qualification (6I).
