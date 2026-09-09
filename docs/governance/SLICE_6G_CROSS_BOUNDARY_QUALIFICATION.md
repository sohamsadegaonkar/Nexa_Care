# Slice 6G — Cross-Boundary Qualification

Status: implementation complete; exact-head qualification pending.

Baseline: merged Slice 6F `5a4efadc47464b3454ec0ab6749ab583cb485bc0`.

## Purpose

Slice 6G qualifies the already-merged patient authority boundaries across real PostgreSQL, Redis, routes, transactions, retries, and concurrency. It is not a replacement for the unit/integration coverage in Slices 6B–6F; it exercises failure ordering and races that cross those subsystem boundaries.

## Hardening found during qualification

The 6G audit found one real enrollment race in the 6B/6C composition. `claim_device_enrollment_token()` revalidated the exact issuing patient session, but `finalize_device_enrollment_token()` previously consumed the reserved Redis grant without atomically checking that the same session/epoch was still current. Therefore the interleaving `claim -> logout-all -> finalize -> PostgreSQL enrollment` could finalize a grant after logout-all won.

The production finalization linearization point is now one Redis Lua operation over:

- the enrollment grant;
- the reservation/claim id;
- the exact patient session row;
- the patient-wide current session epoch.

The script verifies grant scope/patient/session binding, the exact claim id, active session state, patient ownership, and session epoch equality before deleting the grant/claim and returning success. If logout/revocation wins first, the stale reservation is burned and finalization returns false. There is no JWT-only fallback. Test/local Redis doubles without Lua revalidate immediately before deletion, but real Redis qualification specifically exercises the atomic Lua branch.

## New real PostgreSQL + Redis qualification

`tests/integration/test_slice6g_cross_boundary_postgres_redis.py` uses a disposable PostgreSQL database migrated to `20260909_device_trust_lifecycle` plus the real CI Redis service. It exercises:

- stale-epoch session creation after logout-all: a session written with the old epoch never becomes authoritative;
- bootstrap enrollment partial failure: the exact-session Redis grant is finalized first, an injected real PostgreSQL transactional audit failure rolls the device insert back, and the consumed grant cannot be replayed;
- device rotation partial failure: the one-time Redis rotation challenge is consumed first, an injected PostgreSQL transactional audit failure rolls the version transition back, the old key remains current, and the challenge cannot be replayed;
- account recovery partial failure: the one-time recovery capability is consumed and old session epoch invalidated first, an injected PostgreSQL transactional audit failure rolls device changes back, the old session remains invalid, and the consumed recovery capability cannot be replayed;
- discovery handle consume versus revoke: the real Redis consume/delete race yields at most one disclosure result and no later handle reuse.

`tests/integration/test_patient_enrollment_session_redis.py` additionally proves the previously missing deterministic race: reserve an enrollment grant, execute logout-all, then finalize. Finalization fails and the stale grant cannot be reclaimed.

## Existing evidence reused by the 6G matrix

6G deliberately reuses qualified evidence rather than duplicating it under new test names:

- Slice 6B real Redis coverage: exact-session grants, cross-patient/session rejection, one-winner concurrent claims, exact-session revoke, logout-all invalidation, Redis-unavailable fail-closed behavior;
- Slice 6C real PostgreSQL coverage: active-device-limit race and same-key/cross-patient global key ownership races;
- Slice 6D real PostgreSQL/Redis coverage: concurrent double rotation, stale/revoked key denial, one-time challenge replay/expiry/session binding, transactional rollback;
- Slice 6E real PostgreSQL/Redis coverage: recovery-capability one-winner/replay/expiry/session binding, recovery DB rollback, trusted-device enrollment races and global key ownership;
- Slice 6F V3 production-shaped E2E: approving key must remain current and provider professional/facility/affiliation/capability trust is reloaded before access capability claim.

## Cross-store ordering and retry semantics

### Bootstrap enrollment

Order: claim Redis grant -> atomically finalize against live exact session/epoch -> PostgreSQL device transaction.

If PostgreSQL fails after finalization, no device authority is committed and the enrollment grant is gone. Retry requires a newly issued enrollment authority from a current session; the old token is not restored. This chooses `consumed grant + no device` over `device committed + reusable grant`.

### Device-key rotation

Order: consume exact one-time Redis rotation challenge -> PostgreSQL version transition/audit transaction.

If PostgreSQL fails, the old device key remains active because the transaction rolls back, while the challenge remains consumed. Retry requires a fresh challenge and a fresh signature from the still-current private key. The system does not attempt cross-store compensation that would resurrect a consumed challenge.

### Account recovery

Order: consume one-time recovery capability -> advance patient-wide session epoch -> PostgreSQL revoke-old/install-fresh-device transaction -> issue fresh patient session.

If PostgreSQL fails after epoch invalidation, old sessions remain invalid and old device rows remain unchanged because the DB transaction rolls back. The patient must authenticate again to obtain a new recovery capability. This intentionally favors stale-session invalidation over availability; it is fail-closed, not distributed ACID.

### Discovery handles

Activation and consumption are Redis-atomic. Consume deletes the ACTIVE handle as its linearization point; revoke deletes the same key. In a consume/revoke race, the handle cannot survive for later reuse. A losing operation cannot create a second disclosure authority.

## Qualification rules

- Use disposable real PostgreSQL and real Redis for cross-boundary cases; mocks are not sufficient evidence for races or datastore failure semantics.
- Reuse production routes/services rather than constructing a parallel test-only authority implementation.
- Preserve existing fail-closed behavior. Tests must not weaken production session, device, provider-trust, recovery, or consent checks.
- Where atomicity across PostgreSQL and Redis is impossible, record the precise partial-failure state and prove that it does not create unauthorized authority.
- Concurrency assertions must prove the number of successful authorities, not merely that one request returned an error.
- CI qualification requires Backend partitions A/B/C with zero skips and Frontend CI on the exact final head.

## Qualification history

Backend CI #321 on implementation head `56193d85c808446961f56639d6d56aea472127fe` established that Ruff and Partition A were green and that all five new cross-boundary test bodies executed successfully. Partition C failed only in fixture teardown because the repository pins `redis==4.5.1`, which provides `close()` rather than the newer `aclose()` API. The fixture cleanup was corrected without changing production behavior or test assertions. That failed run is not final qualification evidence.

## Nonclaims

Slice 6G does not claim:

- distributed ACID across PostgreSQL and Redis;
- automatic compensation for a consumed one-time authority after a later PostgreSQL failure;
- physical-device or native-hardware qualification;
- hardware-backed/non-exportable private keys;
- completion of mobile/native Slice 6H;
- completion of physical-pilot Slice 6I;
- that Nexa Care is "fully secure".
