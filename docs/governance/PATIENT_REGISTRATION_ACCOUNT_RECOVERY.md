# Patient Registration Account Recovery

Status: **implementation under qualification**

This slice adds a patient-facing recovery workflow for a narrow class of historical registration-account graph defects. It is intentionally separate from first-time registration and from cryptographic device recovery.

## Authority separation

The flow preserves these boundaries:

- Supabase OTP proves fresh control of the external phone identity.
- `registration_recovery_attempt_token` is continuity and invalid-OTP-budget state only.
- `registration_recovery_token` is one-time authority for one exact server-classified graph repair.
- a patient access session is created only after the graph repair commits.
- bootstrap device authority is issued only when the repaired account has no device history.
- existing device history routes the patient to the independent device-recovery/trusted-device workflow.

No recovery token is consent authority, provider authority, or historical device authority.

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

Those states return `REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED` after fresh identity proof. The current `recovery_reference` is a non-authoritative support reference only. It is **not** a durable case identifier. A durable manual registration-recovery case lifecycle is a separate follow-on slice with its own schema, operator authority, audit trail, reason-specific resolution policy, and adversarial qualification.

## Audit contract

`PATIENT_REGISTRATION_RECOVERY_REQUIRED` is transactionally staged in the audit outbox after verified identity and server-side recovery classification.

`PATIENT_REGISTRATION_RECOVERY_COMPLETED` is staged in the same PostgreSQL transaction as an automatic graph repair. A repair is not reported as successful without its durable outbox event.

Audit metadata contains server-owned disposition/repair/reason codes, not plaintext OTPs, phone numbers, provider access tokens, or recovery tokens.

## Redis authority contract

Production Redis uses Lua for authority transitions. A repairable verified attempt is exchanged atomically for its one-time repair capability: the exact attempt claim is validated, the provider-subject capability slot is checked, capability state is written, and the attempt is deleted at one linearization point.

The capability is stored only under hashed token identifiers and is bound to the exact provider subject, patient id, repair kind, graph fingerprint, operation, and five-minute lifetime. Consumption atomically validates and deletes the capability and its subject slot.

## Failure semantics

A repair capability is deliberately one-time and is consumed before PostgreSQL mutation. If the graph changed, completion returns `REGISTRATION_RECOVERY_STATE_CHANGED`; the patient must restart recovery. If an unexpected repair failure occurs after consumption, completion returns `REGISTRATION_RECOVERY_RESTART_REQUIRED` rather than telling the client to retry a burned token.

If the graph repair committed but session authority cannot be established, the response states that account repair completed and directs the client to ordinary fresh-OTP sign-in. It does not re-run the graph mutation or manufacture device authority.

## Qualification gates

Merge eligibility requires the exact final head to pass:

- backend lint and pure/unit tests;
- disposable PostgreSQL qualification for graph classification, lock/revalidation, audit coupling, erasure/revocation/merge cases, and concurrent repair behavior;
- real Redis qualification for attempt serialization, invalid-OTP budget, one-time capability semantics, and the atomic attempt-to-capability exchange;
- frontend tests and production build;
- Android and iOS native generation/source compilation as required by the repository workflow;
- route-registry non-regression.

Until those exact-head gates pass, this document does not claim the slice is qualified or merged.
