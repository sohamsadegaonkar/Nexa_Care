# Patient Registration Account Recovery — Security Review

Review baseline: `main` `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`

Status: **pre-CI review complete; exact-head qualification pending**

## Findings closed during review

### Unauthenticated mobile transport

The first client draft used the shared API client without `noAuth: true`. That would have made the recovery entry point unusable from the unauthenticated sign-in screen because the client itself would reject the request as `AUTH_REQUIRED`.

The recovery send, verify, and complete requests now explicitly use unauthenticated transport. A direct service test guards all three calls.

### OTP proof to repair authority handoff

The first route draft consumed the claimed recovery attempt before separately creating the repair capability. A Redis failure between those operations could strand a successfully verified patient with neither the original attempt nor repair authority.

Repairable recovery now uses one production Redis Lua exchange across the exact claimed attempt, provider-subject capability slot, capability record, and attempt deletion. Real Redis qualification covers single-winner concurrency, one-time consumption, and the rule that a busy subject slot does not consume a second verified attempt.

### Burned capability retry semantics

Repair capability consumption deliberately precedes the PostgreSQL mutation so replay cannot race a recovery mutation. That means a failure after capability consumption cannot truthfully be retried with the same token.

The route now returns `REGISTRATION_RECOVERY_RESTART_REQUIRED` for unexpected repair failure after token consumption instead of labelling the consumed token retryable. If repair committed but session authority is unavailable, the response states `account_repaired=true` and the mobile workflow returns to normal fresh-OTP sign-in rather than replaying account repair.

### Manual-review reference semantics

The initial manual-review response called a deterministic attempt-derived value `case_reference` even though this slice does not create a durable review case. It is now named `recovery_reference` and documented as a non-authoritative support reference only.

A real durable manual registration-recovery case lifecycle remains the next slice and must not infer operator authority from this support reference.

## Retained security invariants

- OTP proof is external-identity possession proof, not account repair authority by itself.
- Recovery attempt state is not login, device, provider, or consent authority.
- Repair capability is one-time and bound to exact provider subject, patient, repair kind, graph fingerprint, operation, and expiry.
- Revocation and erasure are not automatically reversed.
- Merge reconciliation follows durable tombstones and refuses ambiguity.
- Graph state is revalidated under a PostgreSQL advisory lock before mutation.
- Automatic repair and its completion audit outbox event share one transaction.
- Account repair does not restore old device keys.
- Existing device history remains on the independent trusted-device/device-recovery path.

## Remaining gate

No merge claim is made by this review document. The final exact branch head must pass the repository's backend and frontend CI workflows, including real PostgreSQL/Redis partitions and native frontend checks, before merge.
