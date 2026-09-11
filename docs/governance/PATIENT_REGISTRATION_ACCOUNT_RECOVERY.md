# Patient Registration Account Recovery

Status: **MERGED / INTERNALLY QUALIFIED; MANUAL-REVIEW CONTINUATION DEFINED BY SLICE 9A**

This workflow provides patient-facing recovery for a narrow class of historical registration-account graph defects. It is intentionally separate from first-time registration, cryptographic device recovery, and operator manual-review authority.

## Authority separation

The flow preserves these boundaries:

- Supabase OTP proves fresh control of the external phone identity.
- `registration_recovery_attempt_token` is continuity and invalid-OTP-budget state only.
- `registration_recovery_token` is one-time authority for one exact server-classified automatic graph repair.
- a patient access session is created only after an automatic graph repair commits.
- bootstrap device authority is issued only when the repaired account has no device history.
- existing device history routes the patient to the independent device-recovery/trusted-device workflow.
- a durable manual-review `case_reference` is an opaque case handle, not patient session, repair, device, consent, or provider authority.

No recovery token or case reference is consent authority, provider authority, or historical device authority.

## Automatic repair allowlist

Self-service repair is limited to server-provable mutations whose provenance is unambiguous:

1. restore a missing, data-free `PatientRecord` anchor for one active patient;
2. rebind one non-revoked Supabase identity along an unambiguous merge-tombstone chain to the single active canonical patient;
3. optionally restore the canonical `PatientRecord` anchor as part of that deterministic rebind.

The repair service re-inspects the graph under a PostgreSQL advisory transaction lock and requires the observed graph fingerprint, patient, provider subject, and repair kind to match the one-time capability.

## States that are not self-service repair

The workflow does not automatically clear or override:

- identity revocation;
- any erasure-registry state;
- unexplained patient soft deletion;
- missing linked patient rows;
- multiple source identities;
- merge cycles or excessively deep/ambiguous merge chains;
- unavailable/deleted canonical patients;
- canonical erasure state;
- canonical identity conflicts.

After fresh identity proof, those states return `REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED` with an opaque durable `case_reference`. Slice 9A owns the case lifecycle, reviewer authority, terminal policy and audit trail. Repeated verified openings for the same durable graph are idempotently anchored to the same case rather than fabricating independent support references.

The patient may poll only the public status surface:

`GET /api/v2/auth/registration-recovery/review/cases/{case_reference}`

That surface does not expose provider subject, graph fingerprint, reviewer identity/session binding or internal authority metadata. The patient cannot select or authorize a reviewer repair.

## Audit contract

`PATIENT_REGISTRATION_RECOVERY_REQUIRED` is transactionally staged in the audit outbox after verified identity and server-side recovery classification.

`PATIENT_REGISTRATION_RECOVERY_COMPLETED` is staged in the same PostgreSQL transaction as an automatic graph repair. A repair is not reported as successful without its durable outbox event.

Manual-review creation and lifecycle events use the separate `PATIENT_REGISTRATION_RECOVERY_REVIEW_*` vocabulary defined by Slice 9A. Audit metadata contains server-owned disposition/repair/reason codes, not plaintext OTPs, phone numbers, provider access tokens, recovery tokens, or reviewer session material.

## Redis authority contract

Production Redis uses Lua for authority transitions. A repairable verified attempt is exchanged atomically for its one-time repair capability: the exact attempt claim is validated, the provider-subject capability slot is checked, capability state is written, and the attempt is deleted at one linearization point.

The capability is stored only under hashed token identifiers and is bound to the exact provider subject, patient id, repair kind, graph fingerprint, operation, and five-minute lifetime. Consumption atomically validates and deletes the capability and its subject slot.

Manual-review cases are durable PostgreSQL authority metadata; a case reference does not replace Redis recovery-attempt/capability semantics.

## Failure semantics

A repair capability is deliberately one-time and is consumed before PostgreSQL mutation. If the graph changed, completion returns `REGISTRATION_RECOVERY_STATE_CHANGED`; the patient must restart recovery. If an unexpected repair failure occurs after consumption, completion returns `REGISTRATION_RECOVERY_RESTART_REQUIRED` rather than telling the client to retry a burned token.

If the graph repair committed but session authority cannot be established, the response states that account repair completed and directs the client to ordinary fresh-OTP sign-in. It does not re-run the graph mutation or manufacture device authority.

If a manual-review graph changes while the case is open, Slice 9A terminal resolution fails closed instead of applying stale reviewer intent.

## Qualification record and continuation

The parent patient-facing recovery implementation was exact-head qualified and merged in PR #40 at main merge commit `54351f9a55ba94665420961cfe766bdcc84a5398`.

Its qualification covered backend lint/pure tests, disposable PostgreSQL graph and concurrency behavior, real Redis authority semantics, frontend tests/build, native generation/source compilation, and route-registry non-regression according to the merged PR evidence.

Slice 9A is a separate continuation with its own exact-head merge gate. Its implementation must not weaken the already-merged automatic recovery contract above.
