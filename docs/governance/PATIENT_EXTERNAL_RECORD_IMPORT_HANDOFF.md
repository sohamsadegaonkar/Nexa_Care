# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **Current HEAD at start of current logical commit:** `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b` (the commit containing this file must be resolved from the branch ref after write; a commit cannot embed its own future SHA).
- **Base main SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Current implementation phase:** patient-owned import persistence contract; API/service extraction path is next.
- **Last completed step:** designed patient-owned import/job evidence tables, linear migration, ORM registration, and authority/provenance contract tests without provider/hospital/consent fields.
- **Exact next step:** implement patient-self source storage/upload service and `/api/v2/patient/me/external-records` API using server-derived patient identity, then run focused tests and existing provider document regressions.
- **Blockers:** no repository/concurrency blocker. Existing provider async extraction remains provider-delegation-bound and must not be reused by faking provider/hospital authority; patient extraction orchestration needs its own authority-safe service using shared storage/extractor/evidence primitives.
- **Exact tests to run next:** `tests/test_patient_external_record_import_contract.py`, existing patient-auth/self-route tests, migration governance/head tests, then focused provider-pipeline regressions after shared storage changes.
- **Files that must not currently be touched:** `app/services/clinical_access_session.py`, `app/security/clinical_access_policy.py`, `app/services/approved_access_capability.py`, `app/api/v2/consent_v3_routes.py`, `app/core/consent_gate.py`, `app/models/clinical_access_session.py`, clinical-access-session qualification tests, and Slice 10B policy/session files unless an unavoidable dependency is proven.

## Current Scope

Authenticated patient self-import of previous/out-of-network medical records from onboarding and Records: category selection, secure source retention, extraction, patient-safe review/correction, provenance-aware finalization into existing typed records, timeline integration, source access where policy allows, retries, audit/privacy/lifecycle behavior, and qualification.

## Explicit Non-Scope

- Redesigning/broadening `ClinicalAccessSession` or provider treatment consent.
- Making a patient import grant provider authority.
- Treating a patient as a synthetic provider, hospital, or clinical tenant.
- Replacing evidence-based processing with direct OCR/LLM-to-record writes.
- Enabling extraction auto-commit.
- Creating a generic JSON clinical-record blob when typed record models exist.
- Redesigning global erasure architecture.
- Creating a sibling/competing Alembic head.

## Repository Baseline

- **origin/main SHA at branch creation:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Branch:** `slice-11a-patient-external-record-import`
- **Execution mode:** authenticated GitHub connector; no local repository checkout is mounted.
- **Branches at creation:** only `main`.
- **Open PRs at creation:** none.
- **Draft PR:** #47, `feat(patient): integrate external medical record import workflow`.
- **Migration head at baseline:** `20260916_clinical_access_sessions`.
- **Pre-migration concurrency recheck:** main still `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`; only `main` plus this branch exist; the only open PR is #47. Therefore the patient-import migration is linear from `20260916_clinical_access_sessions` and does not create a sibling head.

## Product Contract

### New patient / onboarding

Create account → required onboarding → **Add previous medical records** → category → camera/photo/PDF where supported → upload → secure processing → patient-friendly review → correct/confirm → retain original source → provenance-aware save → categorized record → timeline → continue onboarding or **Skip for now**.

### Existing patient

Home / Records → **Add external record** → category → upload → processing → review → save → categorized record → timeline.

Patient-facing categories:

- Prescription
- Lab report
- Imaging / radiology report
- Discharge summary
- Other medical record

Never expose internal terms such as `SOURCE_ONLY`, `QUARANTINE`, workflow IDs, adjudication candidates, or extraction-provider jobs.

## Security Invariants

1. Patient ownership is derived from `get_current_patient` / `get_current_patient_session`; client patient IDs are never authority.
2. Patient self-import is distinct from treatment consent, provider capability/session, clinician verification, and AI confidence.
3. Patient import cannot create provider access or mutate treatment consent.
4. Provider tokens/capabilities cannot authorize patient self-import.
5. Do not invent provider/hospital/tenant bindings for patient imports.
6. Source documents remain encrypted and patient-bound.
7. Extracted/reviewed medical values are encrypted at rest and retain source/evidence provenance.
8. OCR/AI output cannot silently become authoritative clinical truth; auto-commit remains disabled.
9. Patient corrections remain explicitly patient-reviewed external-document provenance.
10. No document bytes, extracted PII, object-storage keys, or sensitive identifiers in logs/URLs/analytics/patient UI.
11. Security-sensitive writes preserve fail-closed audit behavior.
12. Duplicate/replay upload/finalization is idempotent or safely rejected.
13. Merge/erasure/delete behavior must reuse canonical lifecycle hooks.
14. Existing provider document processing must not regress.

## Existing Components Reused

| Component | Classification | Evidence / decision |
|---|---|---|
| Strict patient-self authentication | **EXISTS** | `app/core/dependencies.py`: live Redis + PostgreSQL patient/session/identity authority. |
| Patient-self API namespace | **EXISTS** | `app/api/v2/patient_self_routes.py` under `/api/v2/patient/me`, no patient-ID override. |
| Patient-authenticated external upload | **MISSING** | To implement next. |
| Provider document upload | **WRONG_FLOW** for self-import / **EXISTS** for provider | Consent/capability + hospital/provider bound. Preserve unchanged. |
| Legacy unbound upload | **CORRECTLY RETIRED** | Returns 410; do not revive. |
| Encrypted source storage | **EXISTS/PARTIAL** | Strong AES-GCM/KMS storage exists, but current storage namespace requires tenant string; add truthful patient-self namespace instead of fake hospital. |
| Patient import persistence | **IMPLEMENTING** | New patient-owned import/candidate tables contain no provider/hospital/consent authority. |
| Existing extraction jobs/candidates | **WRONG_FLOW** for patient | Provider-delegated trust and non-null provider/hospital candidate bindings. |
| Extraction adapters | **EXISTS** | `app/ai/extractor.py` has validated configured adapters and attempt provenance. |
| Field evidence/source traceability | **EXISTS** | Existing evidence/routing/source primitives can be reused where authority-neutral. |
| AI auto-commit safety | **EXISTS** | Existing provider pipeline requires adjudication; do not weaken. |
| Human adjudication/review | **WRONG_FLOW** for patient UX | Internal provider/steward semantics need patient-facing adapter. |
| Typed clinical records | **EXISTS/PARTIAL** | Medication/Lab/etc. and `source_document_id` exist; imaging/discharge final mapping still needs exact design. |
| Timeline | **EXISTS** | Existing timeline model/write patterns available. |
| Audit/outbox | **EXISTS** | Existing clinical writes fail closed if durable audit cannot be staged. |
| Erasure/merge lifecycle | **EXISTS/PARTIAL** | Canonical patient erasure/session checks exist; new rows need integration/qualification. |
| API client abstraction | **EXISTS** | `nexa-client` centralizes API calls; existing pipeline method is provider-flow specific. |
| Patient import UI | **MISSING/PARTIAL** | To implement after backend contract. |
| Malware scanning | **NOT VERIFIED** | Do not claim scanner exists. |

## Architecture Decisions

### 1. Patient authority remains patient authority

- **Decision:** new self-import endpoints derive patient ownership exclusively from strict patient auth.
- **Reason:** self-import is a patient ownership action, not a provider treatment session.
- **Rejected:** client patient UUID, provider consent token, ClinicalAccessSession.
- **Security consequence:** prevents cross-patient selection and accidental provider authority.

### 2. Patient imports use distinct persistence from provider-delegated candidates

- **Decision:** add `patient_external_record_imports` and `patient_external_record_candidates` bound directly to patient + retained source.
- **Reason:** existing extraction candidate graph requires provider/hospital delegated authority and would misstate provenance for self-import.
- **Rejected:** fake provider, fake hospital, nullable weakening of provider candidate semantics.
- **Affected files:** `app/models/patient_external_record_import.py`, model registry, linear migration.
- **Security consequence:** provider pipeline authorization semantics remain intact while patient imports have truthful ownership.

### 3. No generic clinical JSON store

- **Decision:** import rows store workflow/provenance metadata and encrypted candidate evidence only. Completed imports must reference an existing typed record and a timeline event.
- **Reason:** canonical clinical facts belong in typed record models.
- **Rejected:** arbitrary final JSON medical-record blob.
- **Security consequence:** avoids a second ungoverned clinical truth store.

### 4. Patient corrections require explicit provenance

- **Decision:** a `CORRECTED` candidate requires `patient_reviewed`, encrypted reviewed value, and review timestamp.
- **Reason:** edited extraction cannot be relabeled as clinician verification.
- **Security consequence:** correction origin remains auditable and truthful.

### 5. Migration is linear from the current single head

- **Decision:** `20260916_patient_external_record_import` revises `20260916_clinical_access_sessions` after a fresh branch/PR/main check.
- **Reason:** patient-specific authority cannot safely fit provider-bound candidate schema without semantic weakening.
- **Security consequence:** no sibling migration head.

## Work Log

### Entry 1 — Baseline and isolation

- **Timestamp:** 2026-09-16
- **Starting SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Ending SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Files changed:** handoff only.
- **Behavior:** created isolated branch and living handoff.
- **Security impact:** process isolation only.
- **Tests:** none; documentation step.
- **Concurrent overlap:** none visible.
- **Next:** architecture audit.

### Entry 2 — Architecture/security audit and draft PR

- **Timestamp:** 2026-09-16
- **Starting SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Ending SHA:** `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`
- **Files changed:** handoff only.
- **Behavior:** classified patient auth, provider pipeline, encrypted storage, candidate graph, typed records/timeline and client boundary; opened draft PR #47.
- **Security impact:** explicitly prohibited fake provider/hospital reuse of delegated processing.
- **Tests:** none; audit-only.
- **Known issue:** provider orchestrator is delegated-authority-bound.
- **Concurrent overlap:** main unchanged; no external branch/PR.
- **Next:** patient-owned domain contract.

### Entry 3 — Patient-owned persistence contract

- **Timestamp:** 2026-09-16
- **Starting SHA:** `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`
- **Ending SHA:** resolve branch HEAD after this atomic commit.
- **Files changed:** `app/models/patient_external_record_import.py`; `app/models/__init__.py`; `alembic/versions/20260916_patient_external_record_import.py`; `tests/test_patient_external_record_import_contract.py`; this handoff.
- **Behavior:** adds patient-owned import orchestration/evidence schema with required categories/states, encrypted candidate fields, patient-correction provenance constraints, completion references, and patient-self duplicate source hash protection.
- **Security impact:** no provider/hospital/consent authority can be stored in the new import/candidate tables; completion requires typed record + timeline references; patient correction provenance is constrained.
- **Tests run:** NOT RUN in this execution environment before commit; contract tests are added and CI/workflow evidence must be checked against the resulting exact SHA.
- **Results:** code review/static contract prepared; no PASS claimed.
- **Known failures:** none observed yet; runtime/migration tests pending.
- **Concurrent overlap check:** main unchanged at original base; only this branch exists besides main; only PR #47 open immediately before migration creation.
- **Next action:** run/check exact-head tests; implement patient-self encrypted source storage/upload API if migration/model checks are green.

## Test / Qualification Matrix

| Area | State | Notes |
|---|---|---|
| Patient import model contract | NOT RUN | Test file added in Entry 3. |
| Unit | NOT RUN | Await exact-head workflow/local runner. |
| Backend API | NOT RUN | Patient upload API not implemented yet. |
| Patient auth regression | NOT RUN | Required after API delta. |
| Provider document regression | NOT RUN | Mandatory after shared storage changes. |
| Migration single-head/governance | NOT RUN | New linear migration added; must execute. |
| PostgreSQL | NOT RUN | Must exercise migration + constraints. |
| Redis | NOT RUN | Required for real patient-session API tests. |
| Privacy/security | NOT RUN | Add authority/log/provenance/replay coverage. |
| Mobile tests | NOT RUN | UI not implemented. |
| Web tests | NOT RUN | UI not implemented. |
| Next production build | NOT RUN | UI not implemented. |
| Android compile | NOT RUN | UI not implemented. |
| iOS compile | NOT RUN | UI not implemented. |
| E2E | NOT RUN | End-to-end path incomplete. |
| Vercel | NOT RUN | Required if Vercel-deployed frontend changes. |

## Open Risks / Blockers

1. Existing provider async extraction remains delegated provider/hospital trust-bound. Patient orchestration must invoke shared extraction/evidence primitives under patient authority without bypassing provider checks.
2. Patient-self document storage needs a non-hospital encrypted namespace; existing storage method requires tenant ID. Do not pass a synthetic hospital UUID/string.
3. Imaging/discharge final typed-record mapping remains to be selected from current canonical models.
4. Patient-safe original-source retrieval is not implemented yet.
5. Malware/content scanner is not verified; document absence rather than fabricating a control.
6. New migration must be executed against PostgreSQL and single-head governance before being considered qualified.

## Merge / Rebase Safety Notes

- Original base `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Draft PR #47 exposes overlap.
- Recheck branches/PRs/main before every major phase and before any further migration changes.
- Do not touch protected Slice 10B files casually.
- Rebase only at safe integration point; resolve conflicts semantically, never blind ours/theirs.
- Final qualification must correspond to final exact head SHA.

## Final Completion Checklist

- [ ] Patient can enter import from onboarding and Records.
- [ ] Five required patient-facing categories exist.
- [ ] Onboarding has Skip for now and upload is optional.
- [ ] Patient ownership is server-derived; no client patient UUID authority.
- [ ] Upload validates type/signature/size/empty/corrupt cases and is idempotent.
- [ ] Source is encrypted in a truthful patient-self namespace.
- [ ] Existing extraction/evidence primitives are reused without provider-authority fabrication.
- [ ] AI/OCR cannot auto-commit clinical truth.
- [ ] Patient-friendly structured review/correction exists.
- [ ] Corrections retain patient-review provenance.
- [ ] Supported results persist into existing typed record models.
- [ ] Timeline entry retains external-import provenance.
- [ ] Original source can be viewed safely where policy permits.
- [ ] Retry/recovery/resume paths exist.
- [ ] Patient import never creates provider authority or changes treatment consent.
- [ ] Provider document-processing regressions pass.
- [ ] Authority/privacy/provenance/adversarial tests pass.
- [ ] Merge/erasure/delete lifecycle qualified.
- [ ] Exactly one Alembic head and migration tests pass.
- [ ] Frontend/mobile/build/native/E2E gates green where applicable.
- [ ] Exact final SHA recorded.
- [ ] Draft PR #47 updated and ready for review only after qualification.
