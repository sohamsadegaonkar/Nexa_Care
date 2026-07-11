# API Contract Deviation Report

**Report Date:** 2026-07-11  
**Contract Version:** v2.0.0-alpha (LOCKED)  
**Canonical Source:** `docs/API-CONTRACTS.md`  
**Auditor:** QA Test Suite (automated + manual cross-check)

---

## Summary

| Category | Endpoints Checked | Deviations Found | Severity |
|----------|-------------------|------------------|----------|
| 1. Device Enrollment | 3 | 4 | Medium |
| 2. Consent Flow | 5 | 3 | Low–Medium |
| 3. Patient Records | 4 | 2 | Low |
| 4. AI Pipeline | 5 | 6 | Medium |

**Total Deviations: 15** (3 resolved as security fixes)

---

## 1. Device Enrollment Endpoints

### §1.1 POST /api/v2/patient/devices/enroll

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-01 | Request body field `device_name: string` | Implementation uses `device_label: str` (field name mismatch) | Medium | WS2 |
| D-02 | Request body field `public_key: string` (Base64 DER) | Implementation uses `device_public_key: str` (field name mismatch) | Medium | WS2 |
| D-03 | Response includes `device_id` + `patient_id` + `status` + `enrolled_at` | ✓ Matches | — | — |
| D-04 | Response HTTP status `201 Created` | ✓ Matches | — | — |
| D-05 | Contract specifies `patient_id` in request body | Implementation derives `patient_id` from `get_scoped_session` dependency (session-bound, not body field) | Low | WS2 |

### §1.2 GET /api/v2/patient/devices

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-06 | Response item includes `device_name` | Implementation returns `device_label` | Low | WS2 |
| D-07 | Response item includes `public_key_fingerprint: string` (SHA-256 hex of DER key) | Implementation does NOT return fingerprint | Medium | WS2 |
| D-08 | Response item includes `is_active: boolean` | Implementation returns `status: string` instead | Low | WS2 |
| D-09 | Response item includes `last_used_at: string \| null` | Implementation does NOT return `last_used_at` | Low | WS2 |

---

## 2. Consent Flow Endpoints

### §2.1 POST /api/v2/consent/request

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-10 | Response HTTP status `201 Created` | ✓ Matches | — | — |
| D-11 | Response includes `notification_sent: boolean` | Implementation does NOT return `notification_sent` | Low | WS2 |
| D-12 | Response field `expires_in_seconds` default 90 | Implementation uses 120 seconds | Low | WS2 |

### §2.2 POST /api/v2/consent/approve-signed

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-13 | Response includes `consent_token` when approved | Implementation does NOT return `consent_token` in the response body (token stored server-side in Redis; caller polls via `/status/{request_id}`) | Medium | WS2 |
| D-14 | Response includes `scope` and `expires_at` | Implementation only returns `request_id`, `status`, `responded_at` | Medium | WS2 |

### §2.3 GET /api/v2/consent/status/{request_id}

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-15 | Contract status values: `pending \| approved \| denied \| timeout` | Implementation uses: `pending \| approved \| denied \| expired \| cancelled` (adds `expired` and `cancelled`, no `timeout`) | Low | WS2 |
| D-16 | Response includes `consent_token` when status=approved | Implementation does NOT return consent_token via status polling (security: token not exposed to polling endpoint) | Informational | WS2 |

---

## 3. Patient Records Endpoints

### §3.1 GET /api/v2/patient/{id}/summary

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-17 | Response includes `blood_group`, `allergies`, `chronic_conditions`, `active_medications` | ✓ Matches (plus additional fields `active_conditions`, `current_medications`, `latest_vitals`, `recent_labs`) | Low | WS3 |

### §3.3 POST /api/v2/patient/{id}/record/vitals

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-18 | Contract requires `encounter_id: string` (required) | Implementation makes `encounter_id` optional (`str \| None = None`) | Low | WS3 |

---

## 4. AI Pipeline & Ingestion Endpoints

### §4.1 POST /api/v2/pipeline/documents/upload

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-19 | Response HTTP status `202 Accepted` | Implementation returns `202 Accepted` ✓ | — | — |
| D-20 | Response field `status: "processing"` | Implementation returns `status: "queued"` (semantic difference) | Low | WS4 |
| D-21 | Contract response includes `filename` | ✓ Matches | — | — |

### §4.2 GET /api/v2/pipeline/jobs/{job_id}

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-22 | Contract status values: `queued \| processing \| review_required \| auto_approved \| failed` | Implementation adds: `scored`, `review_pending`, `committed` | Low | WS4 |
| D-23 | Response does not include `auto_approved_count` / `needs_review_count` in contract | Implementation includes both counts (additive, not breaking) | Informational | WS4 |
| D-26 | Contract accepts `patient_id` as query param or header | **RESOLVED**: patient_id is now derived server-side from the job's DB row. Client-supplied `?patient_id=` and `X-Patient-Id` are ignored. This is a security fix, not a contract break. | Informational | WS4 |

### §4.4 POST /api/v2/pipeline/fields/{field_id}/review

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-27 | Contract accepts `patient_id` as query param | **RESOLVED**: patient_id is now derived server-side from the field's parent ExtractionJob. Client-supplied `?patient_id=` is ignored. | Informational | WS4 |

### §4.5 POST /api/v2/pipeline/jobs/{job_id}/commit

| # | Contract Spec | Actual Implementation | Severity | Owning Workstream |
|---|--------------|----------------------|----------|-------------------|
| D-24 | Response includes `fields_committed_count` | Implementation returns both `fields_committed` and `committed_fields_count` (dual-key) | Low | WS4 |
| D-25 | Contract response includes `status: "committed"` | ✓ Matches | — | — |
| D-28 | Contract accepts `patient_id` in request body as trustable | **RESOLVED**: payload.patient_id is now validated against the job's server-derived patient_id. Mismatch returns 400. Ingestion uses the server-derived patient_id. | Informational | WS4 |

---

## Critical Gaps (Not Contract Deviations, but Security Concerns)

These are tracked as xfail tests in `tests/security/` and are NOT contract deviations:

1. **HIGH/CRITICAL auto_approved enforcement** — Backend does not validate that auto_approved fields are not HIGH/CRITICAL risk at commit time.
2. **Low-confidence enforcement** — No service-layer rule forces `needs_review` for confidence < 0.80.
3. **~~Cross-doctor consent binding~~** — ~~`require_consent()` does not check that `capability.clinician_id` matches the requesting provider's `actor_uid`~~. Partially addressed: pipeline endpoints now derive patient_id server-side (D-26, D-27, D-28). Full cross-doctor binding still pending for non-pipeline routes.
4. **Audit chain verification** — No `verify_chain_integrity()` function exists.
5. **Break-glass reason_code allow-list** — Backend accepts any string as `reason_code`.
6. **~~Pipeline patient_id spoofing~~** — **RESOLVED**: Pipeline endpoints (job status, field review, commit) now derive patient_id from DB entities instead of trusting client-provided values. See `validate_consent_for_patient()` in `app/core/consent_gate.py` and test coverage in `tests/test_pipeline_consent_server_side.py`.

---

## Template for New Deviation Reports

```markdown
### D-XXX: [Short Title]

- **Endpoint:** `METHOD /api/v2/...`
- **Contract Section:** §N.N
- **Contract Spec:** [What the contract says]
- **Actual Behavior:** [What the implementation does]
- **Severity:** Critical / High / Medium / Low / Informational
- **Owning Workstream:** WS[N]
- **Breaking Change:** Yes / No
- **Recommended Action:** [Fix implementation / Update contract / Accept as-is]
- **Status:** Open / In Progress / Resolved
- **Tracking Issue:** [Issue URL if applicable]
```

---

## Recommendations

1. **D-01, D-02 (field name mismatch):** Update contract to match implementation OR add Pydantic aliases. These are the most impactful deviations as they cause frontend integration failures.
2. **D-07 (missing public_key_fingerprint):** Implement SHA-256 hex fingerprint derivation in the list endpoint. This is needed for patient-side device verification UX.
3. **D-13, D-14 (approve-signed response):** The contract says `consent_token` and `scope` should be in the approval response. The current design deliberately withholds them for security (polling endpoint doesn't leak tokens). Update the contract to match the secure design.
4. **D-15 (status values):** Align contract to include `expired` and `cancelled`; remove `timeout` (which is not implemented).
5. **D-22 (job status values):** Update contract to include `scored`, `review_pending`, `committed` as valid status values.
6. **D-18 (encounter_id optional):** Consider making encounter_id required in the contract for audit trail integrity, or document it as optional in Alpha.

---

*Report generated by automated QA test suite. All deviations should be communicated to the owning workstream lead for triage.*
