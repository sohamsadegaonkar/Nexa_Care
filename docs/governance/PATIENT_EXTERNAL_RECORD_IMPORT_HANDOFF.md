# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **Current HEAD before this documentation commit:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Base main SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Current implementation phase:** Step 1 audit complete enough to lock the first architecture boundary; runtime implementation has not started.
- **Last completed step:** Audited patient-self authentication, provider pipeline authority, encrypted document storage, extraction job/candidate persistence, typed records/timeline, client API wrapper, and the asynchronous delegated-trust recheck; opened draft PR #47.
- **Exact next step:** Implement the smallest patient-owned import boundary without faking provider/hospital authority. First prove whether a patient-import authority strategy can be introduced in non-protected pipeline code and persistence while preserving the provider path unchanged; if that requires protected Slice 10B files, mark that subcomponent BLOCKED and continue UI/API contract work that does not depend on it.
- **Blockers:** The existing asynchronous pipeline is provider-delegation-bound: it rechecks delegated provider trust and extraction candidates require `tenant_id` plus `authorization_provider_id`. A patient must not be represented as a fake provider or hospital. This is an architectural constraint, not yet a total workstream blocker.
- **Exact tests to run next:** focused new patient-import authority tests; existing patient auth tests; document pipeline/provider regression tests; extraction evidence/candidate binding tests. No test is marked PASS until actually executed.
- **Files that must not currently be touched:** `app/services/clinical_access_session.py`, `app/security/clinical_access_policy.py`, `app/services/approved_access_capability.py`, `app/api/v2/consent_v3_routes.py`, `app/core/consent_gate.py`, `app/models/clinical_access_session.py`, clinical-access-session migrations/tests, and migration-head governance files unless an unavoidable dependency is proven.

## Current Scope

Authenticated patient self-import of previous/out-of-network medical records from onboarding and Records, including safe upload, processing/review states, patient correction, provenance-aware finalization, typed record/timeline integration, retained source access where policy allows, privacy/audit/lifecycle behavior, and qualification.

## Explicit Non-Scope

- Redesigning or broadening `ClinicalAccessSession`.
- Reinterpreting provider treatment consent.
- Making patient import grant provider authority.
- Treating a patient as a synthetic provider or hospital/tenant to satisfy provider-only schema.
- Replacing the safe extraction/evidence pipeline with `upload → LLM/OCR → JSON → canonical record`.
- Enabling AI/OCR auto-commit.
- Redesigning global erasure architecture.
- Creating a competing Alembic head.

## Repository Baseline

- **origin/main SHA at branch creation:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Branch:** `slice-11a-patient-external-record-import`
- **Execution mode:** authenticated GitHub connector; no repository checkout is mounted in the sandbox.
- **Remote branches at branch creation:** only `main`.
- **Open PRs at branch creation:** none.
- **Current visible branches at latest overlap check:** `main` and this workstream branch only.
- **Draft PR:** #47, `feat(patient): integrate external medical record import workflow`.
- **Migration head at baseline:** `20260916_clinical_access_sessions`.
- **Latest overlap check:** `main` remains at the branch base SHA; no new concurrent remote work is visible.

## Product Contract

### New patient / onboarding

Create account → required onboarding → **Add previous medical records** → choose category → camera/photo/PDF where supported → upload → secure processing → patient-friendly review → correct/confirm → retain original source → provenance-aware save → categorized record → longitudinal timeline → continue onboarding or **Skip for now**.

### Existing patient

Home / Records → **Add external record** → category → source upload → processing → review → save → categorized record → timeline.

Initial patient-facing categories:

- Prescription
- Lab report
- Imaging / radiology report
- Discharge summary
- Other medical record

No internal terms such as `SOURCE_ONLY`, `QUARANTINE`, workflow IDs, adjudication candidates, or extraction-provider jobs may appear in patient UI.

## Security Invariants

1. Patient ownership comes from strict server-side patient JWT/session authority; a client-supplied patient UUID is never authorization.
2. Patient self-import authority is distinct from treatment consent, provider capability, provider clinical session, clinician verification, and AI confidence.
3. Patient import cannot create provider access or alter treatment consent.
4. Provider consent/capability tokens cannot be reused as patient-upload authority.
5. Do not satisfy provider-only bindings by inventing a provider identity or hospital tenant for the patient.
6. Source documents remain encrypted and patient-bound using existing storage primitives.
7. Extraction evidence/candidates remain encrypted/provenance-aware; OCR/AI output never silently becomes canonical clinical truth.
8. `AUTO_COMMIT_ENABLED = False` and `AUTO_COMMIT_APPROVED = False` remain unchanged absent separate governance authorization.
9. Patient-edited values remain external-document + patient-reviewed provenance, not clinician-created or clinician-verified.
10. No document bytes, extracted PII, raw object keys, or sensitive identifiers in logs, URLs, analytics, or patient UI.
11. Security-sensitive transitions remain auditable with fail-closed behavior consistent with existing governance.
12. Duplicate upload/finalization is idempotent or safely rejected.
13. Merge/erasure/delete behavior must use existing canonical lifecycle hooks.
14. Existing provider document processing must not regress.

## Existing Components Reused

| Component | Classification | Evidence / decision |
|---|---|---|
| Strict patient-self authentication | **EXISTS** | `app/core/dependencies.py` exposes `AuthenticatedPatient`, `AuthenticatedPatientSession`, `get_current_patient`, and `get_current_patient_session`. JWT claims are resolved through live server-side session authority and DB identity state. |
| Patient-self API namespace | **EXISTS** | `app/api/v2/patient_self_routes.py` uses `/api/v2/patient/me`, strict patient JWT/session dependencies, and explicitly rejects body/path/query patient-ID overrides. |
| Patient-authenticated external-document upload API | **MISSING** | No audited patient-owned upload route exists. Current pipeline upload accepts form `patient_id` and uses clinical consent/capability authority. |
| Provider-authorized document upload | **WRONG_FLOW** for patient self-import; **EXISTS** for provider path | `app/api/v2/pipeline_routes.py` is the mature provider/clinical document path and must remain intact. |
| Legacy unbound upload | **CORRECTLY RETIRED** | `app/api/v2/document_routes.py` returns 410 and directs callers to the patient-bound staged pipeline. Do not revive it. |
| Encrypted source document storage | **EXISTS** | `app/services/document_storage.py` provides local AES-GCM and S3 client-side AES-GCM + KMS storage, patient/tenant binding, authenticated retrieval, digest, and delete. |
| Document metadata/job persistence | **PARTIAL** | `DocumentStorage` and `ExtractionJob` already hold patient, source, type, digest/status/timestamps. They are reusable, but current authorization fields are provider-oriented. |
| Extraction orchestration | **WRONG_FLOW** for patient self-import; **EXISTS** for provider path | `app/services/pipeline_orchestrator.py` rechecks delegated provider trust during async processing. Patient self-import needs a distinct authority strategy; bypassing the recheck is not acceptable. |
| Encrypted extraction candidates | **PARTIAL** | `ExtractionCandidateRecord` encrypts raw/source text and preserves evidence, but schema requires non-null `tenant_id` and `authorization_provider_id` and its FK/index semantics are provider authorization-bound. |
| Field-level evidence / source traceability | **EXISTS** | Pipeline imports evidence integrity and source relationship primitives and candidates retain `evidence_id`, source document, page/bbox, confidence, provider/version and reason codes. |
| AI auto-commit safety | **EXISTS** | Pipeline response contract reports `auto_commit_enabled: false` and clinician adjudication required; retired direct extraction paths prevent unbound direct writes. |
| Human adjudication/review | **WRONG_FLOW** for patient UX; **EXISTS** internally | Current review/adjudication vocabulary and APIs are steward/provider/engineering oriented. Patient UI requires a safe adapter, not raw candidate lanes. |
| Typed clinical record models | **EXISTS/PARTIAL** | `patient_record_routes.py` uses typed `Medication`, `LabResult`, `DocumentReference`, `TimelineEvent`, etc. Existing append models retain `source_document_id`, source/confidence/risk metadata. Imaging/discharge mapping still needs exact audit before finalization. |
| Timeline persistence | **EXISTS** | `TimelineEvent` is part of the typed record layer and existing clinical writes stage audit + record/timeline atomically. Patient-import provenance wording still needs integration. |
| Fail-closed audit/outbox patterns | **EXISTS** | Patient record routes stage audit intent in the same clinical transaction and roll back on audit durability failure. Reuse this pattern. |
| Erasure signal handling | **EXISTS/PARTIAL** | Patient-self routes already map erased-patient and erasure-registry failures; pipeline also imports/checks erasure registry. Full imported-record lifecycle qualification remains pending. |
| Client API abstraction | **EXISTS** | `nexa-client/packages/app/utils/apiClient.ts` already centralizes pipeline calls; no ad-hoc fetch/axios should be added. Existing upload method is consent-token/provider-flow oriented and cannot be reused unchanged. |
| Patient onboarding/import UI | **MISSING/PARTIAL** | Patient onboarding status exists server-side; no audited patient-facing external-record import flow has been found. |
| Patient Records/timeline self-view | **PARTIAL** | Backend has patient self-view record support, but external-import category/review/source UX is not implemented. |
| Malware scanning | **NOT VERIFIED / DO NOT CLAIM** | No audited evidence yet of a malware scanner. Do not fabricate this control. |

## Audit Questions A–Q

- **A. Patient-authenticated upload API?** No safe self-import upload API found: **MISSING**.
- **B. Existing upload requires provider/consent authority?** Yes. It is clinical-capability/consent bound: **WRONG_FLOW** for patient self-import.
- **C. Safe patient-self import authority path?** Strict patient auth exists, but no document-import adapter yet: **PARTIAL**.
- **D. Does upload directly create authoritative truth?** The mature pipeline stages extraction/review; direct unbound extraction has been retired. Auto-commit remains disabled.
- **E. Source encrypted?** Yes, existing storage is authenticated encryption and patient-bound.
- **F. Extraction candidates encrypted?** Yes for candidate raw/source text.
- **G. AUTO_COMMIT disabled?** Yes in audited pipeline contract; do not change.
- **H. Human-review boundary?** Exists, but current adjudication flow is clinician/steward oriented, not patient UX.
- **I. Typed models for categories?** Medication/lab/document/timeline primitives exist; exact imaging/discharge category commit mapping remains to be finalized.
- **J. Timeline persistence supports source references?** Typed record layer and source-document IDs exist; imported provenance presentation remains to implement.
- **K. Existing frontend workflow?** Current pipeline client/API is clinical/provider oriented; patient import UX is missing.
- **L. After upload?** Existing flow creates an extraction job and queues asynchronous extraction.
- **M. Extraction failure?** Job model carries `error_code`, retryability, attempt count; quarantine/retry services exist. Patient-friendly mapping remains to implement.
- **N. Ambiguous/low confidence?** Existing evidence/candidate routing uses safe lanes/reason codes and does not auto-commit; patient UI must translate this into review language.
- **O. Can patient view source after processing?** Existing adjudication has source-read primitives; no audited patient-safe source route exists yet.
- **P. Duplicate/idempotency controls?** Existing document schema has patient/tenant/content-hash uniqueness and jobs have request IDs; patient-self semantics require focused tests.
- **Q. PII/log/URL leakage?** Safe logging/error patterns exist in audited components, but patient-import-specific static/adversarial tests are still required.

## Architecture Decisions

### 1. Patient authority lives under the strict patient-self boundary

- **Decision:** New patient-import endpoints must derive ownership from `get_current_patient` / `get_current_patient_session` and must not accept a patient UUID as authority.
- **Reason:** This is the repository's canonical self-service trust model.
- **Rejected:** Provider consent token, ClinicalAccessSession, client-supplied patient UUID.
- **Affected files:** Prefer a new patient-import route/service plus registration in existing non-protected router wiring.
- **Security consequence:** Prevents cross-patient selection and provider-authority creation.

### 2. Do not fake provider/hospital bindings

- **Decision:** A patient import will not populate `authorization_provider_id` or hospital tenant with synthetic values merely to satisfy the provider pipeline.
- **Reason:** Those fields are used in delegated-trust rechecks, candidate FKs, indexes, and provenance. Synthetic values would misstate authority.
- **Rejected:** `provider_id = patient_id`, fake hospital UUID, bypassing delegated-trust checks.
- **Affected files:** Likely pipeline authority abstraction and/or persistence contract; exact design still being proven.
- **Security consequence:** Preserves the distinction between self-import provenance and provider clinical authority.

### 3. Reuse encrypted storage and extraction/evidence primitives, not provider authorization semantics

- **Decision:** Reuse `document_storage`, extractor adapters, evidence encryption/integrity, routing, source relationships, audit/outbox and typed record/timeline primitives where they can be invoked under patient authority without weakening checks.
- **Reason:** These are mature safety controls.
- **Rejected:** New unencrypted storage, direct OCR-to-record writes, raw engine output in UI.
- **Security consequence:** Retains source confidentiality and human-review provenance.

### 4. No speculative migration

- **Decision:** No migration is created until the authority strategy proves whether the existing provider-bound candidate schema can safely support a distinct patient authority. If schema change is necessary, it must be linear from `20260916_clinical_access_sessions` after a fresh concurrency check.
- **Reason:** Avoid multiple heads and semantic hacks.
- **Security consequence:** Prevents accidental provider-binding corruption.

## Work Log

### Entry 1 — Baseline and isolation

- **Timestamp:** 2026-09-16
- **Starting SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Ending SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Files changed:** `docs/governance/PATIENT_EXTERNAL_RECORD_IMPORT_HANDOFF.md`
- **Behavior:** Created isolated branch and mandatory handoff.
- **Security impact:** Process isolation only.
- **Tests:** None; documentation step.
- **Concurrent overlap:** None visible; protected Slice 10B is already on main.
- **Next:** Audit architecture.

### Entry 2 — Architecture/security audit and draft PR

- **Timestamp:** 2026-09-16
- **Starting SHA:** `aa218f24930140c0fc34a6b719403178e95ac0eb`
- **Ending SHA:** this documentation commit; resolve exact SHA after write.
- **Files changed:** `docs/governance/PATIENT_EXTERNAL_RECORD_IMPORT_HANDOFF.md`
- **Behavior:** No runtime change. Classified patient auth, pipeline authority, storage, candidates, typed records/timeline and client boundary; opened draft PR #47.
- **Security impact:** Identified and prohibited the unsafe shortcut of representing a patient as a provider/hospital to reuse delegated provider processing.
- **Tests run:** None; audit-only step.
- **Results:** Audit evidence recorded above.
- **Known failures/blockers:** Existing async extraction path is provider-delegation-bound; patient authority strategy is not yet implemented.
- **Concurrent overlap:** Latest remote branch check shows only main + this branch; main remains at base SHA.
- **Next:** Prove/implement a distinct patient-import authority strategy without protected Slice 10B changes.

## Test / Qualification Matrix

| Area | State | Notes |
|---|---|---|
| Unit | NOT RUN | Runtime implementation not started. |
| Backend API | NOT RUN | Patient import routes not implemented. |
| Existing patient auth regression | NOT RUN | Required after first backend delta. |
| Existing provider document pipeline regression | NOT RUN | Mandatory after shared pipeline changes. |
| Extraction evidence/candidate security | NOT RUN | Mandatory if authority/persistence changes. |
| PostgreSQL | NOT RUN | Required for any persistence/schema work. |
| Redis | NOT RUN | Patient session authority uses Redis; applicable. |
| Privacy/security | NOT RUN | Add authority, logging, provenance, replay tests. |
| Mobile tests | NOT RUN | UI not implemented. |
| Web tests | NOT RUN | UI not implemented. |
| Next production build | NOT RUN | UI not implemented. |
| Android compile | NOT RUN | UI not implemented. |
| iOS compile | NOT RUN | UI not implemented. |
| E2E | NOT RUN | End-to-end path not implemented. |
| Vercel | NOT RUN | Apply if Vercel-deployed code changes. |

## Open Risks / Blockers

1. **Provider-bound async extraction contract:** `pipeline_orchestrator.py` rechecks delegated clinical trust; extraction candidate schema requires provider/hospital bindings. Patient self-import needs a truthful distinct authority strategy.
2. **Migration risk:** If authority-neutral candidate persistence requires schema change, create it only after a fresh main/PR/branch/migration-head check and linearly from the current single head.
3. **Category commit mapping:** Medication/lab are clear typed targets; imaging and discharge need exact existing-model mapping before implementation.
4. **Source viewing:** Existing source-read functionality is adjudication-oriented; patient-safe source retrieval must derive patient ownership and hide storage refs.
5. **Malware/content scanning:** Not verified; must be documented as absent if the repository has no real scanner.

## Merge / Rebase Safety Notes

- Original base: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Draft PR #47 makes overlap visible.
- Recheck main/branches/open PRs before each major implementation phase and before any migration.
- Do not modify protected Slice 10B files casually.
- Do not resolve future conflicts with blind ours/theirs selection.
- Final qualification must be tied to the final exact head SHA.

## Final Completion Checklist

- [ ] Patient can enter import from onboarding.
- [ ] Patient can enter import from Records.
- [ ] Five required categories available.
- [ ] Onboarding has Skip for now and upload is optional.
- [ ] Patient-owned upload derives identity server-side.
- [ ] MIME/extension/size/empty/corrupt/duplicate/storage/extraction failure handling qualified.
- [ ] Source remains encrypted and safely retrievable.
- [ ] Existing safe extraction/evidence primitives reused under truthful patient authority.
- [ ] AI/OCR cannot auto-commit clinical truth.
- [ ] Patient-friendly structured review/correction implemented.
- [ ] Patient edits retain external-document + patient-review provenance.
- [ ] Typed record integration implemented for supported categories.
- [ ] Timeline integration retains provenance.
- [ ] Safe source-view route implemented where policy permits.
- [ ] Retry/recovery/resume behavior implemented.
- [ ] No provider authority or treatment-consent mutation occurs.
- [ ] Provider document pipeline regressions pass.
- [ ] Authority/privacy/provenance/adversarial tests pass.
- [ ] Merge/erasure/delete lifecycle qualified.
- [ ] Exactly one Alembic head if migration introduced.
- [ ] Frontend/mobile/build/native/E2E gates green where applicable.
- [ ] Exact final SHA recorded.
- [ ] Draft PR #47 updated and ready for review only after all required qualification.
