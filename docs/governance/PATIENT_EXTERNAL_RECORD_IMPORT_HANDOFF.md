# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`.
- **PR:** #47, `feat(patient): integrate external medical record import workflow` — **OPEN / DRAFT / UNMERGED**.
- **Latest observed main:** `342d25cb960a3c81539502e6d3eacde4e7121aee`.
- **Semantic current-main reconciliation SHA:** `5b9d8a7b444f56aba4fb4ca98fca8af82133e37e`.
- **Phase-B implementation SHA:** `392ba90670cb2518ba216d123cc108071473942f`.
- **Phase-B final code/qualification SHA:** `24b46e9176459a787886b0524e9e0dd3339d7b0f`.
- **Phase-B backend CI:** run `35350346488` — **PASS**.
  - Ruff/lint: PASS.
  - Partition A: **3952 passed / 430 deselected / 0 failed / 0 skipped**.
  - Partition B: **300 passed / 4082 deselected / 0 failed / 0 skipped**.
  - Partition C: **130 passed / 4252 deselected / 0 failed / 0 skipped**.
  - All A/B/C zero-skip qualification assertions: PASS.
- **Phase-B frontend CI:** run `35350346502` — **PASS**.
  - Web tests: PASS.
  - Next production build: PASS.
  - Workspace package build: PASS.
  - Android native project generation + native compile: PASS.
  - iOS native project generation + CocoaPods + native compile: PASS.
- **Vercel exact-head status for `24b46e917...`:** SUCCESS.
- **Task-1 repository/CI migration head:** `20260916_patient_external_record_import`.
- **Linear migration chain:** `20260914_patient_search_identifiers → 20260916_clinical_access_sessions → 20260917_treatment_session_operations → 20260916_patient_external_record_import`.
- **Approved production/pilot migration head remains:** `20260917_treatment_session_operations`. Task-1 qualification does not silently mark the patient-import migration pilot-approved.
- **Migration contention:** RESOLVED after Slice-10B4 landed. No Alembic merge revision was created; Task-1 was linearized as the child of `20260917_treatment_session_operations`.
- **Route-governance overlap:** RESOLVED semantically. Both treatment-session V1 routes and all five patient external-record routes are preserved.
- **Phase B target:** COMPLETE / QUALIFIED.
- **Exact next Task-1 increment:** patient review/correction over encrypted `PatientExternalRecordCandidate` values, preserving evidence and patient correction provenance. Do not combine review with typed clinical finalization.
- **Protected Slice-10B behavior:** remains unchanged by Task-1 extraction. Do not alter `ClinicalAccessSession`, Signed Consent V3, treatment-session authority, provider treatment-consent authority, or provider delegated-trust semantics.
- **Unexpected leftover refs:** prior tooling left `tmp-inspect-fe57-patient-import`, `ops/task1-exact-head-qualification-2`, and `_phaseb-object-check`. The available connector exposes no ref-deletion action. Do not use or repurpose these refs.

## Current Scope

Authenticated patient self-import of external medical records from onboarding and Records: category selection, encrypted source retention, patient-authority extraction, patient-friendly review/correction, provenance-aware typed finalization where repository semantics safely support it, timeline projection, source viewing, retry/recovery, lifecycle handling, and qualification.

The completed Phase-B coding target is intentionally narrower: extract a patient-owned retained source into encrypted `PatientExternalRecordCandidate` rows and stop at explicit review.

## Explicit Non-Scope

- Redesigning `ClinicalAccessSession`, Signed Consent V3, signed treatment sessions, provider treatment consent, or delegated-provider trust.
- Creating provider, hospital, tenant, facility, treatment-consent, or clinical-session authority for a patient-self import.
- Calling the provider delegated extraction orchestrator as patient authority.
- Treating extraction output as authoritative clinical truth.
- Auto-committing AI/OCR output.
- Typed clinical finalization or timeline publication during Phase B.
- Creating another Task-1 schema migration for extraction; the existing candidate model is sufficient for this increment.
- Creating a sibling/merge migration to resolve the new current-main migration contention.
- Frontend/onboarding/Records UX in this increment.

## Repository Baseline

### Task-1 lineage

- Original Task-1 base: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Frozen Phase-A qualification SHA: `72ea0eb9082e64fc9bae07981af06ba34ad40a2e`.
- Phase-A handoff documentation commit: `3a0d203636ac3358e8f551ac23b26d291864ac50`.
- Phase-B extraction implementation commit: `392ba90670cb2518ba216d123cc108071473942f`.
- Semantic current-main reconciliation commit: `5b9d8a7b444f56aba4fb4ca98fca8af82133e37e`.
- Phase-B hardening / exact qualification SHA: `24b46e9176459a787886b0524e9e0dd3339d7b0f`.

### Current main / concurrency

Latest observed main: `342d25cb960a3c81539502e6d3eacde4e7121aee` (`test(ci): advance Slice 4 qualification head`).

Reconciliation facts:

1. Slice-10B4's `20260917_treatment_session_operations` migration is fully landed on main.
2. Task-1's external-record migration now has `down_revision = "20260917_treatment_session_operations"`; the combined repository has one linear Alembic head.
3. `tests/test_route_registration.py` was resolved semantically from current main: all treatment-session V1 routes remain, and Task-1's five patient external-record routes are added.
4. Current-main pilot deployment governance remains authoritative for the approved pilot head (`20260917_treatment_session_operations`); Task-1 CI independently qualifies the later feature head.
5. No protected Slice-10B authority implementation was rewritten to make Task-1 pass.
6. Open PR inventory at the final Phase-B checkpoint: only PR #47.

## Product Contract

### New patient

Create account → required onboarding → Add previous medical records → choose category → choose camera/photo/PDF where supported → encrypted upload → processing → patient-friendly review/correction → retained original source → provenance-aware save where safe → record category + timeline → continue onboarding or Skip for now.

### Existing patient

Records → Add external record → choose category → upload → processing → review → save → categorized record + timeline.

Categories remain:

- Prescription
- Lab report
- Imaging / radiology report
- Discharge summary
- Other medical record

Patient-facing status vocabulary remains `processing`, `needs_review`, `imported`, `retry_available`, `could_not_process`, and `cancelled`. Internal pipeline lanes and engineering identifiers must never leak into patient UI.

## Security Invariants

1. Patient identity is derived from the authenticated patient session; client-selected patient UUID is never authority.
2. Patient upload/extraction authority is distinct from provider upload authority, treatment consent, Signed Consent V3, `ClinicalAccessSession`, hospital authority, and provider delegated extraction authority.
3. Patient-self extraction cannot grant provider access or provider write authority.
4. Patient source storage remains in the distinct patient-self encrypted namespace/AAD.
5. Background/process-time access revalidates patient ownership, soft-deletion state, and the erasure registry before source use.
6. Source bytes are read only through the patient-bound storage API and the retained SHA-256 digest is rechecked before the extraction provider is called.
7. Only the configured Nexa extraction adapter is accepted, and the returned adapter/contract provenance must match the server-owned configured adapter.
8. Only provider-authentic `field_evidence` can become candidates. Compatibility summary arrays are never promoted into facts.
9. Identity fields (`patient_name`, `phone`, `aadhaar_abha_id`) are excluded from patient clinical review candidates in this increment.
10. Raw candidate values and source snippets are encrypted using patient-bound KMS contexts before persistence.
11. Candidate output stops at `REVIEW_REQUIRED`; it is not clinician-created, clinician-verified, patient-confirmed, or canonical clinical truth.
12. No typed record or timeline entry is written by Phase B.
13. Audit metadata remains structural/value-free; raw source or extracted clinical values are never written to audit metadata.
14. Retryable and terminal extraction failures preserve the retained source and write stable error codes only where audit persistence succeeds.
15. Patient merge/retirement does not silently redirect self-import authority to another patient UUID; a retired identity is denied.

## Existing Components Reused

| Component | Classification | Phase-B use |
|---|---|---|
| Strict patient auth/session dependency | EXISTS / REUSED | Process route derives `patient_id` from `get_current_patient`. |
| Patient external import/candidate models | EXISTS / REUSED | No new extraction migration needed. |
| Patient-self encrypted source storage | EXISTS / REUSED | Owned source bytes only. |
| Configured extraction adapter | EXISTS / REUSED | Mechanism only; no provider authority inheritance. |
| `ExtractionProviderResult` / `ProviderFieldEvidence` | EXISTS / REUSED | Provenance-validated evidence boundary. |
| Generic `CurrentExtractionBinding` adapter | WRONG_FLOW for patient self | Requires `tenant_id`; Task-1 does not synthesize tenant authority. |
| Patient envelope encryption / KMS | EXISTS / REUSED | Encrypt candidate value/source text. |
| Erasure registry | EXISTS / REUSED | Fail-closed process-time lifecycle gate. |
| Audit outbox | EXISTS / REUSED | `EXTRACTION_JOB_STARTED`, `EXTRACTION_JOB_VALIDATED`, `EXTRACTION_JOB_FAILED`. |
| Provider delegated extraction orchestrator | WRONG_FLOW for patient self | Not called or weakened. |
| Typed-record commit/timeline | LATER PHASE | Phase B intentionally stops before canonical persistence. |

## Architecture Decisions

### 1. Patient-specific extraction orchestration

`app/services/patient_external_record_extraction.py` owns the patient-self process boundary. It takes only `db`, server-derived `patient_id`, and `import_id`. No provider/tenant/hospital/consent/session parameter exists.

### 2. Synchronous bounded process route

`POST /api/v2/patient/me/external-records/{import_id}/process` is synchronous for this increment and uses the existing upload cap/provider timeout behavior. The import row is locked while the process is evaluated, preventing concurrent duplicate candidate creation on the same import.

### 3. Provider evidence, not summary arrays

The configured extractor's validated `field_evidence` is the only candidate source. Extracted compatibility arrays such as diagnoses/lab/prescription summaries are intentionally ignored when evidence is absent.

### 4. Dedicated patient candidate persistence

Provider candidate tables and provider delegated authorization graph are not used. Candidates persist only in `PatientExternalRecordCandidate` with patient/import/source graph binding and encrypted sensitive values.

### 5. No schema delta for Phase B

The existing Task-1 model already contains the required encrypted candidate/evidence/review fields. No new migration is created.

### 6. Landed migration was reconciled linearly

After `20260917_treatment_session_operations` landed on main and no competing migration PR remained, Task-1 followed the repository rule to reconcile after landing. The patient-import revision now descends directly from the landed treatment-session revision. No Alembic merge revision was created, and approved pilot deployment remains independently pinned to the treatment-session revision.

## Work Log

### Baseline / audit / Phase A

- `aa218f24930140c0fc34a6b719403178e95ac0eb`: isolated branch + living handoff.
- `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`: architecture/security audit + draft PR.
- `cd8af0bce6e774a978ba2d9e728bb569ac11f694`: patient-owned persistence + linear migration.
- `21b7d4822688774f1d11d2ae0a77e0403b8b7584`: encrypted patient-self source staging.
- `ae29e8759942c5ffda8dc45916609628207bc684`: strict patient upload/list/detail/source API.
- Qualification repairs followed; Phase A ultimately froze green at `72ea0eb9082e64fc9bae07981af06ba34ad40a2e`.
- `3a0d203636ac3358e8f551ac23b26d291864ac50`: documentation-only Phase-A freeze.

### Phase B — patient-authority extraction target

- **Starting Phase-B SHA:** `3a0d203636ac3358e8f551ac23b26d291864ac50`.
- **Implementation SHA:** `392ba90670cb2518ba216d123cc108071473942f`.
- **Semantic main reconciliation SHA:** `5b9d8a7b444f56aba4fb4ca98fca8af82133e37e`.
- **Final hardening / qualification SHA:** `24b46e9176459a787886b0524e9e0dd3339d7b0f`.
- **Runtime files:** patient-specific extraction service plus patient-self process route only.
- **Focused security coverage:** configured-provider provenance, identity-field exclusion, evidence-only candidate creation, deterministic value-free evidence IDs, SHA-256 source integrity, patient/import row-lock binding, retired-patient denial, stable failure mapping, and explicit rollback/value-free response on unexpected runtime failures.
- **Migration reconciliation:** Task-1 migration was moved behind the fully landed `20260917_treatment_session_operations`; no merge revision was introduced.
- **Pilot-governance separation:** production/pilot migration tooling remains pinned to the approved treatment-session head. Feature-branch tests mock that repository-head precondition only where necessary to isolate runner behavior.
- **Exact backend qualification:** CI run `35350346488` — Ruff PASS; A 3952, B 300, C 130; zero skips/failures.
- **Exact frontend/native qualification:** Frontend CI run `35350346502` — web tests/build/workspace PASS; Android PASS; iOS PASS.
- **Exact deployment status:** Vercel SUCCESS on `24b46e917...`.
- **Not added:** provider authority, treatment consent, ClinicalAccessSession authority, provider delegated extraction, typed clinical commit, timeline write, auto-commit, or a new extraction migration.

## Test / Qualification Matrix

| Area | State | Evidence |
|---|---|---|
| Phase-A backend qualification | PASS | Frozen SHA `72ea0eb9...`; A 3923 / B 299 / C 130; zero skips. |
| Phase-B focused extraction/security tests | PASS | Included in Partition A on exact SHA `24b46e917...`. |
| Phase-B API authority/route contracts | PASS | Included in Partition A; process route derives patient authority and exposes no provider/tenant/consent inputs. |
| Alembic single-head / migration ancestry | PASS | Task-1 head descends from landed treatment-session head; CI shared DB migrated successfully. |
| Phase-B Ruff/lint | PASS | Backend CI run `35350346488`. |
| Phase-B Partition A | PASS | 3952 passed / 430 deselected / 0 failed / 0 skipped. |
| Phase-B Partition B | PASS | 300 passed / 4082 deselected / 0 failed / 0 skipped. |
| Phase-B Partition C | PASS | 130 passed / 4252 deselected / 0 failed / 0 skipped. |
| Web tests / Next build / workspace build | PASS | Frontend CI run `35350346502`. |
| Android native compile | PASS | Frontend CI run `35350346502`. |
| iOS native compile | PASS | Frontend CI run `35350346502`. |
| Vercel exact-head deployment | PASS | SUCCESS for `24b46e917...`. |
| Malware scanning | NOT VERIFIED / NOT IMPLEMENTED | No claim otherwise. |
| Decoder-level document validation | NOT VERIFIED | Existing checks remain envelope/signature/truncation level. |

## Open Risks / Blockers

1. Phase B itself has no open qualification blocker; its exact code SHA is green.
2. Patient review/correction is not implemented yet; candidates are intentionally non-canonical and remain at `REVIEW_REQUIRED`.
3. Typed finalization and timeline publication remain later phases. Prescription/Imaging/Discharge semantics must be audited before mapping; do not relabel Medication as Prescription.
4. Retry/cancel UX and complete lifecycle/retention/merge/erasure qualification remain incomplete.
5. Onboarding + Records patient frontend flow remains incomplete.
6. Malware scanning is not verified/implemented.
7. Full decoder-level corruption validation remains unverified beyond the existing structural checks.
8. Temporary refs remain because the connector does not expose ref deletion; do not use them.

## Merge / Rebase Safety

- Keep PR #47 draft and unmerged; Phase-B success does not complete the overall Task-1 workstream.
- The current reconciliation already includes main `342d25cb...`; do not rebase again merely because history is non-linear.
- Preserve the single Alembic chain ending in `20260916_patient_external_record_import`.
- Preserve main's approved pilot head separation unless Task-1 migration receives explicit pilot approval.
- Preserve both treatment-session and patient external-record route registrations.
- Re-check main/open PR overlap before the next major Task-1 increment and before final merge qualification.
- Do not modify protected Slice-10B authority semantics to implement patient review/correction or finalization.

## Final Completion Checklist

- [x] Patient-owned import persistence exists.
- [x] Patient-self encrypted source storage exists.
- [x] Strict patient upload/list/detail/source API exists.
- [x] Phase A exact backend qualification is green.
- [x] Patient-authority extraction service is implemented.
- [x] Patient process route is implemented.
- [x] Focused extraction authority/security tests execute successfully.
- [x] Extraction excludes provider/hospital/tenant/consent/session authority.
- [x] Extraction uses provider-authentic field evidence only.
- [x] Identity fields are excluded from Phase-B clinical review candidates.
- [x] Candidate sensitive values are encrypted before persistence.
- [x] Unexpected extraction failures explicitly roll back and return stable value-free errors.
- [x] Phase B stops at `REVIEW_REQUIRED`; no typed record/timeline write occurs.
- [x] Current main is semantically reconciled.
- [x] Combined migration graph is single-head and linear.
- [x] Shared route governance preserves both workstreams.
- [x] Exact-head Ruff + A/B/C + zero skips are green at `24b46e917...`.
- [x] Web tests / Next build / workspace packages / Android / iOS are green at `24b46e917...`.
- [x] Vercel exact-head status is green.
- [ ] Implement patient review/correction.
- [ ] Implement safe typed finalization where repository semantics support it.
- [ ] Publish provenance-aware timeline entries.
- [ ] Qualify retry/cancel/recovery and lifecycle/erasure/merge behavior.
- [ ] Implement onboarding + Records patient frontend flow.
- [ ] Complete final end-to-end Task-1 qualification.
- [ ] Keep PR draft until all required Task-1 phases are complete.



- [x] Patient-owned import persistence exists.
- [x] Patient-self encrypted source storage exists.
- [x] Strict patient upload/list/detail/source API exists.
- [x] Phase A exact backend qualification is green at frozen SHA `72ea0eb9...`.
- [x] Patient-authority extraction service is implemented at `392ba906...`.
- [x] Patient process route is implemented.
- [x] Focused extraction authority/security tests are written.
- [x] Extraction excludes provider/hospital/tenant/consent/session authority.
- [x] Extraction uses provider-authentic field evidence only.
- [x] Identity fields are excluded from Phase-B clinical review candidates.
- [x] Candidate sensitive values are encrypted before persistence.
- [x] Phase B stops at `REVIEW_REQUIRED`; no typed record/timeline write occurs.
- [ ] Resolve current-main migration ownership/order without sibling heads.
- [ ] Semantically reconcile latest main and shared route governance.
- [ ] Execute focused Phase-B tests successfully.
- [ ] Execute exact-head Ruff + A/B/C + zero skips successfully after reconciliation.
- [ ] Implement patient review/correction.
- [ ] Implement safe typed finalization where repository semantics support it.
- [ ] Publish provenance-aware timeline entries.
- [ ] Qualify retry/cancel/recovery and lifecycle/erasure/merge behavior.
- [ ] Implement onboarding + Records patient frontend flow.
- [ ] Qualify web/mobile/native/E2E surfaces.
- [ ] Update final PR/handoff with exact green SHA.
- [ ] Keep PR draft until all required qualification is complete.
