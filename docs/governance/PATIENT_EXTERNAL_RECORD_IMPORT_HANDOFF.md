# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **PR:** #47, `feat(patient): integrate external medical record import workflow` — **OPEN / DRAFT / UNMERGED**.
- **Phase-B code commit:** `392ba90670cb2518ba216d123cc108071473942f`.
- **Phase-B code target:** patient-authority extraction service + process route + focused security/authority tests.
- **Frozen Phase-A qualification SHA:** `72ea0eb9082e64fc9bae07981af06ba34ad40a2e`.
- **Phase-A backend CI:** run `35137558974` — PASS.
  - Ruff/lint: PASS.
  - Partition A: 3923 passed / 429 deselected / 0 failed / 0 skipped.
  - Partition B: 299 passed / 4053 deselected / 0 failed / 0 skipped.
  - Partition C: 130 passed / 4222 deselected / 0 failed / 0 skipped.
  - All A/B/C zero-skip qualification assertions: PASS.
- **Task-1 feature migration head:** `20260916_patient_external_record_import`.
- **Task-1 migration chain:** `20260914_patient_search_identifiers → 20260916_clinical_access_sessions → 20260916_patient_external_record_import`.
- **Latest observed `main`:** `860e261b86a399d98497291133e7544be7b78b5a`.
- **Current-main migration contention:** `main` now contains `20260917_treatment_session_operations` with `down_revision = 20260916_clinical_access_sessions`. It is therefore a sibling of Task-1's `20260916_patient_external_record_import` if the branches are naively combined.
- **Integration rule:** do **not** create a merge migration, retarget either migration, change Slice-10B migration ownership, or force-rebase this branch until the migration owner chooses the linear integration order.
- **PR merge state after Phase B push:** GitHub reports PR #47 `mergeable=false`; no exact-head Actions run was created for `392ba906...` at the latest check. Treat Phase-B qualification as **NOT RUN / BLOCKED**, not failed and not green.
- **Exact next step once migration ownership is resolved:** semantically reconcile current `main`, preserving both workstreams' route-governance changes, then run focused Phase-B tests followed by exact-head A/B/C + zero-skip qualification.
- **Protected Slice-10B behavior:** do not alter `ClinicalAccessSession`, Signed Consent V3, treatment-session operation claims, provider treatment-consent authority, or provider delegated-trust semantics to make patient import work.
- **Unexpected leftover refs:** prior tooling already left `tmp-inspect-fe57-patient-import` and `ops/task1-exact-head-qualification-2`; this continuation also accidentally created `_phaseb-object-check`, pointing only to the pre-Phase-B Task-1 head. The available connector exposes no ref-deletion action. Do not use or repurpose these refs.

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
- Qualified Phase-A SHA: `72ea0eb9082e64fc9bae07981af06ba34ad40a2e`.
- Phase-A handoff documentation commit: `3a0d203636ac3358e8f551ac23b26d291864ac50`.
- Phase-B extraction implementation commit: `392ba90670cb2518ba216d123cc108071473942f`.

### Current main / concurrency

Latest observed main: `860e261b86a399d98497291133e7544be7b78b5a` (`docs(security): freeze 10B4 claim-mint qualification target`).

Current main has materially advanced Slice 10B4. Relevant overlap/integration facts:

1. `tests/test_route_registration.py` is now changed by both Task-1 and Slice 10B4. Task-1 adds patient external-record routes; Slice 10B4 adds treatment-session routes. This must be semantically merged, never resolved with blanket ours/theirs.
2. Current main introduced `alembic/versions/20260917_treatment_session_operations.py` as a direct child of `20260916_clinical_access_sessions`.
3. Task-1 already has `20260916_patient_external_record_import` as a direct child of the same revision.
4. Therefore a naive branch merge creates two Alembic heads. This is a hard integration blocker under Task-1 migration governance.
5. Slice-10B protected authority files were not modified by the Phase-B Task-1 commit.

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

### 6. Current-main migration contention is not solved inside Task-1

Current main's `20260917_treatment_session_operations` and Task-1's `20260916_patient_external_record_import` are sibling revisions. The safe integration order must be chosen by the migration owner before final reconciliation; Task-1 will not fabricate a merge migration.

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

- **Starting SHA:** `3a0d203636ac3358e8f551ac23b26d291864ac50`.
- **Implementation SHA:** `392ba90670cb2518ba216d123cc108071473942f`.
- **Files changed:**
  - `app/services/patient_external_record_extraction.py` — new.
  - `app/api/v2/patient_external_record_routes.py` — adds process route.
  - `tests/test_patient_external_record_extraction.py` — new focused security/authority tests.
  - `tests/test_patient_external_record_api_contract.py` — process-route authority contract.
  - `tests/test_route_registration.py` — intentional fifth Task-1 patient route.
- **Behavior:** owned source → lifecycle/ownership/integrity checks → configured extractor → provider-authentic non-identity evidence → patient KMS encryption → patient candidate rows → `REVIEW_REQUIRED`.
- **Not added:** provider authority, treatment consent, clinical session, typed clinical commit, timeline write, auto-commit, or schema changes.
- **Static diff check:** exactly five paths; no protected Slice-10B file and no migration file changed.
- **Qualification:** focused tests were written but not executed. No new Actions run was created for `392ba906...` after current-main migration/merge contention made PR #47 non-mergeable.

## Test / Qualification Matrix

| Area | State | Evidence |
|---|---|---|
| Phase-A Ruff/lint | PASS | CI run `35137558974` on `72ea0eb9...`. |
| Phase-A Partition A | PASS | 3923 passed / 0 skipped. |
| Phase-A Partition B | PASS | 299 passed / 0 skipped. |
| Phase-A Partition C | PASS | 130 passed / 0 skipped. |
| Phase-B focused extraction tests | WRITTEN / NOT RUN | `tests/test_patient_external_record_extraction.py`; Actions run not created for `392ba906...`. |
| Phase-B API authority tests | WRITTEN / NOT RUN | process route requires strict patient dependency and exposes no provider/tenant/consent inputs. |
| Phase-B route registration | WRITTEN / NOT RUN | Branch allowlist includes process route; current-main allowlist has independent Slice-10B4 changes requiring semantic reconciliation. |
| Phase-B full A/B/C | NOT RUN / BLOCKED | PR #47 currently non-mergeable due current-main divergence including sibling migration heads. |
| Alembic single-head after combining latest main | BLOCKED | Two direct children of `20260916_clinical_access_sessions`; do not claim a single combined head. |
| Malware scanning | NOT VERIFIED / NOT IMPLEMENTED | No claim otherwise. |
| Decoder-level document validation | NOT VERIFIED | Existing upload checks remain envelope/signature/truncation level. |
| Frontend/mobile/native/E2E | NOT QUALIFIED | Later Task-1 phase. |

## Open Risks / Blockers

1. **Hard integration blocker — migration contention:** current main owns `20260917_treatment_session_operations`, a sibling of Task-1's external-record migration. Migration owner must choose the linear order before reconciliation.
2. **Shared route-governance overlap:** `tests/test_route_registration.py` has legitimate changes in both workstreams. Resolve semantically after migration order is established.
3. **Phase-B tests not executed:** PR conflict prevented a new exact-head PR Actions run at the latest check. Do not call Phase B green.
4. Temporary refs remain because connector does not expose ref deletion; do not use them.
5. Review/correction, typed finalization, timeline, retry/cancel UX, lifecycle retention/erasure qualification, and frontend are still incomplete.
6. Prescription/Imaging/Discharge typed semantics must still be audited before finalization; do not relabel Medication as Prescription.
7. Malware scanning and full decoder validity remain unresolved security/product gaps.

## Merge / Rebase Safety

- Keep PR #47 draft and unmerged.
- Do not force-rebase merely because main moved.
- Do not merge the two migration heads or create an Alembic merge revision inside Task-1 without migration-owner agreement.
- When the migration order is resolved, reconcile current main semantically. Preserve both Task-1 patient routes and Slice-10B4 treatment-session routes in `tests/test_route_registration.py`.
- Re-run focused Phase-B tests after reconciliation, then full Ruff + backend A/B/C + zero-skip assertions on the exact new SHA.
- Re-check current main/open PRs immediately before that qualification because Slice 10B is actively moving.
- Do not modify protected Slice-10B authority semantics to make tests pass.

## Final Completion Checklist

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
