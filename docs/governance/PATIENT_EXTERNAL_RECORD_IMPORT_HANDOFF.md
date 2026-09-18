# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`.
- **PR:** #47, `feat(patient): integrate external medical record import workflow` — **OPEN / DRAFT / UNMERGED**.
- **Latest observed main:** `34510ec1e308762cf2836c70de7e1cc8a828b39d`.
- **Latest semantic current-main reconciliation SHA:** `10c7fac1efd88c5a4e31b2cb7eb1cbd5e7a7bf41`.
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
- **Route-governance overlap:** RESOLVED semantically after PR #48 merged. Slice 11B longitudinal routes and all existing Task-1 external-record routes are preserved on `10c7fac1...`.
- **Phase B target:** COMPLETE / QUALIFIED.
- **Exact branch-tip rerun:** backend CI `35351247909`, frontend CI `35351247888`, and Vercel on `b0177393...` are all PASS/SUCCESS.
- **Phase C1 target:** COMPLETE / QUALIFIED — patient review/correction service foundation over encrypted `PatientExternalRecordCandidate` values, preserving extracted evidence and patient correction provenance. No typed clinical finalization or timeline write.
- **Phase C1 qualified SHA:** `1f53e830f00290539e5f39ed49ccfcc58f848707`.
- **Phase C1 backend CI:** run `35353447620` — Ruff PASS; A **3961 passed / 430 deselected / 0 skipped**, B **300 / 4091 / 0**, C **130 / 4261 / 0**; zero failures.
- **Phase C1 frontend CI:** run `35353447680` — web tests, Next production build, workspace packages, Android native compile, and iOS native compile all PASS.
- **Phase C1 Vercel:** SUCCESS on `1f53e830...`.
- **Current concurrency:** PR #48 is MERGED and reconciled. PR #50 remains open on the protected treatment-session operation gate and does not overlap the review-route files.
- **Phase C2 target:** patient-self review route publication — GET review snapshot + POST per-item accept/correct/reject, strict patient authority, opaque review-item IDs, no internal candidate field names or provider/tenant/consent/session inputs.
- **Phase C2 qualification state:** IMPLEMENTED / NOT YET QUALIFIED at the handoff update point. Exact next step is commit the route slice, run exact-head backend/frontend/native/Vercel qualification, then freeze the result.
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

Latest observed main: `34510ec1e308762cf2836c70de7e1cc8a828b39d`. Slice 11B longitudinal patient-record routes/UI from PR #48 are fully landed and semantically reconciled into Task-1 at `10c7fac1...`.

Reconciliation facts:

1. Slice-10B4's `20260917_treatment_session_operations` migration is fully landed on main.
2. Task-1's external-record migration now has `down_revision = "20260917_treatment_session_operations"`; the combined repository has one linear Alembic head.
3. `tests/test_route_registration.py` was resolved semantically from current main: all treatment-session V1 routes remain, and Task-1's five patient external-record routes are added.
4. Current-main pilot deployment governance remains authoritative for the approved pilot head (`20260917_treatment_session_operations`); Task-1 CI independently qualifies the later feature head.
5. No protected Slice-10B authority implementation was rewritten to make Task-1 pass.
6. Phase-B exact-tip rerun on `b0177393...` is fully green: backend `35351247909`, frontend `35351247888`, Vercel SUCCESS.
7. PR #48 (Slice 11B longitudinal records UX) is merged into main and reconciled into Task-1.
8. `tests/test_route_registration.py` now contains both Slice 11B longitudinal routes and Task-1 external-record routes; the prior same-file blocker is closed.
9. Phase-C1 exact-head qualification on `1f53e830...` is green across backend, web, Android, iOS, and Vercel.
10. Current open PR inventory before Phase C2 qualification: #47 (Task-1) and #50 (10B.5 clinical-session gate).

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

Patient-facing status vocabulary is `processing`, `needs_review`, `ready_to_save`, `imported`, `retry_available`, `could_not_process`, and `cancelled`. Internal pipeline lanes and engineering identifiers must never leak into patient UI.

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
16. Patient review must preserve extracted evidence separately from patient corrections; accepting or rejecting a field never overwrites the extracted ciphertext.
17. Patient corrections use a distinct patient-bound encryption context and are not clinician verification or canonical clinical truth.
18. `READY_TO_SAVE` means review is complete only; it does not authorize typed persistence, timeline publication, or provider access.

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

### 7. Review/correction preserves two-value provenance

Phase C1 uses a dedicated patient review service. Extracted value/source ciphertext stays immutable. A patient correction is stored only in `encrypted_reviewed_value` under a distinct patient-bound encryption context, with `patient_reviewed`, `review_status`, and `reviewed_at` recording the explicit decision. When no candidate remains `NEEDS_REVIEW`, the import advances to `READY_TO_SAVE`; no typed record or timeline row is created.

### 8. Review routes publish only after shared route ownership clears

Phase C1 intentionally stopped at the service boundary while PR #48 owned `tests/test_route_registration.py`. After PR #48 merged, Task-1 reconciled its landed longitudinal routes first, then Phase C2 adds only two patient-self review routes. This preserves concurrent work rather than overwriting the shared route registry.

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

### Phase C1 — patient review/correction service foundation

- **Starting branch tip:** `b0177393869cdb64a4c1d5d22615d3eaf4d996a9` — exact-tip backend/frontend/native/Vercel green.
- **New service:** `app/services/patient_external_record_review.py`.
- **Status projection:** `READY_TO_SAVE` now maps to patient-visible `ready_to_save` instead of remaining `needs_review`.
- **Behavior:** owned import/candidate graph → patient lifecycle/erasure gate → decrypt extracted evidence → explicit accept/correct/reject → correction re-encryption under distinct context → value-free audit event → `READY_TO_SAVE` only when all candidate decisions are resolved.
- **Provenance:** extracted ciphertext is never overwritten by a correction; accept/reject clears stale correction ciphertext instead of copying plaintext.
- **Focused tests:** patient-only authority surface, lock binding, correction validation, separated encryption contexts, extracted/corrected review projection, READY_TO_SAVE transition, and accept-without-plaintext-copy.
- **No schema change. No typed record or timeline write.**
- **Former route blocker:** RESOLVED after PR #48 merged and was reconciled at `10c7fac1...`.

### Phase C2 — patient review API publication

- **Starting reconciliation SHA:** `10c7fac1efd88c5a4e31b2cb7eb1cbd5e7a7bf41`.
- **Routes:** `GET /api/v2/patient/me/external-records/{import_id}/review` and `POST /api/v2/patient/me/external-records/{import_id}/review/{review_item_id}`.
- **Authority:** both routes derive patient identity only from `get_current_patient`; caller-supplied patient/provider/hospital/tenant/consent/session authority is not accepted.
- **Request hardening:** review mutation payload is `extra="forbid"`, so authority-shaped or unknown fields are rejected rather than ignored.
- **Patient projection:** review response exposes opaque `review_item_id`, patient-friendly label, extracted/corrected values, decision, source context, and confirmation requirement. Internal candidate field names, clinical fact keys, extractor provider/version, storage refs, and internal lanes are not exposed.
- **Persistence boundary:** mutations call the already-qualified Phase-C1 service and stop at `READY_TO_SAVE`; no typed clinical commit or timeline write is introduced.
- **Shared route registry:** Slice 11B routes remain; Task-1 registry expands from five to seven external-record routes.
- **Qualification:** PENDING for the exact Phase-C2 commit.

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
| Phase-C1 review service tests | PASS | Exact SHA `1f53e830...`; backend CI `35353447620`: A 3961 / B 300 / C 130, zero skips/failures; audit catalog repaired. |
| Phase-C2 review API routes | WRITTEN / NOT RUN | PR #48 is merged; two strict patient-self routes plus authority/unknown-field/internal-projection contracts are staged for exact-head qualification. |

## Open Risks / Blockers

1. Phase B itself has no open qualification blocker; both code SHA and documentation-only branch-tip rerun are green.
2. Phase C1 review/correction service foundation is qualified at `1f53e830f00290539e5f39ed49ccfcc58f848707`; the initial audit-catalog-only failure was repaired and the exact repaired SHA is green.
3. The prior route-registry blocker is resolved; Phase C2 route publication still needs exact-head qualification before it can be frozen green.
4. Candidates remain non-canonical; `READY_TO_SAVE` means review complete, not clinical persistence.
5. Typed finalization and timeline publication remain later phases. Prescription/Imaging/Discharge semantics must be audited before mapping; do not relabel Medication as Prescription.
6. Retry/cancel UX and complete lifecycle/retention/merge/erasure qualification remain incomplete.
7. Onboarding + Records patient frontend flow remains incomplete.
8. Malware scanning is not verified/implemented.
9. Full decoder-level corruption validation remains unverified beyond the existing structural checks.
10. Temporary refs remain because the connector does not expose ref deletion; do not use them.

## Merge / Rebase Safety

- Keep PR #47 draft and unmerged; Phase-B success does not complete the overall Task-1 workstream.
- The current reconciliation includes main `34510ec1...` at `10c7fac1...`; do not rebase again merely because history is non-linear.
- Preserve the single Alembic chain ending in `20260916_patient_external_record_import`.
- Preserve main's approved pilot head separation unless Task-1 migration receives explicit pilot approval.
- Preserve both treatment-session and patient external-record route registrations.
- Re-check main/open PR overlap before every major Task-1 increment and before final merge qualification.
- Preserve the merged Slice 11B longitudinal patient-record routes/frontend byte-for-byte unless a later Task-1 UI integration explicitly requires a semantic change.
- PR #50 remains active; do not modify protected Slice-10B treatment-session gate semantics to implement patient review/correction or finalization.

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
- [x] Implement isolated patient review/correction service foundation.
- [x] Preserve extracted ciphertext separately from patient corrections.
- [x] Add patient-visible `ready_to_save` workflow status.
- [x] Publish patient review/correction API routes after PR #48 route-registry overlap resolved.
- [x] Qualify the Phase-C1 service increment on exact SHA `1f53e830...` after the audit-catalog repair.
- [ ] Qualify the Phase-C2 review-route increment on its exact committed SHA.
- [ ] Implement safe typed finalization where repository semantics support it.
- [ ] Publish provenance-aware timeline entries.
- [ ] Qualify retry/cancel/recovery and lifecycle/erasure/merge behavior.
- [ ] Implement onboarding + Records patient frontend flow.
- [ ] Complete final end-to-end Task-1 qualification.
- [ ] Keep PR draft until all required Task-1 phases are complete.
