# Slice 10B.5d — Provider WRITE_VITALS Client Integration

## Status

Implementation branch:

```text
task0/10b5d-provider-write-vitals-client
```

Starting authoritative main:

```text
789e2488ee5d3b35b071978d246d488b8a6c431d
```

This slice makes the already-qualified 10B.5c `WRITE_VITALS` authority usable
end-to-end. It does not authorize another clinical-write family.

## Provider UX audit

The starting provider client had:

- provider web cookie authentication;
- patient discovery with an opaque, memory-only discovery handle;
- Signed Consent V3 request/claim flows for read access;
- a read-only Vitals tab in `PatientRecordViewerScreen`;
- legacy `appendVitals(patientId, payload, consentToken)` transport support.

It did **not** have:

- a Treatment Session V1 request client;
- a Treatment Session V1 patient-signing client;
- a Treatment Session claim client;
- a canonical Encounter client;
- an `X-Treatment-Token` client;
- a provider vitals-entry form using the bounded route.

The legacy append method remains for compatibility but is not used by the new
provider workflow.

## Integration defect found

The Treatment Session V1 signed context intentionally includes:

```text
provider_session_binding_hash
```

but the authenticated patient challenge response did not expose that hash.
Consequently a legitimate patient client could not reproduce the exact
domain-separated bytes verified by
`SignedTreatmentSessionV1Verifier`.

The narrow repair adds the **SHA-256 binding hash** to
`TreatmentSessionV1ChallengePayload`. The raw provider session binding remains
server-only and is never returned.

Because this changes a security-sensitive Treatment Session contract, final
qualification must include the complete 10B.5c backend regression matrix.

## Operation boundary

The provider requests exactly:

```text
CREATE_ENCOUNTER
WRITE_VITALS
```

`CREATE_ENCOUNTER` is the required server-owned treatment context prerequisite.
The only clinical-record mutation authorized by this slice remains
`WRITE_VITALS`.

The slice does not request or expose:

- `WRITE_PRESCRIPTION`
- `WRITE_DIAGNOSIS`
- `WRITE_CLINICAL_NOTES`
- `ORDER_INVESTIGATION`

## Patient signing

Treatment Session signing is separate from Signed Consent V3.

Canonical signed bytes remain domain-separated with:

```text
domain = NEXA_CARE_SIGNED_TREATMENT_SESSION
operation = TREATMENT_SESSION_DECISION
protocol_version = nexa-treatment-session-v1
policy_version = clinical-access-v1
```

The patient client uses the existing non-exportable native device-key alias.
Approval requires the existing biometric gate. A signed payload is retained
only in process memory across transport uncertainty so an approval retry can
reuse the identical signature.

The patient UI displays provider, facility, purpose, access duration, and exact
operations. It does not display the challenge nonce, context hash,
provider-session binding hash, or any bearer token.

## Provider Treatment Session bearer

The claimed `treatment_token` is held only in
`ProviderAuthContext` React memory.

It is not written to:

- URLs;
- localStorage or sessionStorage;
- IndexedDB;
- AsyncStorage or SecureStore;
- logs or analytics.

The memory grant intentionally discards claimed `patient_id` and
`clinical_session_id`. It retains only the request identifier, treatment
bearer, approved operation set, expiry, a non-sensitive patient display label,
and the server-returned Encounter identifier for display/state sequencing.

Provider hydration, logout, or authority failure clears the grant.

## Encounter sequence

The provider workflow is:

```text
patient discovery
-> Treatment Session request
-> patient signed approval
-> provider one-time claim
-> POST /api/v2/treatment-session/v1/encounter
-> vitals form enabled
-> POST /api/v2/treatment-session/v1/vitals
```

The Encounter identifier is never sent in the vitals request. It remains
server-bound to the same Treatment Session.

If the one-time claim was already consumed but its bearer is absent from client
memory, the UI requires a new Treatment Session rather than falling back to
Signed Consent V3.

## Vitals transport

The dedicated client method calls:

```text
POST /api/v2/treatment-session/v1/vitals
X-Treatment-Token: <memory-only bearer>
Idempotency-Key: <mutation intent key>
```

The body is exactly one discriminated observation:

- blood pressure;
- heart rate;
- temperature;
- SpO2.

No patient, provider, hospital, ClinicalAccessSession, Encounter, operation, or
provenance authority is accepted from UI state.

Blood glucose remains unsupported.

## Idempotency and double-submit

One in-memory mutation intent binds one exact typed request body to one
idempotency key.

- lost response + unchanged observation -> same key;
- semantic observation edit -> new key;
- known committed observation -> accidental exact duplicate blocked;
- rapid double submit -> one in-flight request;
- provider-session/Treatment Session replacement -> mutation intent reset.

## Error-state semantics

The provider workflow distinguishes:

- retryable/transport-uncertain outcome: preserve observation + idempotency key;
- wrong operation: clear authority and require a correctly scoped Treatment Session;
- expired/revoked authority: clear authority and require a new patient-signed Treatment Session;
- provider trust/session-binding loss: clear authority and require provider reauthentication/trust repair;
- validation rejection: remain in form and require value review.

No authorization failure is presented as a network retry.

Signed Consent V3 remains read-only and is never used as a write fallback.

## Accessibility and clinical neutrality

The vitals UI provides explicit labels and visible units, text status in
addition to tone/color, keyboard-operable buttons and inputs, and accessible
status/action labels.

The UI performs representation validation only. It does not label observations
normal/abnormal, diagnose, or provide treatment or dosage recommendations.

## Persistence and migrations

No database schema change is required.

Expected Alembic head remains:

```text
20260918_treatment_vitals_encounter
```

## Qualification gate

Before merge run/verify on the exact final head:

- focused provider vitals/client/signing tests;
- patient Treatment Session review tests;
- complete app test suite;
- Next test suite;
- Treatment Session V1 backend contract tests;
- 10B.5c vitals route/service/adversarial regressions;
- route registration and migration graph;
- PostgreSQL concurrency qualification;
- Backend Partitions A/B/C with zero skips;
- Next production build;
- workspace build;
- Android native compile;
- iOS native compile;
- Vercel exact-head status.

Any backend regression caused by the patient challenge contract repair blocks
merge.
