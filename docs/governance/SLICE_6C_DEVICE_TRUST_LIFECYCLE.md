# Slice 6C — Device Trust Schema + Enrollment Hardening

Status: **qualification in progress**

Baseline: post-Slice-6B `main` at `008b1f2fb1f18e5458208a6000f5a218955a1e66`.

## Security invariant

`ENROLLED DEVICE ROW != CURRENT CRYPTOGRAPHIC DEVICE AUTHORITY`

Slice 6C makes patient device trust an explicit, versioned server-owned lifecycle. A cryptographic key is represented by an immutable key-version row; a logical device has a stable `device_id` that survives future rotation.

## Schema contract

Migration `20260909_device_trust_lifecycle` adds:

- stable server-owned `device_id`;
- positive `key_version`;
- SHA-256 `public_key_fingerprint` over canonical SubjectPublicKeyInfo DER;
- terminal lifecycle states `active`, `revoked`, `replaced`, `compromised`;
- `revocation_reason_code` and `revocation_actor`;
- `replaces_key_id` / `replaced_by_key_id` lineage;
- global unique fingerprint index;
- unique `(device_id, key_version)`;
- PostgreSQL partial unique index permitting only one active key version per logical device;
- lifecycle check constraints requiring terminal rows to have `revoked_at`.

Existing rows are backfilled as version 1 with `device_id = id`. No historical row is deleted.

## Canonical key ownership

Production enrollment parses the submitted DER public key, requires ECDSA P-256, reserializes it as DER SubjectPublicKeyInfo, and fingerprints the canonical bytes. The canonical bytes—not the client byte string—are persisted for new enrollments.

The fingerprint is globally unique across the table. The same key material therefore cannot become authority for two patients or two unrelated logical devices. A terminal fingerprint is never reactivated; attempting to enroll it again fails with `DEVICE_KEY_RESURRECTION_FORBIDDEN`.

Patient private keys are never accepted, reconstructed, encrypted, or stored by the backend.

## Concurrent five-device invariant

Application-level count-then-insert is removed from the production route. `enroll_patient_device_key()` acquires a PostgreSQL transaction advisory lock derived from the authoritative patient UUID before checking the active-device count and inserting version 1. Starting from four active devices, concurrent distinct enrollments serialize; only one may commit the fifth.

Global fingerprint uniqueness remains database-enforced separately, so two patients racing the same public key cannot both commit ownership even though their per-patient advisory locks differ.

## Enrollment grant ordering

The exact current patient session is still required by the 6B dependency and enrollment grant.

The route orders the cross-store operation as:

1. validate/canonicalize the public key;
2. claim the exact-session Redis enrollment grant;
3. atomically finalize/consume the Redis grant;
4. execute the PostgreSQL device + audit-outbox transaction.

This deliberately prefers `grant consumed, no device` if PostgreSQL later fails. It avoids the more dangerous state `device committed, Redis grant not finalized`. Ambiguous retry/compensation behavior under injected Redis/PostgreSQL failures remains a 6G qualification target; 6C does not claim distributed ACID.

## Revocation

Device list and revoke routes now require the strict current patient-session dependency rather than the legacy scoped-session fallback.

Revocation targets the server-owned logical `device_id`, rechecks patient ownership in PostgreSQL, locks the active row, and transitions it terminally to `revoked` with `revoked_at`, reason code, and actor context. Another patient cannot revoke the device. A terminal row is not reactivated by the service.

## Audit

Successful enrollment and revocation stage `DEVICE_KEY_ENROLLED` / `DEVICE_KEY_REVOKED` through the transactional audit outbox in the same PostgreSQL transaction as the authority mutation.

Denied invalid/duplicate/resurrection/limit enrollments use the existing `DEVICE_KEY_ENROLLED` event vocabulary with `DENIED` status and a server-owned reason code. Audit metadata never includes public-key bytes, private keys, JWTs, OTPs, enrollment tokens, or Redis claim identifiers.

## Qualification tests

`tests/integration/test_patient_device_trust_postgres.py` uses a disposable real PostgreSQL database migrated to `20260909_device_trust_lifecycle`. It covers:

- migration columns and indexes;
- canonical version-1 persistence;
- transactional enrollment audit outbox;
- four-to-five concurrent distinct-key enrollment race;
- identical-key same-patient race;
- identical-key cross-patient race;
- terminal revocation and resurrection denial;
- cross-patient revoke denial;
- database rejection of an invalid terminal-to-active direct mutation.

The repository migration graph, CI shared-database migrator, pilot migration runner, and qualification-infrastructure default head are advanced to the new single head.

Final GitHub Actions evidence will be recorded in the PR only after the final code/test head is green across Ruff, A, B, C, and Frontend CI.

## Explicit non-claims

Slice 6C does not claim:

- device key rotation protocol or proof-of-possession challenge (6D);
- account recovery or all-devices-lost handling (6E);
- Signed Consent V3 facility/key-version binding (6F);
- closure of all Redis/PostgreSQL operation-pair races (6G);
- hardware-backed or non-exportable mobile private-key custody (6H);
- native NFC (6H);
- physical-device qualification (6I).

`SecureStore` is not treated as evidence of hardware-backed custody. Backend P-256 validation is not hardware attestation.