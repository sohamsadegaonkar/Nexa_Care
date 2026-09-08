# Slice 6D — Device Key Rotation

Status: **implementation in qualification**

Baseline: post-Slice-6C `main` at `3906fe0dc68d81e16dd0dd7066be6e73b32fd7e8`.

## Security invariant

`CURRENT PATIENT SESSION + ACTIVE DEVICE ROW != AUTHORITY TO ROTATE`

Normal rotation additionally requires proof from the **current private key** over a one-time, versioned, server-issued challenge bound to the exact patient session, logical device, current key version, operation, and replacement-key fingerprint.

The backend accepts and stores public keys only. It never accepts, reconstructs, exports, or recovers the patient's private key.

## Protocol

Protocol version: `nexa-device-key-rotation-v1`.

Operation: `rotate_device_key`.

A rotation challenge is valid for 120 seconds and is stored in Redis under a random high-entropy nonce. The Redis record binds:

- patient UUID;
- exact current patient `session_id`;
- stable logical `device_id`;
- current `key_version`;
- operation;
- replacement public-key SHA-256 fingerprint;
- issued and expiry timestamps;
- protocol version;
- SHA-256 of the canonical signing payload.

The canonical signed payload includes the same security context, using a SHA-256 session binding rather than exposing the raw session identifier in the signed JSON.

The mobile/client signer signs the SHA-256 digest of the canonical JSON using the currently active ECDSA P-256 private key. Backend verification uses the current active public-key row only.

## Challenge one-time semantics

Challenge issuance revalidates the exact live patient session in Redis. Challenge consumption revalidates that session again and then atomically performs Redis `GET + DEL` through Lua. Only one concurrent consumer can receive the challenge record.

A missing, replayed, expired, wrong-patient, wrong-session, wrong-device, wrong-version, wrong-operation, or wrong-new-key challenge fails closed. Redis/session-authority failure returns an availability failure; there is no JWT-only rotation fallback.

## Transactional version advance

`rotate_patient_device_key()` acquires the same per-patient PostgreSQL advisory transaction lock used by enrollment. It then:

1. locks the one current active row for the patient-owned logical device;
2. requires the exact expected current key version;
3. verifies the old-key signature before any authority mutation;
4. canonicalizes and globally rechecks replacement-key ownership;
5. marks the old row terminal `replaced`, with `revoked_at`, `KEY_ROTATED`, and patient-current-device actor context;
6. flushes that terminal transition before inserting the replacement so the partial unique active-version index is never violated;
7. inserts the next immutable key version under the same stable `device_id`;
8. writes bidirectional `replaces_key_id` / `replaced_by_key_id` lineage;
9. stages lifecycle audit events in the same transaction.

The old row is never deleted. A replaced/revoked/compromised key fingerprint cannot be resurrected as new authority.

## Audit

Rotation deliberately reuses the established lifecycle vocabulary rather than introducing a parallel event namespace:

- old current key: `DEVICE_KEY_REVOKED`, reason `KEY_ROTATED`, operation `device_key_rotation`;
- new current version: `DEVICE_KEY_ENROLLED`, operation `device_key_rotation`.

Both success events are staged through the PostgreSQL transactional audit outbox. If outbox staging fails, the authority mutation rolls back.

Denied route attempts use `DEVICE_KEY_ENROLLED` with `DENIED`, `operation=device_key_rotation`, and a server-owned reason code. No private key, signature bytes, challenge secret, JWT, OTP, or raw public-key bytes are written to audit metadata.

## Cross-store ordering

The rotate route consumes the one-time Redis challenge **before** the durable PostgreSQL mutation. Therefore a later PostgreSQL failure may leave `challenge consumed + no rotation`, which is intentionally safer than `rotation committed + reusable challenge`.

This is fail-closed behavior, not distributed ACID. Exact retry/idempotency/partial-failure qualification across Redis and PostgreSQL is assigned to Slice 6G.

## Qualification targets

Pure-unit qualification covers:

- canonical payload field binding and protocol version;
- exact one-time challenge use and replay rejection;
- wrong patient/session/device/version/new-fingerprint binding;
- correct-old-key signature success;
- wrong-key and tampered-payload signature rejection;
- Redis-unavailable fail-closed behavior;
- route binding to the authoritative patient session and current key version;
- challenge consumption before DB mutation.

Real Redis qualification covers:

- exact-session binding;
- cross-binding rejection;
- one winner under concurrent challenge consumption;
- replay rejection;
- revoked-session denial;
- expiry denial;
- Redis outage fail-closed behavior.

Real PostgreSQL qualification covers:

- version 1 -> 2 transition and exact lineage;
- terminal old-key state and one active current version;
- immediate invalidation of the old key after rotation;
- concurrent double-rotation with exactly one version-2 winner;
- cross-patient replacement-key ownership denial;
- terminal-key resurrection denial;
- revoked and foreign logical-device denial;
- transactional audit-outbox rollback semantics.

## Explicit non-claims

Slice 6D does **not** provide a lost-private-key bypass. If the current private key is unavailable, normal rotation cannot proceed; recovery is Slice 6E.

Slice 6D also does not claim:

- all-devices-lost/account recovery (6E);
- Signed Consent V3 device/key-version binding (6F);
- complete cross-store race/partial-failure closure (6G);
- hardware-backed or non-exportable private-key custody (6H);
- native NFC (6H);
- physical-device qualification (6I).

`expo-secure-store` or backend P-256 validation is not hardware attestation.
