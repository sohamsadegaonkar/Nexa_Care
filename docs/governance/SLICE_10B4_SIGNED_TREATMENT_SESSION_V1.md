# Slice 10B.4 — Patient-Signed Treatment Session V1

Status: **CRYPTOGRAPHIC FOUNDATION IMPLEMENTED — ROUTE/WRITE AUTHORITY NOT ENABLED**

Qualified parent checkpoint:
`238c59b7fe94cc04213063f55750f3d727a062c7` on `main`.

## Why this protocol exists

Signed Consent V3 remains a read-only authority because its patient-signed bytes bind `purpose` and `scope`, not an explicit clinical write-operation set. It must not be widened in place.

Treatment Session V1 is therefore a distinct cryptographic protocol:

- protocol version: `nexa-treatment-session-v1`;
- domain: `NEXA_CARE_SIGNED_TREATMENT_SESSION`;
- signing operation: `TREATMENT_SESSION_DECISION`;
- policy version: current server-owned `clinical-access-v1`;
- exact patient/provider/hospital/request binding;
- one-way binding to the exact initiating provider session;
- exact challenge nonce and expiry binding;
- exact patient device/key-version binding;
- exact, closed operation-set binding.

## Operation-set contract

The signed operation set is drawn only from the server-owned `ClinicalAccessOperation` vocabulary:

- `READ_CLINICAL_HISTORY`
- `READ_DOCUMENTS`
- `CREATE_ENCOUNTER`
- `WRITE_PRESCRIPTION`
- `WRITE_DIAGNOSIS`
- `WRITE_VITALS`
- `WRITE_CLINICAL_NOTES`
- `ORDER_INVESTIGATION`

Unknown, duplicate, empty, or non-sequence operation sets fail closed. The operation set is semantically unordered and therefore sorted during canonicalization. Adding or removing an operation changes the treatment-context hash and the patient-signed decision bytes.

## Exact provider-session binding

The server hashes the initiating provider's authenticated session binding before it enters the patient-signed treatment context. The raw session binding is never placed in signed bytes, persistence, logs, or API payloads.

The resulting lowercase SHA-256 value is itself signed by the patient. A later claim/issuance stage must compare the live provider session against this signed hash in constant time before any treatment authority can be minted. This prevents a patient approval created for provider session A from being silently rebound to a later provider session B after re-login.

The cryptographic foundation validates only the signed hash format. The later server-created request and claim boundaries are responsible for deriving the hash from the authenticated provider session and enforcing the live match.

## Canonical signed context

The server-created treatment context binds:

```text
request_id
patient_id
provider_id
hospital_id
provider_session_binding_hash
challenge_nonce
purpose
allowed_operations
access_duration
issued_at
expires_at
policy_version
protocol_version
domain
signing operation
```

The patient decision additionally binds:

```text
decision
treatment_context_hash
device_id
key_id
key_version
public_key_fingerprint
```

The verifier requires the exact currently-active P-256 patient device-key row, matching the existing Signed Consent V3 key-lifecycle security model while remaining cryptographically domain-separated from V3.

## Current implementation boundary

Implemented in this increment:

- deterministic canonical treatment-context bytes;
- deterministic canonical signed-decision bytes;
- exact closed operation-set normalization;
- anti-substitution SHA-256 context hash;
- signed one-way provider-session binding;
- exact active patient device/key-version signature verification;
- signature-failure audit reuse through the canonical `SIGNATURE_VERIFICATION_FAILED` event;
- adversarial contract tests proving operation widening/narrowing changes authority bytes;
- adversarial proof that provider-session rebinding changes authority bytes;
- domain separation from Signed Consent V3.

Not implemented in this increment:

- treatment-session request/challenge routes;
- patient UI signing flow;
- treatment-session claim/minting;
- durable write-enabled session issuance;
- `require_clinical_session(operation)` route gate;
- encounter creation/binding;
- any clinical write authorization.

Therefore **no new clinical write authority exists yet**. Existing Signed Consent V3 remains mapped to `READ_CLINICAL_HISTORY` only.

## Next implementation step

Add the server-created treatment-session request/challenge/approval lifecycle that derives the provider-session binding hash from the authenticated provider context, uses these exact bytes, preserves one-time/replay protections and provider/patient/device trust checks, and produces no write-capable session until a verified patient signature is durably finalized.
