# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **Task-1 head presented for Phase-A qualification:** `acfcde41dc150bfb4c8442945c71798ad049ff46`
- **Observed `main` at qualification attempt:** `238c59b7fe94cc04213063f55750f3d727a062c7`
- **Reconciliation state:** `acfcde41...` is the deliberate two-parent merge of Task-1 repair head `4f9ba427428d2001ce04c362a6c2d8c3e0bd66d4` and `main` `238c59b7...`.
- **PR:** #47, `feat(patient): integrate external medical record import workflow` — **OPEN / DRAFT / UNMERGED**.
- **Feature-branch migration head:** `20260916_patient_external_record_import`.
- **Migration chain:** `20260914_patient_search_identifiers → 20260916_clinical_access_sessions → 20260916_patient_external_record_import`.
- **Approved production/pilot migration head:** remains `20260916_clinical_access_sessions`. Do **not** change `scripts/run_pilot_migrations.py` merely to make the Task-1 draft feature look pilot-approved.
- **Current phase:** Phase A exact-head qualification. Phase B patient-authority extraction must not be committed until Phase A is genuinely green.
- **Qualification blocker:** GitHub Actions backend run `35135458394` and Frontend CI run `35135458252` for `acfcde41...` completed immediately with conclusion `action_required`. Backend run `35135458394` contains **zero jobs**, so lint, Partition A/B/C, migration qualification, and zero-skip assertions did not execute. The available GitHub connection exposes no workflow-approval or workflow-dispatch action. A retry of failed jobs returned HTTP 403 because this run is not retryable in that state.
- **Phase-A result on `acfcde41...`: BLOCKED / NOT RUN.** No backend partition PASS is claimed. Historical evidence from `ae29e875...` or `4f9ba427...` must not be reused as exact-head qualification.
- **Exact next step:** obtain authorized GitHub Actions approval/execution for the current Task-1 head, then require real green A/B/C + zero skips + single-head migration evidence. Only after that may patient-authority extraction be committed.
- **Open PR inventory:** only PR #47 was open at the latest check.
- **Unexpected leftover branches:** `ops/task1-exact-head-qualification-2` points to `acfcde41...`; `tmp-inspect-fe57-patient-import` points to `21b7d482...`. Neither contains a newer Task-1 implementation. Do not use or repurpose them. The available connector has no ref-deletion action.
- **Protected Slice-10B behavior:** do not alter `ClinicalAccessSession`, Signed Consent V3, provider treatment-consent authority, or provider delegated-trust semantics to make patient import work.
- **Local execution limitation:** the local sandbox has no usable repository checkout/network path for reproducing the full backend suite, so GitHub Actions is the required evidence source here.
- **Known unresolved product/security gaps:** malware scanning remains **NOT CURRENTLY VERIFIED / NOT IMPLEMENTED**; full decoder-level corruption validation remains unverified beyond MIME/signature and obvious truncation checks.

> This handoff update is documentation-only. The commit containing this text necessarily becomes a newer branch head than `acfcde41...`; use the live branch ref after this commit as the current head, while treating `acfcde41...` as the exact Phase-A qualification attempt that was blocked before any job ran.

## Current Scope

Authenticated patient self-import of external medical records from onboarding and Records: category selection, encrypted source retention, patient-authority extraction, patient-friendly review/correction, typed-record finalization where semantics exist, timeline projection, safe source view, retries, audit/privacy/lifecycle behavior, and qualification.

## Explicit Non-Scope

- Redesign or broaden `ClinicalAccessSession`, Signed Consent V3, provider treatment consent, or provider record-write authority.
- Synthesize provider, hospital, tenant, or treatment authority for a patient-self import.
- Turn OCR/AI output directly into canonical clinical truth or enable auto-commit.
- Create a generic final medical-record JSON blob.
- Reinterpret Medication as Prescription when semantics do not match.
- Create sibling/merge migrations merely because `main` moves.
- Rewrite global erasure architecture.
- Implement the entire review/finalization/frontend workflow during the extraction increment unless a safe backend contract requires it.

## Product Contract

New patient: account → required onboarding → **Add previous medical records** → category → camera/photo/PDF where supported → encrypted upload → processing → patient-friendly review/correction → retained source → provenance-aware typed save where safe → record category + timeline → continue onboarding or **Skip for now**.

Existing patient: Records → **Add external record** → category → source upload → processing → review → save → categorized record + timeline.

Patient-facing categories remain Prescription, Lab report, Imaging / radiology report, Discharge summary, and Other medical record. Never expose provider-pipeline lanes, candidate IDs, workflow IDs, storage refs, object keys, consent internals, or extractor implementation details.

## Security Invariants

1. Patient ownership derives only from strict server-side patient JWT/session authority.
2. Client patient UUIDs, provider credentials/capabilities, treatment consent, `ClinicalAccessSession`, or synthetic provider/hospital identities cannot authorize patient-self import or extraction.
3. Patient source storage uses a distinct authenticated-encryption namespace/AAD and patient-self source rows do not synthesize tenant/provider authority.
4. Extraction output is only a candidate: extraction result != patient-confirmed fact != clinician-confirmed fact != finalized typed clinical record.
5. AI/OCR output remains untrusted until explicit review; auto-commit remains disabled.
6. Original extraction/evidence is retained when the patient corrects a value; patient correction is not clinician verification.
7. Source bytes, storage refs/keys, encryption metadata, raw extracted PII/clinical values, and unnecessary health values never enter URLs, logs, analytics, tracing labels, Redis keys, idempotency keys, or audit metadata.
8. Security-sensitive transitions use existing durable audit/outbox semantics and canonical audit vocabulary where semantically correct.
9. Same-patient idempotency replay is accepted only for the same category/content digest; changed semantics conflict; different patients may reuse key strings independently.
10. Patient merge/erasure/delete behavior must use canonical lifecycle architecture.
11. Provider document processing/storage and provider delegated-trust workflows must not regress.
12. Source ownership and erasure/canonical-patient state must be revalidated before background source read/extraction.

## Existing Components Reused / Intended Reuse

| Component | State | Task-1 use |
|---|---|---|
| `DocumentStorage` Local/S3 encrypted adapters | EXISTS / REUSED | Separate patient-self namespace/AAD with fail-closed patient methods. |
| Patient external import/candidate models | EXISTS | Patient-owned import and candidate persistence, separate from provider authority semantics. |
| Extraction adapter/provider abstraction | EXISTS / AUDITED | Intended Phase-B reuse for extraction mechanism only. |
| Authentic extraction field evidence | EXISTS / AUDITED | Intended Phase-B source/evidence boundary. |
| Patient KMS/DEK encryption primitives | EXISTS / AUDITED | Intended Phase-B candidate-value encryption. |
| Audit outbox / audit context | EXISTS / REUSED | Structural, value-free audit events. |
| Provider delegated extraction orchestrator | WRONG_FLOW for patient-self authority | Do not call or weaken; wrap shared mechanism in a patient-specific orchestrator instead. |
| `ClinicalAccessSession` / Signed Consent V3 | WRONG_FLOW for patient-self import | Protected provider/treatment-consent authority; not required for patient upload/extraction. |

## Architecture Decisions

### Patient-owned persistence and source namespace
Task-1 import/candidate rows bind directly to patient + source document and contain no provider/hospital/treatment-consent authority columns. Patient source objects use a patient-self encrypted namespace and patient-bound AAD, separate from provider tenant storage.

### Patient API authority
`/api/v2/patient/me/external-records` derives patient identity from authenticated server context; no caller-selected patient UUID is accepted. Upload/list/detail/source responses use patient language and `Cache-Control: private, no-store`.

### Audit vocabulary
Patient upload/source view reuse existing `DOCUMENT_UPLOADED` and `DOCUMENT_SOURCE_VIEWED` events with structural patient-self authority metadata. Do not create duplicate audit vocabulary without a semantic need.

### Feature-branch schema head vs approved deployment head
The repository head on Task-1 is `20260916_patient_external_record_import`. CI disposable databases must migrate there to qualify the feature. The production/pilot migration runner remains pinned to the independently approved `20260916_clinical_access_sessions` head until release approval.

### Storage interface compatibility
The original provider storage methods remain abstract. Patient-self storage methods are concrete fail-closed defaults raising `DocumentStorageError`; Local/S3 implementations override them. Existing provider-only implementations remain instantiable.

### Patient-authority extraction design, not yet committed
Safe reuse point: configured extraction adapter → authentic field evidence/provider-version metadata → patient-bound KMS/DEK encryption → `PatientExternalRecordCandidate`. Do not use provider candidate tables or delegated-trust job authority. Exclude identity fields from review candidates unless a later explicit product contract says otherwise. Do not promote evidence-less summary arrays into reviewable facts.

## Work Log

### Entry 1 — Baseline / isolation
- Start: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- End: `aa218f24930140c0fc34a6b719403178e95ac0eb`
- Created isolated Task-1 branch and living handoff.

### Entry 2 — Architecture audit + draft PR
- Start: `aa218f24930140c0fc34a6b719403178e95ac0eb`
- End: `c7bc7998f9a288b8f9cdf9a438653f1da13b6f0b`
- Audited patient/provider authority, secure storage/extraction/evidence, typed records/timeline, auth/client paths; opened draft PR #47.

### Entry 3 — Patient-owned persistence
- End: `cd8af0bce6e774a978ba2d9e728bb569ac11f694`
- Added patient-owned import/candidate persistence and linear migration from `20260916_clinical_access_sessions`.

### Entry 4 — Encrypted patient source staging
- End: `21b7d4822688774f1d11d2ae0a77e0403b8b7584`
- Added distinct patient-self encrypted storage namespace and staging service.

### Entry 5 — Strict patient self-service API
- End: `ae29e8759942c5ffda8dc45916609628207bc684`
- Added strict upload/list/detail/source API, server-derived patient authority, semantic idempotency, no-store behavior, stable failures, and envelope validation.

### Entry 6 — Qualification repair
- Repair lineage reaches `4f9ba427428d2001ce04c362a6c2d8c3e0bd66d4` before reconciliation with current main.
- Historical exact-head CI on `ae29e875...`: lint PASS; Partition A 3880 passed / 8 failed / 429 deselected; Partition B 297 passed / 2 failed; Partition C 128 passed / 2 failed.
- Repaired only evidence-backed roots: canonical audit vocabulary reuse, route allowlist, CI feature-head provisioning, migration ancestry tests, and provider `DocumentStorage` compatibility.
- These counts are historical red evidence only, not current Phase-A qualification.

### Entry 7 — Reconcile current main
- Reconciled Task-1 repair head with `main` `238c59b7fe94cc04213063f55750f3d727a062c7`.
- Resulting two-parent Task-1 commit: `acfcde41dc150bfb4c8442945c71798ad049ff46`.
- Protected Slice-10B authority behavior remains separate.

### Entry 8 — Phase-A exact-head qualification attempt: BLOCKED
- Target: `acfcde41dc150bfb4c8442945c71798ad049ff46`.
- Backend CI: run `35135458394` (`CI`, run 658), status completed, conclusion `action_required`, **0 jobs**.
- Frontend CI: run `35135458252` (`Frontend CI`, run 607), conclusion `action_required`.
- GitHub workflow YAML still defines lint, Partition A, Partition B, Partition C, and zero-skip assertions; none executed because no job was created.
- Workflow approval history endpoint returned no environment approvals; this is not an environment-job execution result.
- Re-running failed jobs returned HTTP 403: workflow run cannot be retried.
- No workflow-approval or workflow-dispatch write action is exposed by the available connection.
- Therefore Phase A is **BLOCKED / NOT RUN**. No extraction code was committed.
- Open PR inventory: only PR #47.
- Branch inventory also exposed `ops/task1-exact-head-qualification-2` at `acfcde41...` and `tmp-inspect-fe57-patient-import` at `21b7d482...`; connector cannot delete refs.
- PR #47 body was updated to this truthful checkpoint and kept draft/unmerged.

## Test / Qualification Matrix

| Area | State | Evidence |
|---|---|---|
| Ruff/lint on `acfcde41...` | NOT RUN | Backend run `35135458394` stopped at `action_required` before jobs existed. |
| Backend Partition A on `acfcde41...` | NOT RUN | 0 backend jobs created. |
| Backend Partition B on `acfcde41...` | NOT RUN | 0 backend jobs created. |
| Backend Partition C on `acfcde41...` | NOT RUN | 0 backend jobs created. |
| Zero-skip assertions on `acfcde41...` | NOT RUN | No partition JUnit output exists. |
| Migration graph / single head on `acfcde41...` | NOT RUN | CI blocked before jobs. |
| Disposable PostgreSQL to Task-1 feature head | NOT RUN | CI blocked before jobs. |
| Historical lint on `ae29e875...` | PASS (historical only) | GitHub Actions run `35130197618`. |
| Historical Partition A on `ae29e875...` | FAIL | 3880 passed / 8 failed / 429 deselected. |
| Historical Partition B on `ae29e875...` | FAIL | 297 passed / 2 failed. |
| Historical Partition C on `ae29e875...` | FAIL | 128 passed / 2 failed. |
| Patient-authority extraction focused tests | NOT RUN / NOT COMMITTED | Phase A must become green first. |
| Malware scanning | NOT VERIFIED / NOT IMPLEMENTED | Do not claim otherwise. |
| Deep decoder-level corruption checks | NOT VERIFIED | Current upload guard proves only MIME/signature + obvious truncation envelope. |
| Frontend/mobile/native/E2E | NOT QUALIFIED | Current Frontend CI run is `action_required`; frontend feature work is later scope. |

## Open Risks / Blockers

1. **Hard blocker:** exact-head GitHub Actions for the reconciled Task-1 head requires an approval/authorization path unavailable to this connection. No A/B/C evidence exists for `acfcde41...`.
2. Patient-authority extraction cannot be committed until Phase A is genuinely green.
3. Unexpected leftover branches from prior inspection/qualification tooling remain because ref deletion is unavailable; neither should be used.
4. Main may continue evolving Slice 10B; compare overlap before resuming but do not rebase merely because main moves.
5. Patient review/correction, typed finalization, timeline, retry/cancel, lifecycle, and frontend remain incomplete.
6. Typed Prescription/Imaging/Discharge semantics still require finalization audit; never relabel Medication as Prescription.
7. Malware scanning and decoder-level validity remain unresolved product/security gaps.

## Merge / Rebase Safety Notes

- Original Task-1 base: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Reconciled code checkpoint: `acfcde41dc150bfb4c8442945c71798ad049ff46` with parents `4f9ba427...` and `238c59b7...`.
- Do not merge PR #47.
- Do not mark PR #47 ready merely because GitHub says it is mergeable.
- Do not rebase merely because main advances; re-check overlap and only integrate when required.
- Task-1 migration remains one linear child of `20260916_clinical_access_sessions`; do not create a sibling/merge migration.
- Production/pilot runner remains pinned to `20260916_clinical_access_sessions` until independent release approval.
- Before resuming Phase B, fetch live main, Task-1 branch, PR #47, open PRs, and Actions state again.

## Final Completion Checklist

- [x] Patient-owned import persistence implemented (qualification pending).
- [x] Distinct encrypted patient-self source storage implemented (qualification pending).
- [x] Strict patient upload/list/detail/source API implemented (qualification pending).
- [x] Current main deliberately reconciled into Task-1 at `acfcde41...`.
- [x] PR #47 body updated to the current blocked qualification checkpoint.
- [ ] Exact-head Phase-A backend CI green on a frozen current Task-1 SHA.
- [ ] Exact A/B/C pass counts recorded with zero skips.
- [ ] Migration graph + disposable PostgreSQL feature-head qualification green.
- [ ] Patient-authority extraction/evidence orchestration.
- [ ] Focused adversarial extraction tests.
- [ ] Explicit patient review/correction with original-value provenance.
- [ ] Safe typed-record finalization where canonical semantics exist.
- [ ] Timeline projection exactly once after canonical persistence.
- [ ] Retry/resume/cancel behavior.
- [ ] Merge/erasure/delete lifecycle qualification.
- [ ] Onboarding + Records frontend entry points and Skip for now.
- [ ] Full backend PostgreSQL/Redis/security partitions green with zero required skips.
- [ ] Full frontend/build/native/E2E qualification as applicable.
- [ ] Final exact SHA frozen, PR body current, draft PR integration-ready but not merged without release-owner instruction.
