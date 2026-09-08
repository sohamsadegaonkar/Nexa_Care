# Slice 6B — Patient Session Authority

Status: **qualification in progress**

This document records the executable security contract implemented by Slice 6B. It deliberately distinguishes cryptographically valid patient JWTs from current server-side patient authority.

## Security invariant

`VALID PATIENT JWT != CURRENT PATIENT AUTHORITY`

Current patient authority requires all of the following to succeed at request time:

1. the patient JWT is cryptographically valid and identifies a patient actor;
2. the JWT contains an opaque `sid` and non-negative integer `session_epoch`;
3. the exact `sid` resolves to an active Redis session record;
4. the session record is bound to the same patient, Supabase subject, and session epoch;
5. the patient-wide Redis epoch equals the JWT/session epoch;
6. the PostgreSQL patient remains active;
7. the corresponding `PatientAuthIdentity` remains active and bound to the same Supabase subject.

Failure to prove any required Redis or PostgreSQL fact denies authority.

## Redis authority model

Patient session identifiers are generated server-side with high entropy. Redis keys use a SHA-256 digest of the opaque session identifier rather than the raw identifier. Session records are TTL-bounded by the JWT expiry.

A patient-wide session epoch is stored separately. `logout-all` advances the epoch, making every prior-epoch session unusable without needing to enumerate session keys.

Redis is part of the authority path. Redis read/write failure is not treated as a soft dependency and does not fall back to JWT validity.

## Exact-session behavior

`logout` revokes the exact current session. `logout-all` advances the patient-wide epoch. A new login after an epoch advance receives the new epoch and can establish a new current session.

Patient JWTs recognized by `get_scoped_session()` never fall through to the legacy biometric-session path. A recognized patient JWT whose live Redis session or current PostgreSQL identity cannot be proven is rejected.

The legacy biometric/session fallback remains for the pre-existing server-scoped handshake flows that are not patient phone-OTP JWTs. It is therefore not a fallback from a failed patient JWT and does not convert a stale patient JWT into legacy authority.

## Device-enrollment binding

Device-enrollment grants are issued only for an exact current patient session. The grant stores the patient id and exact issuing `auth_session_id` server-side in Redis. Claims require the same patient and same live session.

A second session belonging to the same patient cannot claim a grant issued to the first session. Another patient cannot claim the grant. A revoked session and a prior-epoch session cannot claim it. Claim reservation is Redis `SET NX`, so concurrent claims have one winner. Finalization atomically consumes the grant and claim in Redis where `EVAL` is available.

## Logout semantics on mobile

The mobile patient-auth lifecycle attempts server revocation before deleting a non-expired local bearer credential. A server/network failure does not falsely report successful logout. An already-expired credential may be cleared locally because it no longer represents usable bearer authority.

## Audit

Current-session logout emits `PATIENT_SESSION_REVOKED`. Patient-wide logout emits `PATIENT_SESSIONS_REVOKED`. Audit metadata is scope-oriented and does not contain JWTs, OTPs, session identifiers, private keys, or enrollment secrets.

## Qualification evidence

Pre-existing Slice-6B evidence before the final qualification commits:

- focused backend regression set: 285 passed;
- trusted-host regression: 4 passed;
- Partition A on the previous head: 3575 passed / 1 failed because a registration test patched the removed `issue_patient_access_token` runtime symbol;
- PostgreSQL Partition B: pass, zero-skip assertion passed;
- PostgreSQL + Redis Partition C: pass, zero-skip assertion passed;
- Frontend CI: pass;
- real Redis session runtime coverage already included immediate exact revoke, logout-all epoch invalidation, and subject/epoch confusion;
- mobile patient-auth lifecycle: 7 passed on the earlier local qualification run.

The stale registration test was repaired by patching `issue_patient_access_session` and asserting it is never awaited when account finalization rejects an already-registered account. Production code was not weakened and the dead token helper was not restored to `auth_routes.py`.

Additional direct service tests added in this qualification pass cover create/resolve, malformed or missing session claims, patient mismatch, Supabase subject mismatch, session-record epoch mismatch, current-epoch mismatch, missing sessions, exact revoke, revoke-all, post-revoke-all new sessions, Redis read/write failure, corrupt Redis session state, corrupt Redis epoch state, patient substitution in exact-session resolution, expired session creation, and timestamp validation.

Additional real-Redis tests cover exact-session enrollment binding, cross-patient rejection, revoked-session rejection, logout-all invalidation, one-time grant semantics, concurrent claim one-winner behavior, Redis-unavailable denial, and absence of a JWT-only enrollment fallback.

Final GitHub Actions counts and SHAs must be recorded only after the final PR head is green.

## Atomicity and cross-boundary non-claims

Slice 6B does **not** claim distributed ACID behavior across Redis and PostgreSQL.

The following races are explicitly assigned to Slice 6G cross-boundary qualification rather than overclaimed here:

- session creation racing a concurrent `logout-all` epoch advance;
- exact revoke racing an authority resolution already in flight;
- enrollment-grant claim racing exact session revoke or `logout-all` between the session check and claim reservation;
- PostgreSQL device insertion followed by Redis grant finalization failure.

Until 6G qualifies those operation pairs, Slice 6B claims only the sequential/current-state authority contract described above. In ambiguous cross-store failure, callers must fail closed and later Slice-6 work must define idempotency, compensation, and retry semantics.

## Explicit non-claims

Slice 6B does not claim:

- hardware-backed patient private-key custody;
- Android StrongBox, Android Keystore, Apple Secure Enclave, or native attestation;
- native NFC;
- physical-device qualification;
- device-key rotation;
- lost-device/account recovery;
- revoked-key resurrection prevention;
- concurrency-safe five-device enforcement;
- Signed Consent V3 facility/key-version binding;
- cross-boundary Redis/PostgreSQL race closure.

SecureStore use elsewhere in the project is not evidence of hardware-backed non-exportable key custody. Real Redis CI is not physical-platform qualification.