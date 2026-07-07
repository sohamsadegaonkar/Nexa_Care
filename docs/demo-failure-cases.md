# Deliberate Failure Checklist: Security Verification

Use this document to manually verify that Nexa Care V2 correctly "fails closed" under hostile or erroneous conditions.

| Test Case | Interaction | Expected Result | Technical Proof |
| :--- | :--- | :--- | :--- |
| **Biometric Mismatch** | Tapping "Approve" but intentionally failing the FaceID/Fingerprint check. | System remains on the approval screen or returns to initial state. No approval sent. | No `PUSH_RESPONSE_RECEIVED` entry in audit log. |
| **Active Denial** | Patient taps "Deny" on the approval screen. | Doctor's screen shows "Access Denied". Consent issuance blocked. | Audit log shows `decision: denied`. `issue_routine` returns 403. |
| **Network Loss** | Disable Wi-Fi/Data after tapping Approve but before biometric check completes. | Error message "Connection Lost". No approval registered on backend. | Redis key `push_request:{id}` status remains `pending`. |
| **Request Expiry** | Wait 91 seconds on the Doctor's waiting screen. | Screen transitions to "Request Timed Out". Use of old notification fails. | Redis TTL evicts the key; `get_push_status` returns `status: timeout`. |
| **Forged Request ID** | Manually call `issue_routine` via API with a non-existent `request_id`. | Backend returns 403 "Assurance verification failed". | `ASSURANCE_VERIFICATION_FAILED` entry in audit log. |
| **Tampered Signature** | (QA Only) Intercept and modify the signature payload during approval. | Backend returns 401 "Biometric verification failed". | `BIOMETRIC_VERIFICATION_FAILED` audit entry with signature error. |
| **Stale Challenge** | Attempt to use an approved push request ID after it has already been used once. | Backend returns 403/404. | Single-use logic in `AssuranceVerifier` deletes the proof after first use. |

---

## Fail-Closed Sign-off
- [ ] No PII returned on any 4xx/5xx response.
- [ ] Consent tokens consumed even if decryption fails.
- [ ] Audit chain remains intact after every failure.
