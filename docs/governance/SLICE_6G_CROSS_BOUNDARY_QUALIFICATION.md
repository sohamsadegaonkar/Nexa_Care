# Slice 6G — Cross-Boundary Qualification

Status: qualification implementation in progress.

Baseline: merged Slice 6F `5a4efadc47464b3454ec0ab6749ab583cb485bc0`.

## Purpose

Slice 6G qualifies the already-merged patient authority boundaries across real PostgreSQL, Redis, routes, transactions, retries, and concurrency. It is not a replacement for the unit/integration coverage in Slices 6B–6F; it exercises failure ordering and races that cross those subsystem boundaries.

## Required race and failure matrix

Qualification must cover, where the production path exists:

- patient session creation versus logout-all/session-epoch invalidation;
- patient discovery resolve/claim versus revocation;
- device-enrollment grant claim versus session revoke/logout-all;
- concurrent active-device-limit enforcement;
- concurrent global public-key ownership collisions;
- device-key rotation versus consent approval/claim and device revocation;
- signed-consent approval/claim versus live provider professional/facility/affiliation/capability invalidation;
- lost-device/account-recovery capability claim versus replay/concurrent claim/session invalidation;
- Redis unavailable during security-sensitive authority reads/writes;
- PostgreSQL failure after Redis one-time capability transitions, and Redis failure around PostgreSQL transactions, wherever the production design spans both stores;
- retries and idempotency after ambiguous or partial completion.

## Qualification rules

- Use disposable real PostgreSQL and real Redis for cross-boundary cases; mocks are not sufficient evidence for races or datastore failure semantics.
- Reuse production routes/services rather than constructing a parallel test-only authority implementation.
- Preserve existing fail-closed behavior. Tests must not weaken production session, device, provider-trust, recovery, or consent checks.
- Each cross-store mutation must document its ordering, what can commit first, what is idempotent, what retry does, and whether compensation exists.
- Where atomicity across PostgreSQL and Redis is impossible, record the precise partial-failure state and prove that it does not create unauthorized authority.
- Concurrency assertions must prove the number of successful authorities, not merely that one request returned an error.
- CI qualification requires Backend partitions A/B/C with zero skips and Frontend CI on the exact final head.

## Nonclaims

Slice 6G does not claim:

- distributed ACID across PostgreSQL and Redis;
- physical-device or native-hardware qualification;
- hardware-backed/non-exportable private keys;
- completion of mobile/native Slice 6H;
- completion of physical-pilot Slice 6I.
