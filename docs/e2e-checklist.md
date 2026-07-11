# E2E Test Checklist — Nexa Care Alpha

**Date:** 2026-07-11
**Version:** v2.0.0-alpha
**Verification Basis:** Manual real-phone test + automated test suite

---

## 11 Demo Success Criteria

Each criterion is verified with a specific test or manual check that proves it works.

---

### SC-1: Doctor can log in with password + MFA

| Item | Detail |
|------|--------|
| **Criterion** | Doctor authenticates via email + password, then TOTP code if MFA is enabled |
| **Verification Method** | Manual: Doctor Login screen on Chrome |
| **Test Evidence** | `POST /api/v2/auth/login` → 200 with `access_token` + `provider_uid` |
| **MFA Flow** | First login returns `{ detail: "MFA required", mfa_token: "..." }` → `POST /api/v2/auth/mfa/verify` → 200 with `access_token` |
| **Automated Coverage** | `tests/test_auth*.py` — structural test pass |
| **Manual Result** | ✅ PASS |
| **Notes** | MFA token is single-use (Redis TTL). Provider UID derived server-side, not client-supplied. |

---

### SC-2: Doctor can request consent for a patient

| Item | Detail |
|------|--------|
| **Criterion** | Doctor initiates a consent request with controlled purpose, scope, and server-clamped duration |
| **Verification Method** | Manual: RequestConsentScreen → `POST /api/v2/consent/request` |
| **Test Evidence** | 201 Created → `{ request_id, challenge_nonce, status: "pending", expires_in_seconds: 120 }` |
| **Automated Coverage** | `tests/integration/test_consent_flow_qa.py::test_full_consent_flow_with_real_signatures` |
| **Manual Result** | ✅ PASS |
| **Notes** | `provider_id` derived from Bearer session (not body). Purpose must be from controlled list. Duration clamped to [300, 3600]. |

---

### SC-3: Patient can approve consent with biometric signature

| Item | Detail |
|------|--------|
| **Criterion** | Patient receives push, reviews request, approves via Face ID, device signs challenge with real P-256 key |
| **Verification Method** | Manual: Real iPhone 14 Pro with Face ID |
| **Test Evidence** | `POST /api/v2/consent/approve-signed` → 200 `{ status: "approved" }`. Signature verified by `SignedApprovalVerifier` against enrolled public key |
| **Automated Coverage** | `tests/integration/test_consent_flow_qa.py::test_full_consent_flow_with_real_signatures` (real P-256 signatures) |
| **Manual Result** | ✅ PASS |
| **Notes** | Face ID required for approval (biometric proof). Denial does NOT require Face ID. Private key stored in iOS Keychain. |

---

### SC-4: Doctor can upload a clinical document

| Item | Detail |
|------|--------|
| **Criterion** | Doctor uploads a lab report / clinical document through the pipeline |
| **Verification Method** | Manual: Pipeline UI upload |
| **Test Evidence** | `POST /api/v2/pipeline/documents/upload` → 202 Accepted `{ job_id, status: "queued" }` |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py::test_auto_approved_pipeline_flow` |
| **Manual Result** | ✅ PASS |
| **Notes** | Upload requires valid consent token in `X-Consent-Token` header. File size capped at 20 MB. |

---

### SC-5: Job status shows real-time extraction progress

| Item | Detail |
|------|--------|
| **Criterion** | Doctor sees extraction job progress — queued → processing → scored / review_required |
| **Verification Method** | Manual: Job status screen polling |
| **Test Evidence** | `GET /api/v2/pipeline/jobs/{job_id}` → `{ status: "scored", ... }` or `{ status: "review_required" }` |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py::test_auto_approved_pipeline_flow` (status transitions) |
| **Manual Result** | ✅ PASS |
| **Notes** | Status includes `auto_approved_count` and `needs_review_count`. Polling adaptive: 2s → 5s → 10s. |

---

### SC-6: Review queue displays flagged documents

| Item | Detail |
|------|--------|
| **Criterion** | Doctor sees needs_review fields in the review queue with confidence, risk level, and provenance |
| **Verification Method** | Manual: Review queue screen |
| **Test Evidence** | `GET /api/v2/pipeline/jobs/{job_id}/review-queue` → list of fields with `status: "needs_review"` |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py::test_needs_review_pipeline_flow` |
| **Manual Result** | ✅ PASS |
| **Notes** | Fields show AI provenance badge, confidence percentage, risk level. Allergy/allergen fields forced to HIGH_RISK. |

---

### SC-7: Doctor can edit a missing-value field

| Item | Detail |
|------|--------|
| **Criterion** | Doctor selects a field with missing/incorrect value and edits it |
| **Verification Method** | Manual: Field review → edit action |
| **Test Evidence** | `POST /api/v2/pipeline/fields/{field_id}/review` → `{ action: "edit", corrected_value: "..." }` → 200 |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py::test_edited_field_in_commit` |
| **Manual Result** | ✅ PASS |
| **Notes** | Only valid actions: `approve`, `reject`, `edit`. Invalid actions → 400. Edit requires `corrected_value`. |

---

### SC-8: Doctor can approve a flagged field

| Item | Detail |
|------|--------|
| **Criterion** | Doctor reviews and explicitly approves a field that was flagged for review |
| **Verification Method** | Manual: Field review → approve action |
| **Test Evidence** | `POST /api/v2/pipeline/fields/{field_id}/review` → `{ action: "approve" }` → 200 |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py::test_needs_review_pipeline_flow` |
| **Manual Result** | ✅ PASS |
| **Notes** | Approval changes field status from `needs_review` to `approved`. Rejected fields excluded from commit. |

---

### SC-9: Commit succeeds when all fields resolved

| Item | Detail |
|------|--------|
| **Criterion** | When all fields are approved/edited (no `needs_review` remaining), commit succeeds and data is ingested |
| **Verification Method** | Manual: Commit button |
| **Test Evidence** | `POST /api/v2/pipeline/jobs/{job_id}/commit` → 200 `{ status: "committed", committed_fields_count: N }` |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py::test_auto_approved_pipeline_flow` + `test_full_flow_with_review_queue` |
| **Manual Result** | ✅ PASS |
| **Notes** | Commit rejects if unresolved fields exist (409). HIGH/CRITICAL fields with `auto_approved` status → 400 (defense-in-depth). Patient_id derived server-side. |

---

### SC-10: Committed data appears in the patient timeline

| Item | Detail |
|------|--------|
| **Criterion** | After commit, ingested fields are visible in the patient record / timeline |
| **Verification Method** | Manual: PatientRecordViewerScreen refresh |
| **Test Evidence** | `GET /api/v2/patient/{id}/summary` with valid consent token → updated data |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py::test_auto_approved_pipeline_flow` (commit → verify committed) |
| **Manual Result** | ✅ PASS |
| **Notes** | Data access requires valid `X-Consent-Token`. Scope restriction enforced server-side. Committed fields go through `ingest_extracted_fields` → patient record tables. |

---

### SC-11: Emergency access works with reason code + 15-min TTL

| Item | Detail |
|------|--------|
| **Criterion** | Doctor can invoke break-glass with a controlled reason code and mandatory justification; access expires in 15 minutes |
| **Verification Method** | Manual: EmergencyAccessScreen + automated security tests |
| **Test Evidence** | `POST /api/v2/consent/break-glass/issue` → `{ consent_token, expires_at }` with 900s TTL |
| **Automated Coverage** | `tests/security/test_consent_expiry.py` + `tests/security/test_audit_coverage.py` |
| **Manual Result** | ✅ PASS |
| **Notes** | Rate limited: 3/provider/hour. Reason codes from controlled list (12 values). "Other" triggers mandatory review. Justification min 20 chars (50 for "other"). BREAK_GLASS badge in audit trail. After 15 min, token auto-expires → 403 on next access. |

---

## Checklist Summary

| # | Criterion | Result | Evidence Type |
|---|-----------|--------|---------------|
| SC-1 | Doctor login + MFA | ✅ PASS | Manual + structural tests |
| SC-2 | Request consent | ✅ PASS | Manual + integration test (real P-256) |
| SC-3 | Patient approves with Face ID | ✅ PASS | Manual (real device) + integration test |
| SC-4 | Upload clinical document | ✅ PASS | Manual + integration test |
| SC-5 | Job status progress | ✅ PASS | Manual + integration test |
| SC-6 | Review queue | ✅ PASS | Manual + integration test |
| SC-7 | Edit missing-value field | ✅ PASS | Manual + integration test |
| SC-8 | Approve flagged field | ✅ PASS | Manual + integration test |
| SC-9 | Commit resolved fields | ✅ PASS | Manual + integration test |
| SC-10 | Timeline shows committed data | ✅ PASS | Manual + integration test |
| SC-11 | Emergency access + reason code | ✅ PASS | Manual + security tests |

**Overall: 11/11 PASS**

---

## Automated Test Suite Cross-Reference

| Suite | File | Tests | Status |
|-------|------|-------|--------|
| Security (T-01–T-08) | `tests/security/test_*.py` | 47 | ✅ All PASS |
| Consent integration | `tests/integration/test_consent_flow_qa.py` | 5 | ✅ All PASS |
| Pipeline integration | `tests/integration/test_pipeline_flow_qa.py` | 8 | ✅ All PASS |
| Pipeline unit (QA) | `tests/test_pipeline_qa.py` | 33 | ✅ All PASS |
| Pipeline consent server-side | `tests/test_pipeline_consent_server_side.py` | 22 | ✅ All PASS |
| **Total** | | **115** | **✅ All PASS** |

---

## Security Test Verification

| Threat | Test File | Key Tests | Status |
|--------|-----------|-----------|--------|
| T-01 Forged signature | `test_forged_signature.py` | Wrong key→401, revoked→401, unenrolled→401 | ✅ |
| T-02 Forged assurance | `test_forged_assurance.py` | Fabricated ID→False, empty evidence→False, pending→False | ✅ |
| T-03 Consent expiry | `test_consent_expiry.py` | Expired→403, revoked→403, 1s boundary→None, expired challenge→reject | ✅ |
| T-04 Cross-doctor reuse | `test_cross_doctor_reuse.py` | Wrong clinician→None, wrong patient→None, gate→403 | ✅ |
| T-05 Tampered payload | `test_tampered_payload.py` | Tampered status→409, invalid risk→400, tampered decision→401 | ✅ |
| T-06 Unauthorized access | `test_unauthorized_access.py` | No token→403, cross-patient→403, None patient→403 | ✅ |
| T-07 Audit coverage | `test_audit_coverage.py` | Access audited, failure audited, hash chain detects tamper | ✅ |
| T-08 Unsafe auto-approve | `test_unsafe_autoapprove.py` | CRITICAL/HIGH never auto, allergy→HIGH, commit rejects | ✅ |
