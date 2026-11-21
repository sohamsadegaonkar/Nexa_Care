# Investor demo QA qualification — 21 November 2026

**Version:** NEXA-INV-QA/1.0 (frozen execution baseline)  
**Target:** draft PR #101, initial combined revision `9f03d3fdd4d7d63b50c80a47fd151bb0d12ebe7b`; any later HEAD must be revalidated.  
**Objective:** one truthful, reproducible, working *synthetic* Hospital X → patient → Hospital Y original-document demo, while preserving approval, ownership, erasure, provenance, revocation and audit.  
**Owner:** executing QA engineer; physical-device operator and independent reviewers not yet assigned.  
**Scope:** exact-source HTTP API with disposable PostgreSQL/Redis; live loopback ClamAV scanner; Next/Chromium browser smoke and login shell with mocked API clearly separated from full-stack browser; signed original document access on enrolled software P-256 key; physical phone/NFC/browser observation as explicit release gate.  
**Exclusions:** real patient data, payments, public dev environment, AWS/Azure resource creation, production or regulatory approval, hospital integrations, clinical accuracy beyond the bundled fixture, and automatic PR merges.

## Security/regulatory change assessment

Applicable SEC-001/002/003/005/011/013/014/015/016/020/021/023: no bearer or patient data in URLs/logs, explicit patient/provider/facility and document-specific grant, durable deny/audit, erasure, source authenticity, no fabricated clinical verification, actual device boundary. Applicable India regulatory baseline REG-001/003 and source/purpose controls remain **human-review pending**; no legal-compliance claim. Identity Evidence Disclosure Policy remains E0 metadata-only non-release. Engineering constitution: synthetic data, patient control, minimum disclosure, explicitly separate emergency authority, no inferred results. This QA work modifies no clinical data, consent semantics, authorization, audit event definitions, identity, AI fact semantics, emergency scope or regulated access policy; new tests should use disposable synthetic records only.

## Scope matrix and order

| Order / ID | Evidence tier | Test scope | Planning / current outcome |
| --- | --- | --- | --- |
| 01 / INV-QA-01 | GitHub readback | exact PR ancestry / draft-only release gate | **PASS** at initial SHA; repeat on closing HEAD |
| 02 / INV-QA-02 | hosted CI real DB | general backend and PostgreSQL/Redis | **PASS** at initial SHA, [run 44867613372](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44867613372) |
| 03 / INV-QA-03 | hosted CI native compile + tests | frontend and production web build | **PASS** at initial SHA, [run 44867613327](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44867613327) |
| 04 / INV-QA-04 | hosted CI with real scanner | ClamAV scanner contract (baked synthetic signatures) | **PASS** at initial SHA, [run 44867613426](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44867613426); official-current definitions NOT_RUN |
| 05 / INV-QA-05 | SDK doubles / dependency check | Azure Blob, Key Vault and Layout adapters | **PASS OFFLINE ONLY** at initial SHA, [run 44867613421](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44867613421), 27 tests |
| 06 / INV-QA-06 | hosted CI real DB/Redis and real scanner | combined signed P1B HTTP rehearsal with `P1B_REHEARSAL_CLAMD=1` | **NOT_RUN**; add dedicated workflow then execute |
| 07 / INV-QA-07 | hosted real Chromium browser, mocked API | login shell, error states, responsive doctor dashboard | **NOT_RUN**; add dedicated browser workflow then execute |
| 08 / INV-QA-08 | physical observed browser + backend | actual doctor consent flow, original PDF preview and revocation/expiry/disconnect cleanup | **BLOCKED**; need running real local/demo backend and actual browser |
| 09 / INV-QA-09 | physically enrolled phone/NFC | device P-256 signing, biometrics, physical NFC, consent denial/approval | **BLOCKED**; no physical phone/NFC instrumentation connected |
| 10 / INV-QA-10 | live Azure account | Blob, Key Vault and Document Intelligence read/write/unwrap/extract, rollback and costs | **BLOCKED**; no approved isolated account/resources |
| 11 / INV-QA-11 | human reviews | security, privacy and clinical sign-off | **BLOCKED**; independent reviewers required |

All case results are **revision-specific**; an inherited PASS from P1B #99 or Azure #100 does not qualify the combined revision. The old [July alpha signoff](../demo-readiness-signoff.md) refers to a different system and cannot be treated as authority for this build.

## Frozen structured test cases

Each case uses a synthetic-only isolated fixture; no credentials or patient identifiers enter evidence. Record step outcomes, exact commit, runner/device, timestamps, redacted evidence, failure reason, and cleanup as a later dated execution result. FAIL/BLOCKED/NOT_RUN cannot be promoted to PASS through a unit-test success.

### INV-QA-01 — repository/source governance (P0, read-only)
- **Source/oracle:** Repository AGENTS.md; owner no-merge instruction; GitHub API. Technique: configuration/negative release.
- **Environment/roles:** GitHub PR #99/#101, anonymous synthetic QA context; no patient/provider account; starting exact refs.
- **Precondition/setup:** Inspect PR metadata and protected main HEAD; no changes applied.
- **Steps:** (1) Read PR #99, #101 heads and draft/merged flags; *expect* both draft and not merged. (2) Read main SHA; *expect* unchanged. (3) Compare P1B HEAD to integration HEAD; *expect* integration includes latest P1B ancestor and opt-in Azure additions, with no silent policy/production changes.
- **Final/downstream:** Both draft/unmerged; integration SHA documented; review only. **Cleanup:** none. **Result:** PASS at initial `9f03d3f`, GitHub API readback on 2026-11-21; repeat on HEAD change. No human merge approval inferred.

### INV-QA-02 — real database/backend security regression (P0, automated)
- **Source/oracle:** `.github/workflows/ci.yml`; signed clinical and P1B contract. Technique: positive, negative, concurrency, recovery.
- **Environment/roles:** GitHub Ubuntu runner, disposable PostgreSQL 16 + Redis 7, software signing fixture with synthetic Hospital Y clinician and patient.
- **Precondition/setup:** Exact candidate checkout, install pinned Python requirements, apply/test forward-only schema in named disposable qualification DB; no existing patient data.
- **Steps:** (1) Run quality partition; *expect* lint + pure tests success with zero skips. (2) Run PostgreSQL partition; *expect* migrations/data constraints pass and zero skips. (3) Run Redis partition including P1B original-access integration; *expect* unauthorized 403, approved one-time PDF delivery, concurrent one success, revoke 403 and exactly one opening audit. (4) Verify each JUnit result and exact SHA; *expect* no failed/skip/xfail disguised as success.
- **Final/downstream:** CI all partitions pass on same SHA; **cleanup:** ephemeral runners removed by GitHub. **Result:** PASS at initial `9f03d3f` via run 44867613372, not a physical-browser or hardware-key test.

### INV-QA-03 — frontend build and native compile (P1, automated)
- **Source/oracle:** `ci-frontend.yml`; original preview/cleanup tests; Next release build. Technique: positive/negative frontend regression.
- **Environment/roles:** GitHub runner; mocked synthetic API, doctor/patient components, Android/iOS compiler targets.
- **Steps:** (1) Run Vitest; *expect* all doctor original preview/revoke/unmount cases pass, no skips. (2) Build Next; *expect* successful production assets. (3) Compile Android/iOS native surfaces; *expect* success (no runtime qualification).
- **Final/downstream:** Source-level frontend readiness only; **cleanup:** ephemeral CI. **Result:** PASS at initial `9f03d3f` via run 44867613327; no observed phone/browser.

### INV-QA-04 — actual ClamAV daemon, baked synthetic signatures (P0, automated)
- **Source/oracle:** scanner integration CI and source safety policy. Technique: clean/malicious/unavailable.
- **Environment/roles:** runner container with scanner over loopback, synthetic PDF; no user account.
- **Steps:** (1) Build pinned scanner image; *expect* digest output. (2) Start ClamAV and health-readiness check; *expect* scanner ready. (3) Run clean, synthetic malicious, and failure-mode integration cases; *expect* exact source guard behavior and zero silent fallbacks.
- **Final/downstream:** Real engine proof **with synthetic signature data**, not current official definitions. **Cleanup:** stop container and discard ephemeral state. **Result:** PASS at initial `9f03d3f`, run 44867613426. Current official database freshness remains NOT_RUN.

### INV-QA-05 — Azure provider contracts (P1, automated)
- **Source/oracle:** `azure-foundation-ci.yml`, Azure runbook; synthetic-only opt-in. Technique: confidentiality, wrong-owner, tampering, contract.
- **Environment/roles:** runner with Azure SDK installed and provider doubles; no live subscription/resources.
- **Steps:** (1) Install compatible SDKs; *expect* pip check/import success. (2) Check Blob patient ownership, AES ciphertext integrity, exact original byte-size binding; *expect* wrong owner/size failure. (3) Check version-pinned Key Vault DEK binding and Layout exact source-spans; *expect* no fabricated clinical confidence/fallback. (4) Check deployment renderer; *expect* private non-deployable candidate.
- **Final/downstream:** Provider contract eligible for **later live** qualification only. **Cleanup:** no cloud resources created. **Result:** PASS at initial `9f03d3f`, 27 passed, 0 skipped, run 44867613421.

### INV-QA-06 — one integrated HTTP rehearsal with REAL clamd (P0, proposed automated)
- **Source/oracle:** P1B `docs/governance/P1B_ORIGINAL_DOCUMENT_ACCESS.md`, `tests/integration/test_original_document_access.py`. Technique: state transition, negative, concurrency and audit integrity.
- **Environment/roles:** disposable PostgreSQL/Redis + loopback real clamd with synthetic control signatures; Hospital X synthetic PDF, Hospital Y authenticated provider, authenticated patient, enrolled *software* P-256 test key.
- **Precondition/setup:** create isolated disposable DB and empty local encrypted storage; current Alembic head; scanner readiness; never reuse live data.
- **Steps:** (1) Import PDF, extract exact printed value, review and save; *expect* reference + encrypted original. (2) Provider discover/consent and list inventory; *expect* correct owner and hospital. (3) Request original before document approval; *expect* 403. (4) Sign explicit document approval; *expect* approved status. (5) Concurrently open; *expect* exactly one 200 with byte-identical PDF and one 403, one audit outbox event. (6) Deny another request and revoke an approved-but-unopened one; *expect* both cannot release bytes. (7) Induce scanner failure; *expect* fail-closed response and no consumed claim.
- **Final/downstream:** successful HTTP test with scanner operational and source-verified synthetic report. Does not establish phone, live Azure or UI rendering. **Cleanup:** CI disposable DB, Redis namespace, files and scanner container only. **Result:** NOT_RUN at matrix freeze; dedicated job proposed.

### INV-QA-07 — real Chromium UI smoke (P1, proposed automated)
- **Source/oracle:** `nexa-client/apps/next/e2e/product-polish.spec.ts`; Next browser contract. Technique: keyboard, viewport, visual interactive.
- **Environment/roles:** GitHub Ubuntu Chromium with loopback Next production build; mocked API session (the test's synthetic provider), *not* clinical authorization.
- **Precondition/setup:** immutable frontend dependencies; same exact commit; local-only host; synthetic API interception, no external patient data.
- **Steps:** (1) Open login; *expect* accessible email/password labels, visible action and keyboard submission. (2) Submit synthetic invalid credentials; *expect* safe error and no client exception. (3) Render dashboard at 360, 768, 1440 px with synthetic authenticated response; *expect* no horizontal overflow, expected doctor navigation and theme switch. (4) Show patient entry; *expect* direction to mobile app. Capture sanitized screenshots on failure.
- **Final/downstream:** real Chromium frontend shell smoke with **mocked API**, not integrated logged-in doctor/patient UI. **Cleanup:** stop loopback Next, browser and ephemeral runner. **Result:** NOT_RUN at matrix freeze; dedicated browser CI proposed.

### INV-QA-08 — observed clinician browser original preview/revocation (P0, physical)
- **Source/oracle:** P1B original view and 8-second preview lease requirements. Technique: positive/expiry/network-loss.
- **Environment/roles:** actual desktop Chrome or Chromium session against private synthetic API; enrolled real patient phone, synthetic Hospital X and Y; authenticated provider MFA, live scanner.
- **Precondition/setup:** run private disposable app + PostgreSQL + Redis + scanner; seed exactly one synthetic patient/import and clinician, confirm valid current device key; no mock signatures.
- **Steps:** (1) In browser authenticate clinician with real MFA; *expect* provider and affiliation shown. (2) Discover patient via normal UI, request history; *expect* pending until phone approves. (3) Open Documents, choose exact saved PDF; *expect* 403 before separate approval and pending state. (4) Patient signs exact document decision on phone; *expect* preview of byte-exact original in actual browser and no remote PDF viewer. (5) Revoke while preview open, measure elapsed time with recorder; *expect* closed within established 8s lease and subsequent open denied. (6) Repeat with expiry then disconnected network; *expect* bounded preview closure without restored access. (7) Check audit readback with exactly one successful opening for one-time claim.
- **Final/downstream:** real browser + backend/device testimony; redacted screen capture and stopwatch evidence. **Cleanup:** revoke grants, log out, remove only disposable fixtures. **Result:** BLOCKED: no connected running instance, browser session and enrolled phone; no false PASS.

### INV-QA-09 — physical P-256 device signing and NFC tap (P0, physical)
- **Source/oracle:** SEC-016/020; patient access flow; P1B runbook. Technique: positive/negative/replay/revocation.
- **Environment/roles:** owner's Android/iOS real enrolled device with biometric/key provider and genuine NFC reader/tag; synthetic patient + clinician.
- **Precondition/setup:** app installed from exact revision, signed-in synthetic patient, device enrolled and active, NFC linked to synthetic patient, current authorization server.
- **Steps:** (1) Tap actual NFC tag with supported reader; *expect* fresh discovery handle only, no unauthorized record. (2) Doctor requests consent; *expect* patient device displays correct facility/provider, purpose, expiry. (3) Patient authenticates/signs; *expect* server verifies enrolled hardware key and grants correct authority. (4) Deny and revoke follow-ups, attempt reuse; *expect* terminal fail-closed states and patient history.
- **Final/downstream:** physical evidence with OS/device/app build and redacted video; **cleanup:** revoke test grants. **Result:** BLOCKED: cannot observe/interact with an owner's phone/NFC hardware from repository tools. Do not interpret software ECDSA or native compilation as this test.

### INV-QA-10 — isolated LIVE Azure synthetic service smoke (P1, live)
- **Source/oracle:** Azure integration runbook, exact config and data isolation. Technique: identity/auth, storage crypto, OCR source provenance, rollback/cost.
- **Environment/roles:** separately approved paid or free eligible private Nexa subscription, *never CampusPool resources*; synthetic-only account, no user PHI.
- **Precondition/setup:** explicit owner cost/provisioning authorization, allowed regions/SKUs, dedicated managed identity/key vault, private Blob and Document Intelligence, PostgreSQL/Redis TLS and scanner, exact deployment manifest review.
- **Steps:** (1) Verify subscription usage and resource ACLs; *expect* no public storage or shared identity. (2) Encrypt/save/read/destroy synthetic object; *expect* authenticated same-owner bytes and fail-closed wrong-owner. (3) Wrap/unwrap synthetic DEK in actual Key Vault; *expect* correct key/version binding and denied invalid patient. (4) Extract known one-page synthetic report in actual Document Intelligence; *expect* faithful source spans or explicit incomplete/failed state, never fabricated clinical truth. (5) Verify costs and controlled rollback.
- **Final/downstream:** live Azure service runtime evidence, not permission for real-patient pilot. **Cleanup:** only approved test resources, no automatic deletion. **Result:** BLOCKED — no account/resource authorization or credentials, no deployment.

### INV-QA-11 — independent release assessment (P0, human)
- **Source/oracle:** Repository AGENTS and Indian regulatory baseline. Technique: independent security/clinical/privacy review.
- **Environment/roles:** named independent clinical, security, privacy reviewers; no patient record exposure.
- **Steps:** (1) Review patient document authority, erasure, audit, device UI; *expect* documented risks and owner. (2) Review claims and screenshot labels, medical uncertainty; *expect* no simulated hospital integration claimed as verified. (3) Record actual approvals or blockers with date/revision; *expect* signed review not an agent self-review.
- **Final/downstream:** explicit decision for investor demo, distinct from commercial/clinical production approval. **Cleanup:** none. **Result:** BLOCKED pending independent reviewers.

## Release rules and evidence ledger

- **Investor-demo readiness = HOLD** until INV-QA-08/09 pass and actual browser/phone evidence is attached. Automated PASS cannot override BLOCKED.
- Do not merge PR #99 or #101 or change production/cloud state without owner's explicit approval.
- On any test failure, update its case with exact step, expected/actual, commit, runner and run link; add follow-up defect and fix on integration branch only; retest all affected boundaries.
- Record all new CI job names, exact SHA, run links, passed/failed/skipped counts in a dated addendum. Do not overwrite this frozen matrix or assert an obsolete July GO verdict.

## 21 November 2026 — Browser regression diagnosis and fix candidate

Observed on Ubuntu 24.04 real Chromium at `969d3e9`: login and 768/1440 workspace checks passed; 360px doctor header exceeded viewport and the patient web-entry assertion expected a retired heading. Before that, the original `http://127.0.0.1` browser origin was correctly refused by the production same-origin API policy; the harness now uses the reserved `https://doctor.example.test` origin and intercepts all static assets to a loopback-only Next server. No production origin policy was changed.

The provider shell has bounded/wrapping identity, hospital and controls at small widths. Patient entry now checks the actual patient mobile-number login screen, with verification button disabled until valid input, rather than asserting a deprecated mobile-only signpost. These are UI/test corrections, not authentication relaxations. Fresh Chromium and full CI must pass on the updated HEAD before changing case INV-QA-07 to PASS. Screenshot/error-context evidence retained in the failed run artifacts for [44870977653](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44870977653). No physical signing, NFC, live Azure or true browser-to-real-backend session observed.

## 21 November 2026 — Mobile emergency panel root cause

Real Chromium [run 44871575362](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44871575362) narrowed the 360px overflow to the Emergency Access warning text container: viewport=360, page scrollWidth=726, warning flex child left=99 and width=627. Four of five tests passed (provider login, 768/1440 workspace, current patient phone login). The prior header and stale patient test are no longer failing. This targeted layout fix bounds the Emergency Access panel and allows its warning text flex child to shrink while retaining full caution text and distinct emergency route. New exact-head Chromium CI must pass; do not mute the overflow assertion.
