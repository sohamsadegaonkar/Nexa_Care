# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **Current HEAD at start of current logical commit:** `cd8af0bce6e774a978ba2d9e728bb569ac11f694` (resolve branch ref after write for the exact ending SHA).
- **Base main SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Current implementation phase:** patient-owned secure source staging; persistence contract is committed, route registration/extraction/review/finalization remain.
- **Last completed step:** added a separate encrypted `patient-self/<patient>/...` storage namespace and patient import staging service that derives no provider/hospital authority.
- **Exact next step:** register strict `/api/v2/patient/me/external-records` upload/list/status/source endpoints, then qualify the storage/service boundary before adding patient-authority extraction orchestration.
- **Blockers:** no repository/concurrency blocker. Existing provider async extraction remains delegated-provider bound and must not be reused with synthetic provider/hospital values. The connector currently exposes no backend CI run for this branch, so backend tests remain NOT RUN.
- **Exact tests to run next:** `tests/test_patient_external_record_import_contract.py`; `tests/test_patient_external_document_storage.py`; patient-self auth/API tests once routes are registered; migration head/governance; provider document-storage regressions.
- **Files that must not currently be touched:** `app/services/clinical_access_session.py`, `app/security/clinical_access_policy.py`, `app/services/approved_access_capability.py`, `app/api/v2/consent_v3_routes.py`, `app/core/consent_gate.py`, `app/models/clinical_access_session.py`, Slice 10B migrations/qualification files unless an unavoidable dependency is proven.

## Current Scope

Authenticated patient self-import of previous/out-of-network medical records from onboarding and Records, including category selection, encrypted source retention, processing/review, patient correction, provenance-aware typed finalization, timeline integration, safe original-source view, audit/privacy/lifecycle behavior, retries, and qualification.

## Explicit Non-Scope

- Redesign/broaden ClinicalAccessSession or treatment consent.
- Grant provider authority from a patient import.
- Treat patients as synthetic providers/hospitals/clinical tenants.
- Direct OCR/LLM output into canonical clinical truth.
- Enable auto-commit.
- Create a generic final clinical JSON blob.
- Build a parallel erasure architecture.
- Create a competing Alembic head.

## Repository Baseline

- **origin/main at branch creation:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- **Branch:** `slice-11a-patient-external-record-import`.
- **Draft PR:** #47.
- **Migration head at baseline:** `20260916_clinical_access_sessions`.
- **Pre-migration recheck:** main unchanged, only main + this branch, only PR #47 open.
- **Patient import migration:** `20260916_patient_external_record_import`, linear from `20260916_clinical_access_sessions`.
- **Exact-head status after persistence commit `cd8af0...`:** Vercel reported success; no backend workflow run/status was available. Do not infer backend PASS from Vercel.

## Product Contract

New patient: account → required onboarding → Add previous medical records → category → camera/photo/PDF → secure upload/processing → patient-friendly review → correct/confirm → retain source → provenance-aware typed save → record category + timeline → continue or Skip for now.

Existing patient: Records → Add external record → category → upload → processing → review → save → categorized record + timeline.

Required categories: Prescription; Lab report; Imaging / radiology report; Discharge summary; Other medical record. Never show internal pipeline lanes or engineering vocabulary.

## Security Invariants

1. Patient ownership comes only from strict server-side patient JWT/session authority.
2. Self-import authority is separate from provider treatment consent/session/capability.
3. No client patient UUID, provider token, or synthetic provider/hospital may authorize self-import.
4. Source documents remain authenticated-encrypted and patient-bound.
5. Patient-self storage uses a distinct namespace/AAD, not a fake clinical tenant.
6. Extracted/reviewed values remain encrypted with evidence/source provenance.
7. AI/OCR never silently becomes clinical truth; auto-commit stays disabled.
8. Patient corrections remain patient-reviewed external-document provenance.
9. No source bytes, extracted PII, storage keys, or unnecessary identifiers in logs/URLs/analytics/UI.
10. Security-sensitive writes use durable transactional audit where applicable.
11. Duplicate/replay upload and finalization are idempotent or fail safely.
12. Lifecycle uses canonical merge/erasure/delete behavior.
13. Existing provider document processing must not regress.

## Existing Components Reused

| Component | Classification | Evidence / decision |
|---|---|---|
| Strict patient auth | **EXISTS** | `get_current_patient` / live patient session + DB identity. |
| Patient-self namespace | **EXISTS** | `/api/v2/patient/me`; rejects patient-ID override. |
| Patient external-record API | **PARTIAL** | service/storage are being implemented; route registration pending. |
| Provider upload | **WRONG_FLOW** for patient / **EXISTS** provider | consent/capability/hospital/provider-bound; preserve. |
| Encrypted source storage | **EXISTS / EXTENDED** | provider storage preserved; patient-self namespace adds separate AAD/path ownership. |
| Patient import persistence | **EXISTS, unqualified** | patient-owned import/candidate tables committed in `cd8af0...`; no provider/hospital/consent columns. |
| Provider extraction orchestration | **WRONG_FLOW** for patient | delegated trust is rechecked asynchronously. |
| Extraction adapters/evidence primitives | **EXISTS** | reusable only under a truthful patient authority service. |
| Typed records/timeline | **EXISTS/PARTIAL** | Medication/Lab/etc. + source links exist; category finalizers pending. |
| Audit outbox | **EXISTS** | staging service uses transactional outbox intent for accepted upload/source views. |
| Client API abstraction | **EXISTS** | add patient methods; do not add ad-hoc fetch. |
| Patient import UX | **MISSING/PARTIAL** | backend boundary first, then onboarding/Records UI. |
| Malware scanning | **NOT VERIFIED** | do not claim it exists. |

## Architecture Decisions

### 1. Separate patient authority/persistence
Patient import rows bind to patient + source document, never provider/hospital/consent. Existing provider candidate schema stays semantically intact.

### 2. No generic canonical JSON store
Import tables contain workflow/provenance metadata and encrypted candidates. `COMPLETED` requires typed-record and timeline references.

### 3. Patient correction provenance is constrained
`CORRECTED` requires patient-reviewed flag, encrypted reviewed value, and review timestamp.

### 4. Linear migration only
Patient migration revises `20260916_clinical_access_sessions` after a fresh concurrency check; no sibling head.

### 5. Distinct encrypted patient-self source namespace
`DocumentStorage` gains patient methods using `patient-self/<patient-id>/...` plus `nexa-patient-document-v1` AAD. Provider methods/path/AAD are preserved unchanged. The database `document_storage.tenant_id` remains NULL for patient-self sources, so no fake hospital/tenant provenance is created.

### 6. Source staging stops before provider-delegated extraction
`patient_external_record_import` service stores/records/audits the source and returns an UPLOADED workflow row. It deliberately does not call `process_extraction_job`; extraction gets a separate patient-authority orchestrator later.

## Work Log

### Entry 1 — Baseline/isolation
- **Timestamp:** 2026-09-16
- **Starting SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Ending SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Files:** handoff.
- **Result:** branch/handoff created; no runtime change/tests.
- **Overlap:** none visible.

### Entry 2 — Audit + draft PR
- **Timestamp:** 2026-09-16
- **Starting SHA:** `aa218f...`
- **Ending SHA:** `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`
- **Files:** handoff.
- **Result:** classified A–Q; opened draft PR #47; found provider-delegated extraction constraint.
- **Tests:** audit only, none claimed.

### Entry 3 — Patient-owned persistence contract
- **Timestamp:** 2026-09-16
- **Starting SHA:** `c7bc7998...`
- **Ending SHA:** `cd8af0bce6e774a978ba2d9e728bb569ac11f694`
- **Files:** patient import model, model registry, linear migration, model contract tests, handoff.
- **Behavior:** patient-owned job/candidates, encrypted candidate fields, correction provenance, completion refs, duplicate patient-self source hash index.
- **Tests:** backend tests NOT RUN; exact-head Vercel status SUCCESS only.
- **Overlap:** main unchanged; no external branch/PR.

### Entry 4 — Patient-self encrypted source staging
- **Timestamp:** 2026-09-16
- **Starting SHA:** `cd8af0bce6e774a978ba2d9e728bb569ac11f694`
- **Ending SHA:** resolve branch HEAD after this atomic commit.
- **Files:** `app/services/document_storage.py`; `app/services/patient_external_record_import.py`; `tests/test_patient_external_document_storage.py`; this handoff.
- **Behavior:** patient-self authenticated-encryption namespace separated from provider namespace; service validates PDF/PNG/JPEG signature+MIME, deduplicates by idempotency/hash, persists tenant-null patient source/import metadata, stages audit outbox, lists/reads ownership-bound imports/source.
- **Security impact:** no synthetic hospital/tenant; cross-patient storage reads fail ownership check; provider storage contract remains present; storage keys are not service response fields.
- **Tests run:** NOT RUN in connector environment; tests added, no PASS claimed.
- **Known failures:** none executed; API router registration still pending, extraction intentionally not enqueued.
- **Concurrent overlap:** latest pre-migration branch/PR check clean; recheck before next major phase.
- **Next:** register patient-self API, add API authority/idempotency tests, qualify.

## Test / Qualification Matrix

| Area | State | Notes |
|---|---|---|
| Patient model contract | NOT RUN | Added. |
| Patient-self storage | NOT RUN | Added local encrypted namespace tests. |
| Backend API | NOT RUN | Router registration pending. |
| Patient auth regression | NOT RUN | Pending API. |
| Provider storage/pipeline regression | NOT RUN | Mandatory because shared storage interface changed. |
| Migration single-head/governance | NOT RUN | Linear migration exists; execute on PostgreSQL. |
| PostgreSQL | NOT RUN | Required. |
| Redis | NOT RUN | Required for strict patient session tests. |
| Privacy/security | NOT RUN | Authority/log/provenance/replay tests pending. |
| Web/mobile/Next/Android/iOS/E2E | NOT RUN | UI not implemented. |
| Vercel | PASS for `cd8af0...` only | Do not reuse for later heads. |

## Open Risks / Blockers

1. Patient extraction orchestrator is not implemented; provider orchestrator cannot be reused unchanged.
2. New migration/storage/service require real test execution; no backend CI evidence currently surfaced.
3. API route registration and authority tests are pending.
4. Imaging/discharge typed finalization mapping remains to design.
5. Patient source-view route must audit and never expose storage refs.
6. Malware scanning remains unverified.

## Merge / Rebase Safety Notes

- Original base: `2a103847...`.
- Draft PR #47 exposes overlap.
- Recheck main/branches/PRs before each major phase.
- Do not touch Slice 10B protected files casually.
- Rebase only after semantic overlap review; never blind ours/theirs.
- Final qualification must match final exact head SHA.

## Final Completion Checklist

- [ ] Onboarding + Records entry points.
- [ ] Five required categories + Skip for now.
- [x] Patient-owned persistence contract implemented (not yet qualified).
- [x] Distinct encrypted patient-self storage namespace implemented (not yet qualified).
- [ ] Strict patient-owned upload/status/list/source API registered and qualified.
- [ ] Upload corruption/type/size/replay/storage/extraction failure behavior qualified.
- [ ] Patient-authority extraction/evidence processing implemented.
- [ ] AI/OCR cannot auto-commit.
- [ ] Patient-friendly review/correction implemented.
- [ ] Patient edits retain provenance.
- [ ] Typed record + timeline finalization implemented.
- [ ] Safe original source view implemented/qualified.
- [ ] Retry/resume/cancel lifecycle implemented.
- [ ] No provider authority/treatment-consent mutation.
- [ ] Provider regressions pass.
- [ ] Privacy/adversarial/audit/lifecycle tests pass.
- [ ] Exactly one Alembic head, migration qualified.
- [ ] Frontend/mobile/build/native/E2E gates green as applicable.
- [ ] Final exact SHA recorded and draft PR #47 ready for review.
