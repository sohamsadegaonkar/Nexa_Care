# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **Current exact HEAD before this API commit:** `21b7d4822688774f1d11d2ae0a77e0403b8b7584` — resolve the branch ref immediately after the atomic commit for its exact ending SHA; a Git commit cannot contain its own not-yet-created SHA.
- **Original base SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Current origin/main SHA observed:** `be5a08c0fdb60b2aa511a5e634d526f79f0eb1d5`
- **Current migration parent:** `20260916_clinical_access_sessions`; current main migration tests still identify that revision as the single head. Task-1 migration is `20260916_patient_external_record_import` and remains linear from it.
- **Current phase:** Phase A — finish/qualify strict patient-self upload/list/detail/source API before patient-authority extraction orchestration.
- **Last completed step:** audited the recovered `fe57b9985e893f55618a63f2f78f2a5c38669faf` prepared tree; corrected semantic idempotency, stable storage/DB/audit failures, patient JSON no-store headers, and obvious truncated-file rejection before advancing the real branch.
- **Exact next step:** commit this corrected API logical unit; update PR #47 body; inspect exact-head CI; fix any focused/Partition-A failures; then implement patient-authority extraction orchestration without provider/hospital/treatment-consent authority.
- **Tests actually PASS:** none yet on this logical step. Do not reuse Vercel/backend results from older SHAs.
- **Tests actually FAIL:** none yet; no current-head test execution has completed.
- **Tests NOT RUN:** `tests/test_patient_external_record_import_contract.py`; `tests/test_patient_external_document_storage.py`; `tests/test_patient_external_record_api_contract.py`; patient auth/self-route regressions; provider storage/pipeline regressions; PostgreSQL; Redis; migration graph; privacy/adversarial integration; frontend/mobile/native/E2E.
- **Current blockers:** local sandbox has no network checkout, so pytest cannot be run locally from this agent. GitHub PR CI is expected to provide real qualification after the branch advances. Malware scanning is **NOT CURRENTLY VERIFIED / NOT IMPLEMENTED**. Full decoder-level PDF/image corruption validation is not yet verified; this API only rejects MIME/signature mismatch and obvious truncation envelopes.
- **Concurrent PRs/workstreams:** only PR #47 is currently open. Slice 10B is already moving on `main` and remains protected. During inspection an accidental temporary branch `tmp-inspect-fe57-patient-import` was created pointing at the unchanged Task-1 head; no changes were made on it. The available GitHub connector exposes no branch-delete action, so it must be removed at the next environment with ref deletion access. Do not use it for work.
- **Files protected from edits:** `app/services/clinical_access_session.py`, `app/services/clinical_access_session_store.py`, `app/models/clinical_access_session.py`, `app/security/clinical_access_policy.py`, `app/services/approved_access_capability.py`, `app/api/v2/consent_v3_routes.py`, `app/core/consent_gate.py`, `alembic/versions/20260916_clinical_access_sessions.py`, and Slice-10B qualification/governance files.

## Current Scope

Authenticated patient self-import of external medical records from onboarding and Records: category selection, encrypted source retention, patient-authority extraction, patient-friendly review/correction, typed-record finalization, timeline, safe source view, retries, audit/privacy/lifecycle, and qualification.

## Explicit Non-Scope

- Broaden or redesign ClinicalAccessSession/treatment consent.
- Grant provider authority from a patient import.
- Represent patients as fake providers, hospitals, or clinical tenants.
- Direct OCR/LLM output into canonical truth or enable auto-commit.
- Create a generic final clinical JSON blob.
- Build parallel erasure architecture.
- Create a competing Alembic head or merge migration merely because main moves.

## Repository Baseline

- **origin/main at branch creation:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- **Current observed origin/main:** `be5a08c0fdb60b2aa511a5e634d526f79f0eb1d5`.
- **Branch:** `slice-11a-patient-external-record-import`.
- **Draft PR:** #47, open and unmerged.
- **Current main migration head:** `20260916_clinical_access_sessions`.
- **Patient import migration:** `20260916_patient_external_record_import`, linear from that head.
- **Current PR inventory:** only #47 open.
- **Exact-head qualification evidence so far:** historical Vercel success exists for `cd8af0...`; it is stale and not evidence for later heads.

## Product Contract

New patient: account → required onboarding → Add previous medical records → category → camera/photo/PDF where supported → secure upload/processing → patient-friendly review → correct/confirm → retain source → provenance-aware typed save → record category + timeline → continue or Skip for now.

Existing patient: Records → Add external record → category → upload → processing → review → save → categorized record + timeline.

Patient-facing categories: Prescription; Lab report; Imaging / radiology report; Discharge summary; Other medical record. Internal lanes, candidate IDs, provider-job metadata, storage refs, or engineering terminology must never be shown.

## Security Invariants

1. Patient ownership derives exclusively from strict server-side patient JWT/session authority.
2. Patient self-import is separate from treatment consent, provider capability/session, ClinicalAccessSession, clinician verification, and AI confidence.
3. No client patient UUID, provider token, fake provider, fake hospital, or fake tenant may authorize self-import.
4. Patient source storage is authenticated-encrypted in a distinct patient-self namespace/AAD.
5. Extracted/reviewed medical values remain encrypted and source/evidence linked.
6. OCR/AI cannot silently become authoritative clinical truth; auto-commit remains disabled.
7. Patient corrections remain patient-reviewed external-document provenance, never clinician-verified absent a real clinician action.
8. No source bytes, extracted PII, object keys, storage refs, or unnecessary sensitive identifiers in logs/URLs/analytics/UI.
9. Security-sensitive writes use durable transactional audit/outbox where repository governance requires it.
10. Duplicate/replay upload/finalization are idempotent or fail safely; same-patient same idempotency key with changed category/content must conflict.
11. Lifecycle reuses canonical merge/erasure/delete behavior.
12. Provider document processing must not regress.

## Existing Components Reused

| Component | Classification | Evidence / decision |
|---|---|---|
| Strict patient auth | **EXISTS** | `get_current_patient` validates patient session + DB identity and binds trusted audit scope. |
| Patient-self namespace | **EXISTS** | `/api/v2/patient/me`; child route accepts no patient identifier. |
| Patient external-record API | **IMPLEMENTING / prepared for commit** | Upload/list/detail/source under `/api/v2/patient/me/external-records`. |
| Provider upload/extraction | **WRONG_FLOW** for patient / **EXISTS** provider | Provider consent/capability/hospital trust remains untouched. |
| Encrypted source storage | **EXISTS / EXTENDED** | Patient-self namespace + AAD separate from provider namespace. |
| Patient import persistence | **EXISTS, unqualified** | Patient-owned import/candidate tables have no provider/hospital/consent authority columns. |
| Extraction adapters/evidence primitives | **EXISTS** | Reuse next under patient authority. |
| Provider extraction orchestrator | **WRONG_FLOW** for patient | Delegated trust recheck prevents direct reuse. |
| Typed records/timeline | **EXISTS/PARTIAL** | Finalizers pending semantic audit. |
| Audit outbox | **EXISTS** | Upload/source-view service stages durable patient-scoped events. |
| Patient UX / API client | **MISSING/PARTIAL** | Deliberately deferred until backend vertical slice is sound. |
| Malware scanning | **NOT VERIFIED / NOT IMPLEMENTED** | Do not claim it exists. |

## Architecture Decisions

### 1. Patient authority/persistence stays distinct
Patient import rows bind directly to patient + retained source document. Provider authorization graph is not weakened or repurposed.

### 2. No generic canonical JSON store
Import rows hold workflow/provenance metadata and encrypted evidence; completion requires safe typed-record + timeline references.

### 3. Patient corrections require explicit review provenance
A corrected candidate requires patient-review flag, encrypted reviewed value, original extraction retention, and review timestamp.

### 4. Linear migration only
`20260916_patient_external_record_import` revises `20260916_clinical_access_sessions`. Main still reports the latter as its single head as of `be5a08c0...`.

### 5. Distinct patient-self source namespace
Storage uses `patient-self/<patient>/...` with `nexa-patient-document-v1` AAD; DB tenant remains NULL for patient-self sources.

### 6. Staging stops before provider extraction
Patient upload persists/audits source and import state but does not invoke provider delegated-trust orchestration.

### 7. Idempotency is patient-scoped and semantic
`(patient_id, request_id)` is unique. Different patients may reuse a key string. Same patient/key is accepted only when category + content digest match; changed semantics conflict.

### 8. Patient API exposes stable patient language only
Internal statuses map to `processing`, `needs_review`, `imported`, `retry_available`, `could_not_process`, `cancelled`; unknown internal state fails to patient-safe `could_not_process` presentation rather than exposing internals.

### 9. Source viewing is ownership-bound and non-cacheable
Authenticated patient → import ownership → source ownership → patient namespace decrypt. Patient responses carry `Cache-Control: private, no-store`; source API never returns storage implementation metadata.

## Work Log

### Entry 1 — Baseline/isolation
- **Timestamp:** 2026-09-16
- **Starting SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Ending SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Behavior:** created isolated branch/handoff.
- **Tests:** none; documentation step.

### Entry 2 — Architecture audit + draft PR
- **Timestamp:** 2026-09-16
- **Starting SHA:** `aa218f...`
- **Ending SHA:** `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`
- **Behavior:** classified architecture, opened draft PR #47, identified provider-delegated extraction constraint.
- **Tests:** none; audit-only.

### Entry 3 — Patient-owned persistence
- **Timestamp:** 2026-09-16
- **Starting SHA:** `c7bc7998...`
- **Ending SHA:** `cd8af0bce6e774a978ba2d9e728bb569ac11f694`
- **Files:** model, registry, linear migration, contract tests, handoff.
- **Behavior:** patient-owned import/candidate state, encrypted values, correction provenance, typed/timeline completion refs.
- **Tests:** backend NOT RUN; Vercel success only on this exact historical head.

### Entry 4 — Encrypted patient source staging
- **Timestamp:** 2026-09-16
- **Starting SHA:** `cd8af0...`
- **Ending SHA:** `21b7d4822688774f1d11d2ae0a77e0403b8b7584`
- **Files:** storage service, patient import service, storage tests, handoff.
- **Behavior:** separate patient-self encrypted namespace, upload type/signature validation, duplicate detection, audit staging, ownership-bound source read service.
- **Tests:** NOT RUN.

### Entry 5 — Strict patient external-record self-service API
- **Timestamp:** 2026-09-16
- **Starting SHA:** `21b7d4822688774f1d11d2ae0a77e0403b8b7584`
- **Ending SHA:** resolve exact branch HEAD after this atomic commit; do not substitute the raw prepared tree SHA.
- **Prepared-tree provenance:** prior agent supplied tree `fe57b9985e893f55618a63f2f78f2a5c38669faf`. It was inspected through dangling commit `995fc7c26677e87e456113ed77f25cfadd7a2bb7` and **not** advanced unchanged because semantic idempotency was incorrect.
- **Files changed:** `alembic/versions/20260916_patient_external_record_import.py`; `app/api/v2/patient_external_record_routes.py`; `app/api/v2/patient_routes.py`; `app/models/patient_external_record_import.py`; `app/services/patient_external_record_import.py`; `tests/test_patient_external_record_api_contract.py`; this handoff.
- **Behavior:** strict patient upload/list/detail/source API; patient-scoped semantic idempotency; patient-safe statuses; safe no-store responses; stable storage/DB/audit errors; obvious truncated PDF/PNG/JPEG envelope rejection.
- **Security impact:** patient dependency is sole authority; no provider treatment authority; no storage refs/internal lanes exposed; source view audited before successful return path completes.
- **Tests run before commit:** none — local repository checkout unavailable because sandbox cannot resolve GitHub. Focused tests are added but **NOT RUN**.
- **Known gaps:** malware scanning not verified/implemented; deep decoder-level corruption validation not proven; extraction/review/finalization intentionally pending.
- **Concurrent overlap check:** only PR #47 open; current main `be5a08c0...`; migration head remains `20260916_clinical_access_sessions`.
- **Operational note:** accidental temporary branch `tmp-inspect-fe57-patient-import` points to the unchanged starting SHA; connector cannot delete refs. Remove it when deletion access exists.
- **Next action:** advance real branch to corrected commit, update PR body, inspect CI, fix failures, then implement patient-authority extraction orchestration.

## Test / Qualification Matrix

| Area | State | Notes |
|---|---|---|
| Patient model contract | NOT RUN | Added. |
| Patient-self storage | NOT RUN | Added. |
| Patient API contract | NOT RUN | Expanded in Entry 5. |
| Backend API integration | NOT RUN | Needs real patient JWT/session + DB. |
| Patient auth regression | NOT RUN | Required. |
| Provider storage/pipeline regression | NOT RUN | Mandatory because shared storage service was extended. |
| Migration single-head/governance | NOT RUN | Main head inspected statically; runtime Alembic qualification pending. |
| PostgreSQL | NOT RUN | Required. |
| Redis | NOT RUN | Required where patient session authority exercises Redis. |
| Privacy/security/adversarial | NOT RUN | More coverage pending. |
| Web/mobile/Next/Android/iOS/E2E | NOT RUN | UI deliberately not implemented yet. |
| Vercel | STALE | Historical PASS only for `cd8af0...`, invalid for later heads. |

## Open Risks / Blockers

1. Patient extraction orchestration is missing; provider orchestrator cannot be reused unchanged.
2. Real tests must come from PR CI or another checkout-capable environment; this sandbox has no GitHub network resolution.
3. Deep malformed-file validation beyond envelope/truncation checks is not qualified.
4. Imaging/discharge/prescription typed finalization remains to audit; Prescription must not be faked as Medication.
5. Malware/content scanning remains unverified/not implemented.
6. Accidental temp inspection branch must be deleted when a ref-delete-capable environment is available.
7. `patient_routes.py` registration change must remain behavior-preserving and be covered by existing provider/patient route regressions.

## Merge / Rebase Safety Notes

- Original base `2a103847...`; current observed main `be5a08c0...`.
- Do not rebase merely because branch is behind. Current Task-1 migration ancestry remains semantically valid because main still ends at `20260916_clinical_access_sessions`.
- Recheck main/PRs/migration graph before every new schema change and at integration checkpoint.
- Protected Slice 10B files remain untouched.
- Resolve eventual conflicts semantically, never blind ours/theirs.
- Final qualification must correspond to one frozen exact head SHA.

## Final Completion Checklist

- [ ] Onboarding + Records UI entry points.
- [ ] Five categories + Skip for now.
- [x] Patient-owned persistence implemented (unqualified).
- [x] Distinct encrypted patient-self storage implemented (unqualified).
- [x] Strict patient-owned upload/list/status/source API prepared for commit (unqualified).
- [ ] Focused API/storage/model tests green on exact head.
- [ ] PostgreSQL/Redis/migration/security qualification.
- [ ] Deep corruption/malware posture explicitly resolved.
- [ ] Patient-authority extraction/evidence orchestration.
- [ ] AI/OCR auto-commit remains impossible.
- [ ] Patient-friendly review/correction.
- [ ] Typed record + timeline finalization where semantics are valid.
- [ ] Retry/resume/cancel lifecycle.
- [ ] Lifecycle merge/erasure/delete qualification.
- [ ] No provider authority/treatment-consent mutation.
- [ ] Provider regressions pass.
- [ ] Frontend/mobile/build/native/E2E gates green as applicable.
- [ ] Final exact SHA recorded externally in PR/handoff follow-up and draft PR #47 integration-ready.
