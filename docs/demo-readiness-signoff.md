# Demo Readiness Sign-Off — Nexa Care Alpha

**Date:** 2026-07-11
**Version:** v2.0.0-alpha
**Decision:** **GO** — all demo segments clear, zero blocking issues

---

## Sign-Off Authority

| Role | Name | Decision | Date |
|------|------|----------|------|
| QA Lead | — | ✅ GO | 2026-07-11 |
| Security Lead | — | ✅ GO (with alpha caveats) | 2026-07-11 |
| Product Owner | — | ✅ GO | 2026-07-11 |
| Engineering Lead | — | ✅ GO | 2026-07-11 |

---

## Demo Segments — GO/NO-GO Assessment

### Scene 1: Context & Architecture (2 min)

| Item | Assessment |
|------|------------|
| **Segment** | Architecture overview and trust chain explanation |
| **Dependencies** | Slide deck / whiteboard only |
| **Blocking Issues** | None |
| **Decision** | ✅ **GO** |

---

### Scene 2: The Happy Path — Consent Approval (3 min)

| Item | Assessment |
|------|------------|
| **Segment** | Doctor requests consent → patient receives push → Face ID → approve → doctor sees record |
| **Dependencies** | Real iPhone with Face ID, enrolled device key, push notification delivery, backend consent engine |
| **Verified By** | `docs/real-phone-test-report.md` Tests 1–4 |
| **Automated Coverage** | `tests/integration/test_consent_flow_qa.py` (5/5 PASS, real P-256 signatures) |
| **Security Coverage** | T-01 (forged signature → 401), T-03 (expired → 403), T-04 (cross-doctor → 403) |
| **Blocking Issues** | None |
| **Known Risks** | Push may not arrive on demo WiFi — deep-link fallback tested and ready |
| **Decision** | ✅ **GO** |

---

### Scene 3: Deliberate Failure — Denial Path (2 min)

| Item | Assessment |
|------|------------|
| **Segment** | Doctor requests consent → patient denies → doctor sees "Access Denied" |
| **Dependencies** | Same as Scene 2 |
| **Verified By** | `docs/real-phone-test-report.md` Test 5 |
| **Automated Coverage** | `tests/integration/test_consent_flow_qa.py::test_denied_consent_flow_with_real_signatures` |
| **Security Coverage** | T-05 (tampered decision → 401), T-06 (no consent → 403) |
| **Blocking Issues** | None |
| **Decision** | ✅ **GO** |

---

### Scene 4: Break-Glass & Expiry (2 min)

| Item | Assessment |
|------|------------|
| **Segment** | Doctor invokes emergency access with reason code → access granted → 15-min expiry → data access blocked |
| **Dependencies** | Backend break-glass endpoint, Redis TTL, consent gate re-validation |
| **Verified By** | `docs/real-phone-test-report.md` Tests 7–8 |
| **Automated Coverage** | `tests/security/test_consent_expiry.py` (5/5 PASS), `tests/security/test_audit_coverage.py` (5/5 PASS) |
| **Security Coverage** | T-03 (expired → 403), T-07 (audit coverage), T-08 (break-glass audited) |
| **Blocking Issues** | None |
| **Known Risks** | ISS-08 (reason code not validated server-side — P2, mitigated by frontend enforcement) |
| **Decision** | ✅ **GO** |

---

### Scene 5: The Audit Trail (1 min)

| Item | Assessment |
|------|------------|
| **Segment** | Show audit ledger with hash chain, tamper detection, complete access trail |
| **Dependencies** | Audit ledger with hash chaining |
| **Verified By** | `docs/real-phone-test-report.md` (audit confirmed in all tests) |
| **Automated Coverage** | `tests/security/test_audit_coverage.py::test_audit_hash_chain_detects_tampering` |
| **Security Coverage** | T-07 (audit coverage — all access audited, failures audited, hash chain detects tamper) |
| **Blocking Issues** | None |
| **Decision** | ✅ **GO** |

---

### Scene 6 (Pipeline): Upload → Review → Commit (3 min)

| Item | Assessment |
|------|------------|
| **Segment** | Doctor uploads document → AI extracts fields → review queue → approve/edit → commit → timeline updates |
| **Dependencies** | Pipeline upload, extraction service, review/commit endpoints, consent gate |
| **Verified By** | `docs/e2e-checklist.md` SC-4 through SC-10 |
| **Automated Coverage** | `tests/integration/test_pipeline_flow_qa.py` (8/8 PASS) + `tests/test_pipeline_qa.py` (33/33 PASS) + `tests/test_pipeline_consent_server_side.py` (22/22 PASS) |
| **Security Coverage** | T-05 (tampered payload → 400/409), T-06 (unauthorized → 403), T-08 (unsafe auto-approve → rejected) |
| **Blocking Issues** | None |
| **Decision** | ✅ **GO** |

---

## Blocking Issues Summary

| Issue ID | Description | Severity | Blocks Demo? |
|----------|-------------|----------|-------------|
| — | — | — | — |

**Blocking issues: ZERO**

All identified issues (ISS-01 through ISS-16) have documented mitigations for the alpha demo. No issue prevents any demo scene from completing successfully.

---

## Pre-Demo Checklist

Every item must be confirmed before the demo begins.

### Infrastructure

- [x] Backend staging deployed at `https://demo-api.nexacare.ai`
- [x] PostgreSQL database migrated (latest Alembic revision)
- [x] Redis instance reachable and not at capacity
- [x] `KEK_ROOT_SECRET` and `NEXA_PEPPER_KEY` set in environment
- [x] CORS configured for `https://demo.nexacare.ai`
- [x] TLS certificates valid and not expiring within 30 days

### Test Data

- [x] Demo provider account created with `clinician` role
- [x] Demo patient account created with enrolled device key
- [x] Push notification token registered for demo patient
- [x] Test patient record with allergies, medications, vitals, lab results
- [x] Two pre-created consent requests: `REQUEST_APPROVE` and `REQUEST_DENY`

### Patient Device

- [x] iPhone charged (>80%)
- [x] App installed (Expo dev build, not Expo Go)
- [x] Face ID enrolled and working
- [x] Demo patient logged in
- [x] Device key enrolled (`GET /api/v2/patient/devices` → active)
- [x] Push token registered in Redis
- [x] App in background (not killed)
- [x] Do Not Disturb OFF
- [x] Deep-link fallback URLs saved in Notes app

### Doctor Device

- [x] Chrome browser logged in to `https://demo.nexacare.ai`
- [x] Provider session active (Bearer token valid for ≥60 min)
- [x] Patient record accessible via search or NFC UID entry
- [x] Pipeline upload tested with sample lab report

### Automated Test Verification

- [x] `pytest tests/security/ -q` → 47 passed, 0 failed, 0 xfail
- [x] `pytest tests/integration/ -q` → 13 passed, 0 failed
- [x] `pytest tests/test_pipeline_qa.py tests/test_pipeline_consent_server_side.py -q` → 55 passed, 0 failed
- [x] `ruff check .` → 0 violations

---

## Alpha Caveats (Read Before Demo)

The following limitations must be acknowledged if asked by the audience:

1. **Private key is in JS memory during signing** — not hardware-isolated. Acceptable for alpha; hospital pilot requires native Secure Enclave module.
2. **No server-side session revocation** — stolen JWT is valid for 8 hours. Mitigation: implement session blacklist.
3. **Break-glass reason codes not validated server-side** — frontend enforces controlled list but backend accepts any string.
4. **Push notification delivery not guaranteed** — UI says "may trigger notifications" honestly. Deep-link fallback available.
5. **WebSocket transport disabled** — polling only (2s interval). Doctor may see slight delay on approval status.
6. **Role not from signed JWT claim** — hardcoded `clinician` default in frontend.
7. **Tokens in-memory only** — page refresh loses session. Demo presenters should not refresh.

---

## Final Decision

```
┌─────────────────────────────────────────┐
│                                         │
│   DEMO READINESS: ✅ GO                 │
│                                         │
│   Blocking issues: 0                    │
│   Security tests: 47/47 PASS            │
│   Integration tests: 13/13 PASS         │
│   Pipeline unit tests: 55/55 PASS       │
│   Ruff violations: 0                    │
│   Real-phone tests: 10/10 PASS          │
│   E2E criteria: 11/11 PASS              │
│                                         │
│   All demo scenes verified.             │
│   Alpha caveats documented.             │
│   Fallbacks tested and ready.           │
│                                         │
└─────────────────────────────────────────┘
```

**Signed off for demo on:** 2026-07-11
