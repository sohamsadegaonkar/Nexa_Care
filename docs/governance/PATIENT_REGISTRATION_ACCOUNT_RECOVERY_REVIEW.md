# Patient Registration Account Recovery — Security Review

Review baseline: `main` `859bde2aeaefe1c172e0e224746e4cbfffe4dac4`

Measured implementation head: `b3108dc3bd3915fceb9e83d5531ccd16751a9aa4`

Status: **OVERALL REVIEW COMPLETE / IMPLEMENTATION HEAD QUALIFIED / FINAL REVIEW-DOC HEAD REQUIRES REQUALIFICATION**

## Findings closed during review

### Unauthenticated mobile transport

The first client draft used the shared API client without `noAuth: true`. That would have made the recovery entry point unusable from the unauthenticated sign-in screen because the client itself would reject the request as `AUTH_REQUIRED`.

The recovery send, verify, and complete requests explicitly use unauthenticated transport. A direct service test guards all three calls.

### OTP proof to repair authority handoff

The first route draft consumed the claimed recovery attempt before separately creating the repair capability. A Redis failure between those operations could strand a successfully verified patient with neither the original attempt nor repair authority.

Repairable recovery uses one production Redis Lua exchange across the exact claimed attempt, provider-subject slot, capability record, and attempt deletion. Real Redis qualification covers single-winner concurrency, one-time consumption, and the rule that a busy subject slot does not consume a second verified attempt.

### Burned capability retry semantics

Repair capability consumption deliberately precedes the PostgreSQL mutation so replay cannot race a recovery mutation. A failure after capability consumption therefore cannot truthfully be retried with the same token.

The route returns `REGISTRATION_RECOVERY_RESTART_REQUIRED` for unexpected repair failure after token consumption instead of labelling the consumed token retryable. If repair committed but session authority is unavailable, the response states `account_repaired=true` and the mobile workflow returns to normal fresh-OTP sign-in rather than replaying account repair.

### Manual-review reference semantics

The initial manual-review response called a deterministic attempt-derived value `case_reference` even though this slice does not create a durable review case. It is named `recovery_reference` and documented as a non-authoritative support reference only.

A real durable manual registration-recovery case lifecycle remains the dependent follow-on slice and must not infer operator authority from this support reference.

## Final overall review

The final implementation review confirmed:

- OTP initiation is neutral and uses `should_create_user=false`.
- A recovery attempt token is continuity/guess-budget state only and cannot be used as login, repair, device, provider, or consent authority.
- Only provider-confirmed invalid OTP outcomes charge the five-attempt budget; transient provider/Redis/database failures do not become successful authority.
- Verified provider subject and normalized verified phone must match the attempted phone before any graph classification.
- Repairable OTP authority is atomically exchanged in real Redis for one exact, five-minute, one-time repair capability bound to provider subject, patient, repair kind, graph fingerprint, operation, and token digest.
- PostgreSQL recomputes the graph under a provider-subject advisory transaction lock and rejects any stale graph fingerprint, patient binding, repair-kind change, identity revocation, erasure state, or identity ambiguity.
- Automatic repair is closed to restoring a data-free `PatientRecord` anchor and deterministic merge-tombstone identity rebinding. It never undeletes a patient, clears `revoked_at`, clears erasure state, moves clinical content between patients, or restores historical device keys.
- `PATIENT_REGISTRATION_RECOVERY_REQUIRED` is staged only after verified identity and server-side classification.
- `PATIENT_REGISTRATION_RECOVERY_COMPLETED` is transactionally staged with the automatic graph mutation; audit failure rolls back repair.
- Patient session authority is issued only after the repair transaction commits. Existing device history remains on the independent device-recovery boundary; only a repaired account with no device history may receive ordinary bootstrap enrollment authority bound to the newly created session.
- The mobile recovery attempt and repair token live only in the active recovery journey; they are not persisted as patient session or device authority.
- `app/main.py` changes are limited to importing and including the dedicated recovery router; the route registry adds exactly the three intentional recovery endpoints.
- No submitted GitHub reviews or inline review comments were present at the measured implementation head.

## Measured qualification

Implementation head `b3108dc3bd3915fceb9e83d5531ccd16751a9aa4` passed Backend CI `#475` and Frontend CI `#424`, including all backend partitions with zero-skip enforcement, real PostgreSQL/Redis qualification, frontend tests, Next production build, workspace build, Android compilation, and iOS compilation.

The later qualification-attestation commit produced head `1124c776b5f16c7a601a3a6a882eac25b5b6e318`; its Backend CI `#486` is green. Frontend CI `#435` remains the final exact-head gate until every native job terminates successfully.

## Retained security invariants

- OTP proof is external-identity possession proof, not account repair authority by itself.
- Recovery attempt state is not login, device, provider, or consent authority.
- Repair capability is one-time and exact-bound.
- Revocation and erasure are not automatically reversed.
- Merge reconciliation follows durable tombstones and refuses ambiguity.
- Graph state is revalidated under PostgreSQL lock before mutation.
- Automatic repair and completion audit share one transaction.
- Account repair does not restore old device keys.
- Existing device history remains on the independent trusted-device/device-recovery path.

## Release rule

This review-document commit itself changes the branch head. It therefore cannot inherit prior CI by assertion. The exact resulting head must pass the complete required Backend CI and Frontend CI matrix before PR #40 can be marked ready or merged. Immediately before merge, `main`, exact PR head, changed-file scope, and review state must be reverified, and merge must use an expected-head guard.
