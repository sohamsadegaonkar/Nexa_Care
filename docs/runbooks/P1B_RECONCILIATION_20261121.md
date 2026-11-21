# PR #99 — P1B reconciliation and branch cleanup review (21 Nov 2026)

**Baseline:** `main` `4723a118ce681489f7800516564d762f7a4fc5af`; old PR #99 `bc3abe74fedc32f1f3dc244020980981997bf3e1`.
**Status:** candidate integration; fresh verification required. No physical signing, NFC, live Azure or human independent review has been performed.

## Governing controls

Applicable SEC-001/002/003/005/011/013/014/015/016/020/023; REG-001/003/017; identity E0 non-disclosure; provider/hospital/patient server-authoritative eligibility, device-bound signatures, one-time audited document opening and erasure. Patient source, consent, crypto, document storage, audit and browser-preview paths are affected. No policy expansion, production activation, identity release or clinical verification claim is authorized.

## Frozen verification matrix

| ID | Expected result | Current status |
| --- | --- | --- |
| P1B-REV-01 | Reconciled candidate includes latest main without erasing optional Azure protections; PR diff reviewed | NOT_RUN |
| P1B-REV-02 | Strict source graph: reject duplicate/incomplete/unsafe metadata with 404, no original bytes | NOT_RUN; run predecessor adversarial tests |
| P1B-REV-03 | One-time original API under real PostgreSQL/Redis, signed software key: deny before approval; one byte-exact copy and one 403 on race, one outbox event; revoke fails closed | NOT_RUN; new head |
| P1B-REV-04 | Real ClamAV service with current definitions and unsafe source denial | NOT_RUN; new head |
| P1B-REV-05 | Azure SDK double: strict expected patient plaintext size, wrong owner/corruption rejection | NOT_RUN; new head |
| P1B-REV-06 | Full backend/frontend CI, Next production build, Android/iOS native compilation on the reconciled head | NOT_RUN; new head |
| P1B-REV-07 | Real enrolled Android key, biometric approval, document-specific signature | BLOCKED: owner device not connected |
| P1B-REV-08 | Real clinician browser connected to backend, timed revoke/expiry/offline preview closure | BLOCKED: observed session not completed |
| P1B-REV-09 | Physical NFC discovery | BLOCKED: owner's Android lacks NFC |
| P1B-REV-10 | Independent clinical, security, privacy review and live Azure service checks | BLOCKED: named reviewers/live resource authority unavailable |

Planning/CI records must never be labeled equivalent to observed device evidence. Latest CI run/results should be linked after completion, using this branch's exact commit.

## Foundation disposition

The separate `feat/original-document-access-foundation-20261120` branch carried three unique files: `app/services/provider_original_document_eligibility.py`, `tests/test_provider_original_document_eligibility.py`, and `docs/governance/ORIGINAL_DOCUMENT_ACCESS_P1B_PROPOSAL.md`. This candidate incorporates the actual validator, adversarial tests, and a historical/superseded copy of the proposal, so its content can be retained after the PR #99 check passes. The old branch's *commits* remain on another line of history until explicitly merged or deleted.

## Links

[PR #99](https://github.com/sohamsadegaonkar/Nexa_Care/pull/99) · [Original access governance](../governance/P1B_ORIGINAL_DOCUMENT_ACCESS.md) · [Historical foundation](../governance/ORIGINAL_DOCUMENT_ACCESS_P1B_PROPOSAL.md) · [PR #101 investor qualification](https://github.com/sohamsadegaonkar/Nexa_Care/blob/71fe35bd6f6b38dae32d5bf89c81b09d3ce11438/docs/runbooks/INVESTOR_DEMO_QUALIFICATION_20261121.md)
