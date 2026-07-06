# Day 6 Fix Tracker — Nexa Care Security Integration

| Issue ID | Description | Owning Squad | Status | Fix Description | Verified By |
| :--- | :--- | :--- | :--- | :--- | :--- |
| SEC-001 | `AssuranceVerifier` used hardcoded Redis key prefix inconsistent with `AssuranceService`. | Squad A/B | Fixed | Standardized both modules on `push_request:{request_id}`. | Verified by `test_regress_sec_001_redis_prefix`. |
| SEC-002 | `PatientDataErased` exception unhandled in record retrieval route. | Squad A/C | Fixed | Added global exception handler in `app/main.py` to return `410 GONE`. | Verified by `test_regress_sec_002_erased_handler`. |
| SEC-003 | Frontend `api/merge.ts` missing required `X-Hospital-Id` header. | Squad D | Fixed | Updated backend to require header and updated `api/merge.ts`. | Verified by `test_regress_sec_003_merge_hospital_id_required`. |

## Remaining Issues & Mitigations
- **SEC-003**: The backend now strictly enforces `X-Hospital-Id`. Until the frontend package is redeployed, admins must manually add the header if using CLI tools.
