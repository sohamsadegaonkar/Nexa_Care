# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **Current exact HEAD before this qualification-repair commit:** `ae29e8759942c5ffda8dc45916609628207bc684`
- **Original base SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Current origin/main SHA observed before this commit:** `238c59b7fe94cc04213063f55750f3d727a062c7`
- **Current Task-1 migration:** `20260916_patient_external_record_import`
- **Task-1 migration parent:** `20260916_clinical_access_sessions`
- **Approved pilot/deployment migration head on current main:** remains `20260916_clinical_access_sessions`; this draft feature branch does **not** change `scripts/run_pilot_migrations.py` or global pilot governance to imply Task-1 is release-approved.
- **Current phase:** Phase A qualification repair for strict patient-self upload/list/detail/source API. Patient-authority extraction is prepared conceptually but must not land until this API/storage/migration qualification step is green.
- **Last completed step:** exact-head CI on `ae29e875...` ran all three backend partitions and exposed a bounded set of route/migration/storage-governance regressions. This commit repairs those regressions without rebasing or touching Slice-10B authority behavior.
- **Exact next step:** advance the branch to this repair commit, update PR #47, run exact-head CI again, inspect all three backend partitions, and fix any remaining failures. Only after a green API/storage/migration checkpoint should patient-authority extraction orchestration be committed.
- **Tests actually PASS on `ae29e875...`:** Ruff/lint PASS. No backend partition is PASS because each partition had pytest failures.
- **Tests actually FAIL on `ae29e875...`:** Partition A: **3880 passed, 8 failed, 429 deselected**. Partition B: **297 passed, 2 failed**. Partition C: **128 passed, 2 failed**.
- **Observed failure roots:** two new patient-specific audit event names were not registered; four intentional patient routes were not in the route allowlist; migration graph tests/CI shared DB still treated the Slice-10B predecessor as repository head; provider-only `DocumentStorage` test doubles became abstract when patient methods were added.
- **Repair decisions in this commit:** reuse existing `DOCUMENT_UPLOADED` / `DOCUMENT_SOURCE_VIEWED` audit vocabulary; make patient-storage methods concrete fail-closed defaults so legacy/provider implementations remain instantiable; add the four intentional patient-self routes to route governance; migrate CI disposable shared DB to Task-1 head; update migration ancestry tests to preserve `patient-search → clinical-access → patient-external-import`; keep approved pilot migration runner/governance pinned to the current approved main head and mock that condition in its isolated success-path unit test.
- **Tests NOT RUN for this repair commit:** local pytest cannot run because the sandbox cannot resolve `github.com`; exact-head CI is required after branch advance. PostgreSQL/Redis must not be marked PASS until that run completes.
- **Current blockers:** no implementation blocker. Local checkout/network unavailable. Malware scanning remains **NOT CURRENTLY VERIFIED / NOT IMPLEMENTED**. Full decoder-level PDF/image corruption validation remains unverified beyond MIME/signature and obvious truncation checks.
- **Concurrent PRs/workstreams:** only PR #47 was open at the latest check. Slice 10B continues to advance on `main`; latest observed main commit `55bb0a25...` is `security(consent): revoke durable clinical session with patient consent`.
- **Files protected from behavioral edits:** `app/services/clinical_access_session.py`, `app/services/clinical_access_session_store.py`, `app/models/clinical_access_session.py`, `app/security/clinical_access_policy.py`, `app/services/approved_access_capability.py`, `app/api/v2/consent_v3_routes.py`, `app/core/consent_gate.py`, `alembic/versions/20260916_clinical_access_sessions.py`, and Slice-10B authority semantics. This repair only updates cross-repository test/CI contracts required by the Task-1 child migration.
- **Operational cleanup:** accidental branch `tmp-inspect-fe57-patient-import` still points to the old unchanged Task-1 head and has no changes. The available connector exposes no delete-ref action; delete it when ref-deletion access exists. Do not use it.

## Current Scope

Authenticated patient self-import of external medical records from onboarding and Records: category selection, encrypted source retention, patient-authority extraction, patient-friendly review/correction, typed-record finalization where semantics exist, timeline projection, safe source view, retries, audit/privacy/lifecycle behavior, and qualification.

## Explicit Non-Scope

- Redesign/broaden `ClinicalAccessSession`, Signed Consent V3, provider treatment consent, or provider record-write authority.
- Synthesize provider/hospital/tenant authority for patient import.
- Turn OCR/AI directly into canonical clinical truth or enable auto-commit.
- Create a generic final medical-record JSON blob.
- Reinterpret Medication as Prescription when semantics do not match.
- Create sibling/merge migrations merely because main moves.
- Rewrite global erasure architecture.

## Product Contract

New patient: account → required onboarding → **Add previous medical records** → category → camera/photo/PDF where supported → encrypted upload → processing → patient-friendly review/correction → retained source → provenance-aware typed save where safe → record category + timeline → continue onboarding or **Skip for now**.

Existing patient: Records → **Add external record** → category → source upload → processing → review → save → categorized record + timeline.

Patient-facing categories: Prescription; Lab report; Imaging / radiology report; Discharge summary; Other medical record. Never show provider-pipeline lanes, candidate IDs, workflow IDs, storage refs, object keys, consent internals, or extractor implementation details.

## Security Invariants

1. Patient ownership derives only from strict server-side patient JWT/session authority.
2. Client patient UUIDs, provider capabilities, treatment consent, ClinicalAccessSession, or synthetic provider/hospital identities cannot authorize patient import.
3. Patient source storage uses a distinct authenticated-encryption namespace/AAD and tenant remains NULL for patient-self source rows.
4. AI/OCR output remains untrusted until explicit review; auto-commit remains disabled.
5. Original extraction/evidence is retained when the patient corrects a value; patient correction is not clinician verification.
6. Source bytes, storage refs/keys, encryption metadata, raw extracted PII, and unnecessary health values never enter URLs/logs/analytics/API metadata.
7. Security-sensitive transitions remain auditable through existing durable audit/outbox semantics.
8. Same-patient idempotency replay is accepted only for the same category/content digest; changed semantics conflict; different patients may reuse key strings independently.
9. Patient merge/erasure/delete behavior must use canonical lifecycle architecture.
10. Provider document processing/storage must not regress.

## Architecture Decisions

### Patient-owned persistence and source namespace
Task-1 import/candidate rows bind directly to patient + source document and contain no provider/hospital/treatment-consent authority columns. Patient source objects use `patient-self/<patient>/...` with patient-specific AAD, separate from provider tenant storage.

### Patient API authority
`/api/v2/patient/me/external-records` derives patient identity from `get_current_patient`; no client patient identifier is accepted. Upload/list/detail/source responses use patient language and `Cache-Control: private, no-store`.

### Audit vocabulary
Patient upload/source view now reuse existing semantically compatible `DOCUMENT_UPLOADED` and `DOCUMENT_SOURCE_VIEWED` events with `authority=patient_self` metadata. Do not create duplicate event vocabulary without a semantic need.

### Feature-branch schema head vs approved deployment head
The repository head on this Task-1 branch is `20260916_patient_external_record_import`. CI disposable databases must migrate there to qualify the feature. The production/pilot migration runner remains pinned to the last approved main head (`20260916_clinical_access_sessions`) until final integration/release approval; its success-path unit test explicitly mocks an approved repository-head condition. This avoids changing Slice-10B deployment governance merely to make a draft PR green.

### Storage interface compatibility
The three original provider storage methods remain abstract. New patient-self storage methods are concrete fail-closed defaults raising `DocumentStorageError`; Local/S3 implementations override them. Existing provider-only test doubles and integrations therefore remain valid while unsupported patient-self calls fail closed.

## Work Log

### Entry 1 — Baseline/isolation
- **Timestamp:** 2026-09-16
- **Starting SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Ending SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Behavior:** created isolated branch and living handoff.
- **Tests:** none; documentation-only step.

### Entry 2 — Architecture audit + draft PR
- **Starting SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Ending SHA:** `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`
- **Behavior:** audited patient/provider authority, secure storage/extraction/evidence, typed records/timeline, auth and client paths; opened draft PR #47; identified provider-delegated extraction constraint.
- **Tests:** none; audit-only.

### Entry 3 — Patient-owned persistence
- **Starting SHA:** `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`
- **Ending SHA:** `cd8af0bce6e774a978ba2d9e728bb569ac11f694`
- **Behavior:** added patient-owned import/candidate persistence and linear migration `20260916_patient_external_record_import` from `20260916_clinical_access_sessions`.
- **Tests:** backend NOT RUN at commit time; historical Vercel success only, not backend evidence.

### Entry 4 — Encrypted patient source staging
- **Starting SHA:** `cd8af0bce6e774a978ba2d9e728bb569ac11f694`
- **Ending SHA:** `21b7d4822688774f1d11d2ae0a77e0403b8b7584`
- **Behavior:** distinct patient-self encrypted namespace, patient staging service, storage contract tests.
- **Tests:** NOT RUN at commit time.

### Entry 5 — Strict patient external-record self-service API
- **Starting SHA:** `21b7d4822688774f1d11d2ae0a77e0403b8b7584`
- **Ending SHA:** `ae29e8759942c5ffda8dc45916609628207bc684`
- **Resulting tree:** `c1df6a15380cadb01ec1a8eb27e5fcb110a3a310`
- **Prepared-tree provenance:** original raw tree `fe57b9985e893f55618a63f2f78f2a5c38669faf` was inspected through dangling commit `995fc7c26677e87e456113ed77f25cfadd7a2bb7` and not advanced unchanged because its idempotency behavior was semantically unsafe.
- **Behavior:** strict upload/list/detail/source API; server-derived patient authority; patient-scoped semantic idempotency; no-store responses; stable error mapping; patient-safe statuses; obvious malformed-envelope rejection.
- **Tests before commit:** NOT RUN locally.

### Entry 6 — Exact-head API/storage qualification findings and repair
- **Starting SHA:** `ae29e8759942c5ffda8dc45916609628207bc684`
- **Ending SHA:** resolve from branch ref after this atomic repair commit; the commit cannot contain its own not-yet-created SHA.
- **Observed main before commit:** `238c59b7fe94cc04213063f55750f3d727a062c7`.
- **Exact-head CI run inspected:** GitHub Actions run `35130197618` for `ae29e875...`.
- **Lint:** PASS.
- **Partition A:** FAIL — 3880 passed, 8 failed, 429 deselected. Failures were audit vocabulary, four-route allowlist, migration-head assertions, and pilot migration hardening assuming branch repository head equals approved deployment head.
- **Partition B:** FAIL — 297 passed, 2 failed. One provider-trust CLI schema mismatch from CI DB at predecessor head; one provider async-runtime test double blocked because patient storage methods were abstract.
- **Partition C:** FAIL — 128 passed, 2 failed. Both provider-trust CLI journeys saw CI DB at predecessor schema head.
- **Repair files:** shared document storage abstraction + Task-1 storage regression test; patient import service audit event reuse; route registration allowlist; CI shared-DB target; migration ancestry tests; pilot deployment hardening test; this handoff.
- **Security consequence:** no authority broadening. Unsupported patient-self storage fails closed. Approved pilot deployment head remains unchanged. Provider storage compatibility restored.
- **Local tests:** NOT RUN — sandbox DNS cannot resolve GitHub and no checkout exists.
- **Exact next action:** commit repair, update draft PR body/head, inspect the new exact-head CI. Do not land extraction on top of a red qualification head.

## Test / Qualification Matrix

| Area | State | Evidence |
|---|---|---|
| Ruff/lint on `ae29e875...` | PASS | GitHub Actions Partition A lint step. |
| Backend Partition A on `ae29e875...` | FAIL | 3880 passed / 8 failed / 429 deselected. |
| Backend Partition B on `ae29e875...` | FAIL | 297 passed / 2 failed. |
| Backend Partition C on `ae29e875...` | FAIL | 128 passed / 2 failed. |
| Focused Task-1 tests after repair | NOT RUN | Await repaired exact-head CI. |
| PostgreSQL after repair | NOT RUN | Await repaired exact-head CI. |
| Redis after repair | NOT RUN | Await repaired exact-head CI. |
| Provider storage/pipeline regressions after repair | NOT RUN | Await repaired exact-head CI. |
| Migration graph after repair | NOT RUN | Await repaired exact-head CI. |
| Malware scanning | NOT VERIFIED / NOT IMPLEMENTED | Do not claim otherwise. |
| Deep decoder-level corruption checks | NOT VERIFIED | Current API only verifies MIME/signature + obvious truncation envelope. |
| Frontend/mobile/native/E2E | NOT RUN | Frontend intentionally deferred. |

## Open Risks / Blockers

1. API/storage qualification repair must go green before extraction lands.
2. Patient-authority extraction/review/finalization/retry/lifecycle/frontend remain incomplete.
3. Main is actively evolving Slice 10B; continue overlap checks but do not chase it with repeated rebases.
4. Typed Prescription/Imaging/Discharge semantics still require finalization audit; never relabel Medication as Prescription.
5. Malware scanning and decoder-level file validity remain unresolved product/security gaps.
6. Temporary inspection branch cleanup remains pending due connector capability.

## Merge / Rebase Safety Notes

- Original Task-1 base: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Main observed before this repair: `238c59b7fe94cc04213063f55750f3d727a062c7`.
- Do not rebase merely because branch is behind. Current required main integration is limited to current test/CI contract content; no Slice-10B authority implementation has been copied or modified.
- Task-1 migration remains one linear child of `20260916_clinical_access_sessions`; no merge migration.
- Re-check current main/open PRs before every major step and before final integration.
- Final release qualification must run on one frozen exact SHA after deliberate integration/rebase.

## Final Completion Checklist

- [x] Patient-owned import persistence implemented (qualification in progress).
- [x] Distinct encrypted patient-self source storage implemented (qualification in progress).
- [x] Strict patient upload/list/detail/source API implemented (qualification in progress).
- [ ] API/storage/migration/provider regression CI green after Entry 6 repair.
- [ ] Patient-authority extraction/evidence orchestration.
- [ ] Explicit patient review/correction with original-value provenance.
- [ ] Safe typed-record finalization where canonical semantics exist.
- [ ] Timeline projection exactly once after canonical persistence.
- [ ] Retry/resume/cancel behavior.
- [ ] Merge/erasure/delete lifecycle qualification.
- [ ] Onboarding + Records frontend entry points and Skip for now.
- [ ] Full backend PostgreSQL/Redis/security partitions green with zero required skips.
- [ ] Full frontend/build/native/E2E qualification as applicable.
- [ ] Final exact SHA frozen, PR body current, draft PR integration-ready but not merged without release-owner instruction.
