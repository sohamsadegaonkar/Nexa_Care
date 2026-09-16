# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **PR:** #47, `feat(patient): integrate external medical record import workflow` — **OPEN / DRAFT / UNMERGED**.
- **Frozen Phase-A qualification SHA:** `72ea0eb9082e64fc9bae07981af06ba34ad40a2e`.
- **Phase-A result:** **PASS** on backend CI run `35137558974`.
- **Lint:** PASS.
- **Partition A:** 3923 passed / 429 deselected / 0 failed / 0 skipped.
- **Partition B:** 299 passed / 4053 deselected / 0 failed / 0 skipped.
- **Partition C:** 130 passed / 4222 deselected / 0 failed / 0 skipped.
- **Feature-branch migration head:** `20260916_patient_external_record_import`.
- **Migration chain:** `20260914_patient_search_identifiers → 20260916_clinical_access_sessions → 20260916_patient_external_record_import`.
- **Approved production/pilot migration head:** `20260916_clinical_access_sessions`; `scripts/run_pilot_migrations.py` remains pinned there.
- **Observed `main` after Phase-A run:** `385e7d7142c7d0c5c8aefd4322605ecaec4fc948` (`security(consent): start operation-bound treatment signature protocol`). This is one commit after the previously reconciled `238c59b7...` and changes only `app/services/signed_treatment_session_v1.py`, `docs/governance/SLICE_10B4_SIGNED_TREATMENT_SESSION_V1.md`, and `tests/test_signed_treatment_session_v1_contract.py`. No Task-1 overlap; do not rebase merely for this move.
- **Current phase:** Phase B — patient-authority extraction.
- **Exact next step:** implement the smallest patient-specific extraction orchestrator around shared extraction/evidence/encryption primitives; do not use provider delegated-trust authority or provider candidate semantics.
- **Protected Slice-10B behavior:** do not alter `ClinicalAccessSession`, Signed Consent V3, treatment-session signature authority, provider treatment-consent authority, or provider delegated-trust semantics.
- **Open PR inventory at the Phase-A checkpoint:** only PR #47.
- **Unexpected leftover branches:** `ops/task1-exact-head-qualification-2` at the earlier reconciled Task-1 point and `tmp-inspect-fe57-patient-import` at `21b7d482...`. Do not use or repurpose them; available connector does not expose ref deletion.
- **Known unresolved product/security gaps:** malware scanning remains **NOT CURRENTLY VERIFIED / NOT IMPLEMENTED**; full decoder-level corruption validation remains unverified beyond MIME/signature and obvious truncation checks.

> The commit containing this handoff is documentation-only and therefore becomes a branch head after the frozen Phase-A SHA. `72ea0eb9...` remains the exact evidence-bearing Phase-A checkpoint. Phase B must receive its own final exact-head qualification after implementation.

## Current Scope

Authenticated patient self-import of external medical records: category selection, encrypted source retention, patient-authority extraction, later patient review/correction, typed finalization where semantics exist, timeline projection, safe source view, retries, audit/privacy/lifecycle behavior, and frontend integration in later Task-1 parts.

## Explicit Non-Scope for Phase B

- Redesign or broaden `ClinicalAccessSession`, Signed Consent V3, treatment-session signing, provider treatment consent, or provider record-write authority.
- Synthesize provider, hospital, tenant, or treatment authority for patient-self extraction.
- Turn OCR/AI output directly into canonical clinical truth or enable auto-commit.
- Implement the full patient review/correction UI, typed finalization, timeline publication, provider treatment consumption, encounter binding, onboarding redesign, Records redesign, retry/cancel UX, lifecycle/retention redesign, malware-scanning claims, or HMS/HIS integration.
- Create a new migration unless Phase-B persistence genuinely requires one.

## Product Contract

New patient: account → required onboarding → **Add previous medical records** → category → camera/photo/PDF where supported → encrypted upload → processing → patient-friendly review/correction → retained source → provenance-aware typed save where safe → record category + timeline → continue onboarding or **Skip for now**.

Existing patient: Records → **Add external record** → category → source upload → processing → review → save → categorized record + timeline.

Patient-facing categories remain Prescription, Lab report, Imaging / radiology report, Discharge summary, and Other medical record. Never expose provider-pipeline lanes, candidate IDs, workflow IDs, storage refs, object keys, consent internals, or extractor implementation details.

## Security Invariants

1. Patient ownership derives only from authenticated server-side patient context.
2. Caller-selected patient UUIDs, provider credentials, treatment consent, `ClinicalAccessSession`, Signed Consent V3, or synthetic provider/hospital identities cannot authorize patient-self extraction.
3. Patient source storage uses the patient-self authenticated-encryption namespace/AAD and no synthesized tenant/provider authority.
4. Every extraction action is bound to the authoritative patient external-record import and source document, with ownership checked before source read.
5. Revalidate canonical patient/erasure state before background source decryption or candidate persistence.
6. Verify retained source bytes against the authoritative stored hash before extraction; hash mismatch fails closed.
7. Extraction output is only a candidate: extraction result != patient-confirmed fact != clinician-confirmed fact != finalized typed clinical record.
8. Candidate clinical values and evidence text are encrypted under the patient security boundary; do not persist plaintext extracted medical values.
9. Preserve authentic source hash, extraction provider/version, field/evidence location, confidence supplied by the adapter, attempt time, and safe failure state.
10. Never fabricate confidence or promote evidence-less summary arrays into clinical candidates.
11. Source bytes, extracted values, storage refs/keys, encryption metadata, and unnecessary health values never enter URLs, logs, analytics, tracing labels, Redis keys, idempotency keys, object keys, or audit metadata.
12. Audit metadata is structural/value-free and uses canonical audit vocabulary where semantically correct.
13. Idempotency/concurrency must prevent conflicting or uncontrolled duplicate candidate sets.
14. Provider document processing/storage and provider delegated-trust workflows must remain non-regressed.
15. AI/OCR output cannot auto-commit; auto-commit remains disabled.

## Existing Components Reused / Intended Reuse

| Component | State | Task-1 use |
|---|---|---|
| `DocumentStorage` Local/S3 encrypted adapters | EXISTS / REUSED | Patient-self namespace/AAD and ownership-bound source read. |
| `PatientExternalRecordImport` | EXISTS / REUSED | Patient-owned extraction workflow anchor. |
| `PatientExternalRecordCandidate` | EXISTS / REUSE REQUIRED | Separate patient-self candidate persistence; do not overload provider candidate tables. |
| Extraction adapter/provider abstraction | EXISTS / AUDITED | Reuse mechanism only. |
| Authentic extraction field evidence | EXISTS / AUDITED | Candidate source/evidence boundary. |
| Patient KMS/DEK encryption primitives | EXISTS / AUDITED | Candidate-value/evidence encryption. |
| Audit outbox / audit context | EXISTS / REUSED | Structural, value-free transition evidence. |
| Provider delegated extraction orchestrator | WRONG_FLOW | Do not call or weaken; it binds provider/hospital/delegated authority. |
| `ClinicalAccessSession` / Signed Consent V3 / treatment signing | WRONG_FLOW | Protected provider/treatment authority; not required for patient-self extraction. |

## Architecture Decisions

### Patient-owned persistence and source namespace
Task-1 import/candidate rows bind directly to patient + source document and contain no provider/hospital/treatment-consent authority columns. Patient sources use patient-self encrypted storage and patient-bound AAD.

### Patient API authority
`/api/v2/patient/me/external-records` derives patient identity from authenticated server context; no caller-selected patient UUID is accepted. Responses are patient-safe and use `Cache-Control: private, no-store`.

### Audit vocabulary
Patient upload/source view reuse existing `DOCUMENT_UPLOADED` and `DOCUMENT_SOURCE_VIEWED` events with structural patient-self metadata. Phase B should likewise reuse canonical events when semantics fit; do not create duplicate vocabulary casually.

### Feature schema vs approved deployment schema
Task-1 repository/CI head is `20260916_patient_external_record_import`. CI disposable PostgreSQL migrated successfully through that head. Production/pilot runner remains pinned to the independently approved `20260916_clinical_access_sessions` head.

### Provider regression repair
The Slice-4 PostgreSQL+Redis trust-root integration fixture previously migrated its private disposable DB only to `20260916_clinical_access_sessions`; the governance CLI correctly derived the repository head and failed closed with `SCHEMA_REVISION_MISMATCH`. Commit `72ea0eb9...` changes only that fixture's `HEAD` constant to `20260916_patient_external_record_import`. The CLI/security guard is unchanged. This repaired Partition C without weakening provider security.

### Patient-authority extraction design
Safe reuse point: configured extraction adapter → authentic field evidence/provider-version metadata → patient-bound encryption → `PatientExternalRecordCandidate`. Do not use provider candidate tables or provider delegated-trust jobs. Identity fields are not promoted into review candidates unless an explicit later product contract authorizes them. Evidence-less summary arrays are not promoted into reviewable facts.

## Work Log

### Entry 1 — Baseline / isolation
- Original base: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Created isolated Task-1 branch and living handoff.

### Entry 2 — Architecture audit + draft PR
- Opened draft PR #47 and audited patient/provider authority, secure storage/extraction/evidence, typed records/timeline, auth/client paths.

### Entry 3 — Patient-owned persistence
- `cd8af0bce6e774a978ba2d9e728bb569ac11f694`: patient-owned import/candidate persistence and linear migration.

### Entry 4 — Encrypted patient source staging
- `21b7d4822688774f1d11d2ae0a77e0403b8b7584`: distinct patient-self encrypted storage namespace and staging service.

### Entry 5 — Strict patient self-service API
- `ae29e8759942c5ffda8dc45916609628207bc684`: strict upload/list/detail/source API, server-derived patient authority, semantic idempotency, no-store behavior, stable failures, and envelope validation.

### Entry 6 — Qualification repair / reconciliation
- Repair lineage reached `4f9ba427428d2001ce04c362a6c2d8c3e0bd66d4`, then reconciled with `main` `238c59b7fe94cc04213063f55750f3d727a062c7` at two-parent commit `acfcde41dc150bfb4c8442945c71798ad049ff46`.
- Earlier red CI was used only as diagnostic evidence; no historical PASS was reused.

### Entry 7 — Initial Phase-A attempt blocked
- `acfcde41...` Actions runs completed `action_required` before backend jobs existed. No PASS claimed.
- Documentation checkpoint `215fda4f16efb068d21782880b51b45f36e40e8e` caused normal CI execution to begin.

### Entry 8 — Evidence-backed Partition-C repair
- On `215fda4f...`, A and B passed; C failed only two Slice-4 trust-root CLI journeys because their private disposable DB was migrated to the predecessor schema while the CLI correctly required the repository head.
- `72ea0eb9082e64fc9bae07981af06ba34ad40a2e` changes exactly one test-file line: the Slice-4 fixture schema target now equals the Task-1 feature head. No production/security code changed.

### Entry 9 — Phase A exact qualification: PASS
- Exact Task-1 SHA: `72ea0eb9082e64fc9bae07981af06ba34ad40a2e`.
- Backend CI run: `35137558974`.
- Ruff/lint: PASS.
- Partition A: **3923 passed, 429 deselected, 0 failed, 0 skipped**.
- Partition B: **299 passed, 4053 deselected, 0 failed, 0 skipped**.
- Partition C: **130 passed, 4222 deselected, 0 failed, 0 skipped**.
- All three JUnit qualification assertions reported zero failures/errors/skips.
- PostgreSQL provisioning migrated linearly through `20260914_patient_search_identifiers → 20260916_clinical_access_sessions → 20260916_patient_external_record_import`.
- Production/pilot head remained `20260916_clinical_access_sessions`.
- During the run, `main` advanced by one isolated Slice-10B4 treatment-signature commit to `385e7d7142c7d0c5c8aefd4322605ecaec4fc948`; changed paths do not overlap Task-1, so no rebase is required before Phase B.

## Test / Qualification Matrix

| Area | State | Evidence |
|---|---|---|
| Ruff/lint on `72ea0eb9...` | PASS | Backend run `35137558974`. |
| Backend Partition A | PASS | 3923 passed / 429 deselected / 0 failed / 0 skipped. |
| Backend Partition B | PASS | 299 passed / 4053 deselected / 0 failed / 0 skipped. |
| Backend Partition C | PASS | 130 passed / 4222 deselected / 0 failed / 0 skipped. |
| Zero-skip assertions | PASS | A/B/C JUnit checks each report skipped=0. |
| Migration graph / single-head contracts | PASS | Included in green suite on exact SHA. |
| Disposable PostgreSQL to Task-1 feature head | PASS | B/C shared DB provisioning reached `20260916_patient_external_record_import`. |
| Production/pilot migration head unchanged | PASS | `scripts/run_pilot_migrations.py` remains `20260916_clinical_access_sessions`. |
| Patient-authority extraction focused tests | NOT YET RUN | Phase B starts after this checkpoint. |
| Malware scanning | NOT VERIFIED / NOT IMPLEMENTED | Do not claim otherwise. |
| Deep decoder-level corruption checks | NOT VERIFIED | Upload guard proves MIME/signature + obvious truncation only. |
| Frontend/mobile/native/E2E | NOT QUALIFIED FOR TASK-1 UX | Later Task-1 scope. |

## Open Risks / Blockers

1. No Phase-A backend blocker remains; Phase B may proceed.
2. `main` may continue evolving Slice 10B; compare overlap before each major Task-1 checkpoint and do not rebase merely because main moves.
3. Patient review/correction, typed finalization, timeline, retry/cancel, lifecycle, and frontend remain incomplete.
4. Typed Prescription/Imaging/Discharge semantics still require finalization audit; never relabel Medication as Prescription.
5. Malware scanning and decoder-level validity remain unresolved product/security gaps.
6. Leftover inspection/qualification branches remain because the available connector has no ref-deletion action; do not use them.

## Merge / Rebase Safety Notes

- Original Task-1 base: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Reconciliation checkpoint: `acfcde41dc150bfb4c8442945c71798ad049ff46` with parents `4f9ba427...` and `238c59b7...`.
- Frozen Phase-A qualification SHA: `72ea0eb9082e64fc9bae07981af06ba34ad40a2e`.
- Current observed main after Phase A: `385e7d7142c7d0c5c8aefd4322605ecaec4fc948`; its one new commit has no Task-1 path overlap.
- Do not merge PR #47 or mark it ready merely because GitHub says it is mergeable.
- Do not rebase merely because main advances; re-check semantic/path overlap first.
- Task-1 migration remains a single linear child of `20260916_clinical_access_sessions`; do not create a sibling/merge migration.
- Production/pilot runner remains pinned to `20260916_clinical_access_sessions` until independent release approval.

## Final Completion Checklist

- [x] Patient-owned import persistence implemented and Phase-A qualified.
- [x] Distinct encrypted patient-self source storage implemented and Phase-A qualified.
- [x] Strict patient upload/list/detail/source API implemented and Phase-A qualified.
- [x] Phase-A exact backend qualification green on `72ea0eb9...` with A/B/C zero skips.
- [x] Migration graph / feature-head PostgreSQL provisioning qualified.
- [x] Production/pilot head preserved independently.
- [ ] Patient-authority extraction/evidence orchestration.
- [ ] Focused adversarial extraction tests.
- [ ] Final full-backend A/B/C + zero-skip qualification on the extraction head.
- [ ] Explicit patient review/correction with original-value provenance.
- [ ] Safe typed-record finalization where canonical semantics exist.
- [ ] Timeline projection exactly once after canonical persistence.
- [ ] Retry/resume/cancel behavior.
- [ ] Merge/erasure/delete lifecycle qualification.
- [ ] Onboarding + Records frontend entry points and Skip for now.
- [ ] Full frontend/build/native/E2E qualification as applicable.
- [ ] Final exact SHA frozen, PR body current, draft PR integration-ready but not merged without release-owner instruction.
