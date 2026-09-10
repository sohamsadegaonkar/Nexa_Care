# Slice 9A — Step 2 Case Creation Review

Status: **IMPLEMENTED / REVIEWED / CI IN PROGRESS / NOT MERGE-ELIGIBLE**

Verified base: `54351f9a55ba94665420961cfe766bdcc84a5398`

Reviewed Step 2 head: `64a1d17a9a48a9a16123581017b39e1c10757c91`

## Scope reviewed

- `app/services/patient_registration_recovery_review_service.py`
- the eight-line manual-review integration hook in `app/services/patient_registration_recovery_service.py`
- `tests/test_patient_registration_recovery_review_case_service.py`
- Findings 005–006 in `SLICE_9A_REVIEW_FINDINGS.md`

## Review conclusions

1. Only a server-side `RegistrationRecoveryInspection` with `disposition == "manual_review"` is accepted for durable case creation.
2. Concrete classifier reasons are normalized through a closed server-owned map before persistence. Unknown reasons fail closed to `SECURITY_CONCERN` rather than becoming arbitrary durable strings.
3. Provider subject is stored only as a domain-separated SHA-256 digest. Raw provider subject, phone, OTP, recovery token, patient session token, device private material, and clinical content are not stored in review tables.
4. Creation idempotency is bound to provider-subject digest + graph fingerprint, not a transient OTP attempt. Repeated verified recovery attempts against the same graph return the same case.
5. Concurrent duplicate inserts are expected to linearize through PostgreSQL uniqueness using a nested savepoint; real PostgreSQL race qualification remains mandatory in Step 8.
6. A newly-created case and `PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED` are staged in the same outer database transaction as the existing `PATIENT_REGISTRATION_RECOVERY_REQUIRED` event because the verified recovery route already commits immediately after `audit_registration_recovery_required` returns.
7. Automatic repair classifications do not create manual-review cases.
8. Case creation grants no patient login, device, consent, clinical, or provider authority.
9. The integration diff against the previously qualified recovery service is exactly the intended hook; no unrelated semantic change was introduced.

## Qualification state

Backend CI #506 and Frontend CI #455 were triggered on exact Step 2 head `64a1d17a9a48a9a16123581017b39e1c10757c91` and were still in progress when this review record was created. Their results are not inherited or claimed here.

## Gate before Step 3

Do not implement the patient-visible status surface until the Step 2 review record is durable and the exact-head lint/pure-unit gate shows no Step 2 regression. Full PostgreSQL concurrency qualification remains a later mandatory Slice 9A gate.
