# Consent Path Hotfix Playbook (Squad A)

This document provides quick-fix patterns for the Nexa Care consent path during Day 7 end-to-end testing.

## 1. Consent token returns 403 but should be valid

**Symptoms:** Clinician sees "Active consent token required or expired" or "Access Denied" despite a fresh grant.

### Diagnostic Steps
1. Run diagnostic script: `python scripts/diagnose_consent_path.py --consent-token <TOKEN>`
2. Check if `Status: CONSUMED`. (Single-use enforcement might have fired).
3. Check if `Status: EXPIRED`.
4. Check if `Requested scope ... is not authorized`. (e.g., token has `clinical.*` but route requested `pii.*`).

### Quick Fixes
* **Manually Extend Token TTL:**
  ```python
  # Python one-liner
  import redis; r = redis.from_url("..."); r.expire("nexa:consent:<TOKEN>", 3600)
  ```
* **Re-issue Token (Last Resort):** Use the `/api/v2/consent/routine/issue` endpoint with appropriate assurance evidence.

---

## 2. Assurance verification fails for push_biometric

**Symptoms:** `POST /routine/issue` returns 403 with "Assurance verification failed".

### Diagnostic Steps
1. Locate the `request_id` from the frontend logs or `assurance_evidence` body.
2. Check Redis directly:
   ```bash
   redis-cli GET "push_request:<REQUEST_ID>"
   ```
3. Verify `status == "approved"` and `patient_id` matches exactly.

### Quick Fixes
* **Manually Approve Request:**
  ```python
  import redis, json; r = redis.from_url("..."); r.set("push_request:<REQ_ID>", json.dumps({"status": "approved", "patient_id": "<PATIENT_ID>", "approved_at": "2026-07-06T12:00:00+00:00"}), ex=300)
  ```

---

## 3. 503 Service Unavailable on consent issuance

**Symptoms:** Backend returns 503 during grant or data access.

### Diagnostic Steps
1. Check diagnostic script connectivity report.
2. Check `system_audit` table status. If the audit ledger write fails, the entire transaction aborts (Fail-Closed).
3. Check if Redis is full (OOM).

### Quick Fixes
* **Clear Rate Limiters (if blocking legitimate demo):**
  ```bash
  redis-cli KEYS "nexa:rate_limit:*" | xargs redis-cli DEL
  ```

---

## 4. Break-glass token cannot be revoked

**Symptoms:** `POST /api/v2/consent/break-glass/revoke` returns 400 or fails.

### Diagnostic Steps
1. Run diagnostic script: `python scripts/diagnose_consent_path.py --consent-token <TOKEN>`
2. Verify `is_break_glass: True` in the database output.
3. Check clinician roles: `require_role("clinician")` must be satisfied.

### Quick Fixes
* **Force Revoke via SQL:**
  ```sql
  UPDATE consent_grant_log SET revoked_at = NOW(), revoked_reason = 'manual_emergency_end' WHERE token_hash = '<HASH>';
  ```
* **Force Delete from Redis:**
  ```bash
  redis-cli DEL "nexa:consent:<TOKEN>"
  ```

---

## Safety Guardrails
* **NEVER** write raw tokens to the database.
* **NEVER** disable audit logging in production.
* **ALWAYS** use the token hash (SHA-256) when querying the `consent_grant_log` table.
