# Threat Model — Nexa Care Alpha Milestone

**Last updated:** 2026-07-11
**Owner:** QA & Security

---

## Threat Landscape

This document enumerates the attack surfaces specific to the Nexa Care
alpha milestone. Each threat maps to a concrete test in `tests/security/`.

### Legend

| Column | Meaning |
|--------|---------|
| **T-ID** | Unique threat identifier |
| **Attack Surface** | The system boundary the attacker targets |
| **Attacker Capability** | What the attacker can do (assume network access + valid session unless noted) |
| **Expected Defense** | The security control that must prevent or detect the attack |
| **Verification Test** | The specific test file and test name that proves the defense works |
| **Severity** | Impact if the defense is absent |

---

## T-01: Forged ECDSA Signatures

| | |
|---|---|
| **T-ID** | T-01 |
| **Attack Surface** | Retired direct routine issuance (`POST /api/v2/consent/routine/issue`); requests must use discovery-bound consent |
| **Attacker Capability** | A compromised doctor session submits a fabricated ECDSA P-256 signature on the consent approval challenge. The signature does not correspond to any enrolled patient device key. |
| **Expected Defense** | `SignedApprovalVerifier.verify_signed_approval()` checks the signature against **all** enrolled public keys for the patient. A non-matching signature returns `verified=False`. The fixed-duration timing guarantee prevents leaking whether the patient has keys enrolled. |
| **Verification Test** | `tests/security/test_forged_signature.py::test_forged_ecdsa_rejected` |
| **Severity** | CRITICAL — forged approval would bypass the patient's consent authority |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-01a | Wrong key pair (attacker generates own ECDSA key) | `test_forged_signature_wrong_keypair` |
| T-01b | Replayed valid signature on different challenge_nonce | `test_forged_signature_replay_different_nonce` |
| T-01c | Tampered `expires_at` in the signed payload | `test_forged_signature_expired_payload` |
| T-01d | Empty/missing signature field | `test_forged_signature_missing_signature` |

---

## T-02: Forged Assurance Claims

| | |
|---|---|
| **T-ID** | T-02 |
| **Attack Surface** | Historical direct-issuance threat; the endpoint now returns `410 ROUTINE_DIRECT_ISSUANCE_RETIRED` and accepts no issuance input |
| **Attacker Capability** | A doctor claims `push_biometric` assurance level without a real push notification having been sent. They fabricate a `request_id` in the evidence dict. |
| **Expected Defense** | `RedisAssuranceVerifier.verify()` checks the Redis key `nexa:push:{request_id}` for a real pending/approved push state. A fabricated request_id that doesn't exist in Redis returns `verified=False`. |
| **Verification Test** | `tests/security/test_forged_assurance.py::test_forged_push_biometric_evidence` |
| **Severity** | HIGH — bypasses patient's biometric approval requirement |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-02a | Fabricated request_id not in Redis | `test_forged_assurance_fabricated_request_id` |
| T-02b | Claiming push_biometric with only STANDARD evidence | `test_forged_assurance_level_elevation` |
| T-02c | Reusing a consumed/expired push token | `test_forged_assurance_expired_push_state` |

---

## T-03: Expired Consent Grants

| | |
|---|---|
| **T-ID** | T-03 |
| **Attack Surface** | Any consent-gated endpoint (`require_consent()` dependency) accessed with an expired `X-Consent-Token` |
| **Attacker Capability** | A doctor reuses a consent token after its TTL has elapsed (routine: 1 hour, break-glass: 15 minutes). The Redis key for the capability has been auto-deleted by TTL. |
| **Expected Defense** | `consent_engine.validate()` looks up the token in Redis. If the key has expired (TTL-based deletion), the capability is `None`, and `require_consent()` raises HTTP 403. The consent-gate audit trail records `FORBIDDEN_INVALID_OR_EXPIRED`. |
| **Verification Test** | `tests/security/test_consent_expiry.py::test_expired_routine_consent_rejected` |
| **Severity** | CRITICAL — expired consent = unauthorized patient data access |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-03a | Routine consent (1h TTL) after expiry | `test_expired_routine_consent_rejected` |
| T-03b | Break-glass consent (15m TTL) after expiry | `test_expired_break_glass_consent_rejected` |
| T-03c | Token used after explicit revocation | `test_revoked_consent_rejected` |
| T-03d | Clock-skew: token barely expired (1 second past) | `test_consent_rejected_one_second_past_expiry` |
| T-03e | Consent consumed (single-use) then reused | `test_consumed_consent_rejected` |

---

## T-04: Cross-Doctor Consent Reuse

| | |
|---|---|
| **T-ID** | T-04 |
| **Attack Surface** | Any consent-gated endpoint where Doctor B presents a consent token that was issued for Doctor A |
| **Attacker Capability** | Doctor B intercepts or obtains a consent token issued to Doctor A for Patient P. Doctor B uses this token to access Patient P's records. |
| **Expected Defense** | `ConsentCapability.clinician_id` is bound at issue time. `validate()` checks that the requesting provider's `actor_uid` matches `capability.clinician_id`. If mismatched, the request is rejected with HTTP 403 and an `CONSENT_GATED_DECRYPT_FAILED` audit event. |
| **Verification Test** | `tests/security/test_cross_doctor_reuse.py::test_cross_doctor_consent_rejected` |
| **Severity** | CRITICAL — one doctor accessing another's consent grant is IDOR |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-04a | Different doctor_uid using valid token | `test_cross_doctor_consent_rejected` |
| T-04b | Different hospital context | `test_cross_hospital_consent_rejected` |
| T-04c | Same doctor, wrong patient_id | `test_consent_wrong_patient_rejected` |

---

## T-05: Tampered API Payloads

| | |
|---|---|
| **T-ID** | T-05 |
| **Attack Surface** | `POST /api/v2/pipeline/jobs/{job_id}/commit` with fields containing tampered status, confidence, or risk_level values |
| **Attacker Capability** | A doctor modifies the commit payload to change a `needs_review` field's status to `approved`, or sets an artificially high confidence score, or removes the risk_level metadata. |
| **Expected Defense** | (1) The commit endpoint queries the DB for `needs_review` fields — if any exist, HTTP 409 is returned regardless of the payload. (2) Each field in the payload must have valid `confidence` (0.0–1.0 float) and `risk_level` (one of the four allowed values). (3) A field with status `needs_review` in the payload triggers an immediate 409. |
| **Verification Test** | `tests/security/test_tampered_payload.py::test_commit_with_tampered_field_status` |
| **Severity** | HIGH — tampered status could bypass review; missing metadata corrupts clinical data quality |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-05a | Field status changed from needs_review → approved in payload | `test_commit_with_tampered_field_status` |
| T-05b | Confidence set to None / missing | `test_commit_with_missing_confidence` |
| T-05c | Risk_level set to invalid value | `test_commit_with_invalid_risk_level` |
| T-05d | Confidence > 1.0 | `test_commit_with_out_of_range_confidence` |
| T-05e | Field review action with invalid action string | `test_field_review_invalid_action` |

---

## T-06: Unauthorized Record Access

| | |
|---|---|
| **T-ID** | T-06 |
| **Attack Surface** | Patient record endpoints accessed without valid consent or with insufficient scope |
| **Attacker Capability** | (a) Unauthenticated request to patient data. (b) Authenticated doctor without consent token. (c) Authenticated doctor with consent for `medications` scope trying to read `diagnoses` scope. (d) Patient trying to access another patient's data (IDOR). (e) Accessing emergency data without the emergency route. |
| **Expected Defense** | (a) `get_current_provider` rejects → 401. (b) `require_consent()` rejects missing token → 403. (c) Scope enforcement on data endpoints (WS3 scope-gated tabs). (d) `require_self_patient_access()` validates session_patient_id matches target → 403. (e) Emergency snapshot only accessible through dedicated route with audit-first. |
| **Verification Test** | `tests/security/test_unauthorized_access.py::test_unauthenticated_patient_record_rejected` |
| **Severity** | CRITICAL — unauthorized PHI access is a HIPAA violation |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-06a | No auth token at all | `test_unauthenticated_patient_record_rejected` |
| T-06b | Auth but no consent token | `test_no_consent_token_rejected` |
| T-06c | Wrong scope (consent for medications, requesting diagnoses) | `test_insufficient_scope_rejected` |
| T-06d | Patient IDOR (patient A accessing patient B's data) | `test_patient_idor_rejected` |
| T-06e | Emergency data accessed through non-emergency route | `test_emergency_data_wrong_route_rejected` |
| T-06f | Expired session accessing pipeline data | `test_expired_session_pipeline_rejected` |

---

## T-07: Unaudited Access

| | |
|---|---|
| **T-ID** | T-07 |
| **Attack Surface** | The audit ledger — any data access that bypasses the `append_audit_log` call chain |
| **Attacker Capability** | A code path exists that reads patient data without first writing an audit event. Or the audit event is written but with incorrect actor/target metadata. |
| **Expected Defense** | (1) `require_consent()` calls `append_audit_log_or_503` before and after every data access. If the audit write fails, the data access is blocked (HTTP 503). (2) The audit ledger is hash-chained — tampering with a past entry invalidates all subsequent hashes. (3) Every pipeline action (upload, review, commit) has an explicit audit call. |
| **Verification Test** | `tests/security/test_audit_coverage.py::test_consent_access_always_audited` |
| **Severity** | HIGH — unaudited access violates compliance and makes incident response impossible |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-07a | Consent-gated access always produces ≥1 audit event | `test_consent_access_always_audited` |
| T-07b | Pipeline commit always produces audit event | `test_pipeline_commit_audited` |
| T-07c | Audit failure (Supabase down) blocks data access | `test_audit_failure_blocks_access` |
| T-07d | Break-glass access always includes reason_code in audit | `test_break_glass_audit_includes_reason` |
| T-07e | Audit ledger hash chain detects tampering | `test_audit_tamper_detected` |

---

## T-08: Unsafe Auto-Approval

| | |
|---|---|
| **T-ID** | T-08 |
| **Attack Surface** | Pipeline extraction producing `auto_approved` status for HIGH_RISK or CRITICAL_RISK fields |
| **Attacker Capability** | The AI pipeline (or a compromised extraction service) marks a HIGH_RISK or CRITICAL_RISK field as `auto_approved` without human review. |
| **Expected Defense** | (1) Backend should enforce that fields with `risk_level` ≥ `HIGH_RISK` must have `status=needs_review` regardless of the pipeline's decision. (2) The commit endpoint should reject commits containing auto_approved HIGH/CRITICAL fields. (3) Frontend CommitScreen shows a HIGH/CRITICAL warning banner. |
| **Verification Test** | `tests/security/test_unsafe_autoapprove.py::test_high_risk_field_cannot_be_auto_approved` |
| **Severity** | HIGH — auto-approving critical fields without human review is a patient safety risk |

**Sub-threats:**

| Sub-ID | Variant | Test |
|--------|---------|------|
| T-08a | HIGH_RISK field with auto_approved status rejected at commit | `test_high_risk_field_cannot_be_auto_approved` |
| T-08b | CRITICAL_RISK field with auto_approved status rejected at commit | `test_critical_risk_field_cannot_be_auto_approved` |
| T-08c | Pipeline cannot set auto_approved for risk ≥ HIGH | `test_pipeline_cannot_auto_approve_high_risk` |
| T-08d | Confidence below threshold forces needs_review | `test_low_confidence_forces_needs_review` |

---

## Threat-to-Test Matrix

| T-ID | Test File | Test Count | Status |
|------|-----------|-----------|--------|
| T-01 | `tests/security/test_forged_signature.py` | 5 | xfail |
| T-02 | `tests/security/test_forged_assurance.py` | 4 | xfail |
| T-03 | `tests/security/test_consent_expiry.py` | 5 | xfail |
| T-04 | `tests/security/test_cross_doctor_reuse.py` | 4 | xfail |
| T-05 | `tests/security/test_tampered_payload.py` | 5 | xfail |
| T-06 | `tests/security/test_unauthorized_access.py` | 6 | xfail |
| T-07 | `tests/security/test_audit_coverage.py` | 5 | xfail |
| T-08 | `tests/security/test_unsafe_autoapprove.py` | 4 | xfail |
| **Total** | | **38** | |

---

## Assumptions & Alpha Scope Boundaries

1. **Network attacker**: We assume the attacker has network-level access
   (can send arbitrary HTTP requests) but cannot break TLS or compromise
   the server's private keys.

2. **Compromised session**: We assume a doctor's JWT can be stolen (XSS,
   device theft). Defenses must work even with a valid session token.

3. **Insider threat**: A legitimately authenticated doctor attempting to
   access data beyond their consent scope is in scope.

4. **Out of scope for alpha**: Side-channel attacks on KEK/DEK encryption,
   timing attacks beyond the fixed-duration signature verification,
   supply-chain attacks on npm/pip dependencies.

5. **ALPHA gap**: No server-side session revocation endpoint exists yet.
   A stolen JWT is valid until natural expiry (8 hours). This is a known
   gap documented in `docs/security-review-response.md`.

6. **ALPHA gap**: Break-glass reason_code is not validated against an
   allow-list on the backend. The frontend enforces a controlled selector,
   but the API accepts any string. This gap is tracked for the next sprint.
