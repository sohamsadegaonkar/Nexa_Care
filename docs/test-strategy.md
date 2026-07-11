# Test Strategy — Nexa Care Alpha Milestone

**Last updated:** 2026-07-11
**Owner:** QA & Security

---

## 1. Test Layers

### 1.1 Unit Tests

Test individual functions, services, and components in isolation with
mocked dependencies. No network, no database, no filesystem.

| Target | What | Count Target |
|--------|------|-------------|
| Service layer | `consent_engine.py`, `signed_approval_verifier.py`, `assurance_verifier.py`, `pipeline_orchestrator.py`, `record_ingestion.py` | ≥ 50 |
| Route handlers | Each v2 route with `mock_db` + `mock_redis` fixtures | ≥ 40 |
| Frontend screens | Structural source-code tests (Tamagui, apiClient, no localhost, session guards, consent guards, ALPHA labeling) | ≥ 350 |
| Crypto helpers | Token hashing, ECDSA verify, KEK/DEK envelope | ≥ 20 |

**Tools:** `pytest`, `unittest.mock`, `conftest.py` FakeRedis + mock_db.

### 1.2 Integration Tests

Test two or more real components wired together. Uses FastAPI TestClient
against the real app with dependency overrides for DB/Redis.

| Target | What | Count Target |
|--------|------|-------------|
| Auth → consent → data access | Full login → consent issue → validate → read | ≥ 15 |
| Pipeline upload → status → review → commit | End-to-end pipeline flow with FakeRedis | ≥ 20 |
| Consent engine round-trip | Issue → validate → consume → revoke → expired | ≥ 15 |
| Audit ledger chain | Write → read → tamper-detect → hash-chain verify | ≥ 10 |
| Break-glass flow | Issue with reason_code → rate-limit → TTL expiry | ≥ 10 |

**Tools:** `DualModeTestClient`, `FakeRedis`, `mock_db` from `conftest.py`.

### 1.3 End-to-End Tests (E2E)

Full user journeys through the real Next.js frontend against a running
backend. These are the demo success criteria — the final acceptance gate.

| # | E2E Scenario | Mapping |
|---|-------------|---------|
| E1 | Doctor logs in with password + MFA | Demo Step 1 |
| E2 | Doctor requests consent for a patient | Demo Step 2 |
| E3 | Patient approves consent on their device | Demo Step 3 |
| E4 | Doctor uploads a clinical document | Demo Step 4 |
| E5 | Job status screen shows extraction progress | Demo Step 5 |
| E6 | Review queue displays the flagged document | Demo Step 6 |
| E7 | Doctor edits the medication field (frequency missing) | Demo Step 7 |
| E8 | Doctor approves the allergy field | Demo Step 8 |
| E9 | Doctor commits fields to the patient record | Demo Step 9 |
| E10 | Committed data appears in the patient timeline | Demo Step 10 |
| E11 | Emergency access works with reason code + 15-min TTL | Break-glass flow |

**Tools:** Playwright (future), manual demo rehearsal (current ALPHA).

### 1.4 Security Tests

Attack-oriented tests that verify each threat in the threat model is
defended. All are marked `@pytest.mark.xfail` until implementations land.

| Target | File | Count Target |
|--------|------|-------------|
| Forged ECDSA signatures | `test_forged_signature.py` | ≥ 5 |
| Forged assurance claims | `test_forged_assurance.py` | ≥ 4 |
| Expired consent grants | `test_consent_expiry.py` | ≥ 5 |
| Cross-doctor consent reuse | `test_cross_doctor_reuse.py` | ≥ 4 |
| Tampered API payloads | `test_tampered_payload.py` | ≥ 5 |
| Unauthorized record access | `test_unauthorized_access.py` | ≥ 6 |
| Audit coverage completeness | `test_audit_coverage.py` | ≥ 5 |
| Unsafe auto-approval | `test_unsafe_autoapprove.py` | ≥ 4 |

**Tools:** `pytest`, `DualModeTestClient`, `FakeRedis`.

---

## 2. Coverage Targets Per Workstream

| Workstream | Unit | Integration | Security | E2E |
|-----------|------|-------------|----------|-----|
| WS1: Auth & NFC | ≥ 80% | ≥ 70% | ≥ 3 threats | E1 |
| WS2: Consent & Signed Approval | ≥ 85% | ≥ 80% | ≥ 6 threats | E2, E3 |
| WS3: Patient Record Viewer | ≥ 70% | ≥ 60% | ≥ 2 threats | E10 |
| WS4: Pipeline Upload & Extraction | ≥ 75% | ≥ 70% | ≥ 2 threats | E4, E5 |
| WS5: Review & Commit | ≥ 80% | ≥ 75% | ≥ 4 threats | E6–E9 |
| WS6: Emergency Access | ≥ 80% | ≥ 70% | ≥ 3 threats | E11 |
| Cross-cutting: Audit Ledger | ≥ 85% | ≥ 80% | ≥ 2 threats | All |

---

## 3. E2E Demo Success Criteria Mapping

Each criterion has a manual test script in `docs/pipeline-ui-demo-setup.md`
and will have an automated Playwright counterpart.

| Criterion | Manual Script | Playwright (future) | Current Status |
|-----------|--------------|--------------------|---------------|
| SC-1: Doctor can log in with MFA | Demo Step 1 | `e2e/login.spec.ts` | ✅ Structural tests pass |
| SC-2: Doctor can request consent | Demo Step 2 | `e2e/consent-request.spec.ts` | ✅ Structural tests pass |
| SC-3: Patient can approve consent | Demo Step 3 | `e2e/consent-approve.spec.ts` | ✅ Structural tests pass |
| SC-4: Doctor can upload a document | Demo Step 4 | `e2e/upload.spec.ts` | ✅ Structural tests pass |
| SC-5: Job status shows real-time progress | Demo Step 5 | `e2e/job-status.spec.ts` | ✅ Structural tests pass |
| SC-6: Review queue shows flagged items | Demo Step 6 | `e2e/review-queue.spec.ts` | ✅ Structural tests pass |
| SC-7: Doctor can edit a missing-value field | Demo Step 7 | `e2e/review-edit.spec.ts` | ✅ Structural tests pass |
| SC-8: Doctor can approve a flagged field | Demo Step 8 | `e2e/review-approve.spec.ts` | ✅ Structural tests pass |
| SC-9: Commit succeeds when all fields resolved | Demo Step 9 | `e2e/commit.spec.ts` | ✅ Structural tests pass |
| SC-10: Timeline shows committed data | Demo Step 10 | `e2e/timeline.spec.ts` | 🔲 Pending live backend |
| SC-11: Emergency access with reason code | Break-glass flow | `e2e/emergency.spec.ts` | ✅ Structural tests pass |

---

## 4. Test Execution Cadence

| Phase | When | What | Gate |
|-------|------|------|------|
| Pre-merge | Every PR | Unit + integration tests | 0 failures, ruff clean |
| Nightly | 02:00 UTC | Full suite incl. security (xfail expected) | No new xfails flip to fail |
| Pre-release | Before milestone | E2E manual demo rehearsal | All 11 criteria pass |
| Post-release | After deploy | Smoke tests against staging | No 5xx on critical paths |

---

## 5. xfail Convention

All security test skeletons are marked `@pytest.mark.xfail(reason="pending implementation")`.
When the corresponding defense is implemented, the test author:

1. Removes the `xfail` marker.
2. Adds a passing assertion.
3. Verifies the test passes in CI.

This ensures every threat has a test ready *before* the defense is coded,
and the test flips from xfail → pass as a visible milestone.
