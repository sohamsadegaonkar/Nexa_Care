# Patient External Record Import — Living Handoff

## NEXT AGENT — START HERE

- **Current branch:** `slice-11a-patient-external-record-import`
- **Current HEAD:** branch created from `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`; resolve latest branch SHA before any follow-up write.
- **Base main SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Current implementation phase:** Step 1 repository/product/security audit; no feature implementation has started.
- **Last completed step:** Verified current remote baseline, branch inventory, open PR inventory, and current approved migration head; created isolated branch.
- **Exact next step:** Audit the existing upload/extraction/evidence/adjudication/typed-record/timeline/auth/frontend/lifecycle paths and classify each relevant component as `EXISTS`, `PARTIAL`, `WRONG_FLOW`, or `MISSING` before implementing.
- **Blockers:** None currently visible on GitHub. No open PRs and no remote branch other than `main` existed at branch creation. Continue to treat the bounded ClinicalAccessSession area as protected parallel-work scope.
- **Exact tests to run next:** audit-only; inspect existing focused tests and identify the smallest qualification set before the first code delta.
- **Files that must not currently be touched:** `app/services/clinical_access_session.py`, `app/security/clinical_access_policy.py`, `app/services/approved_access_capability.py`, `app/api/v2/consent_v3_routes.py`, `app/core/consent_gate.py`, `app/models/clinical_access_session.py`, clinical-access-session migrations/tests, and migration-head governance files unless an unavoidable dependency is proven.

## Current Scope

This workstream owns the authenticated **patient self-import** experience for previous/out-of-network medical records, including onboarding entry points and later Records entry points, patient-safe status/review UX, provenance-preserving save/finalization, category/timeline presentation, source traceability, authority isolation, upload hardening, audit/privacy behavior, lifecycle integration, and focused qualification.

## Explicit Non-Scope

- Redesigning or broadening `ClinicalAccessSession`.
- Reinterpreting provider treatment consent.
- Making patient import grant provider authority.
- Replacing the existing secure document-processing architecture with a direct OCR/LLM-to-record shortcut.
- Enabling extraction auto-commit.
- Redesigning global erasure architecture.
- Creating a competing Alembic head while another migration head is active.
- Turning provider-side PDF upload into the primary clinician workflow.

## Repository Baseline

- **origin/main SHA at branch creation:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Branch name:** `slice-11a-patient-external-record-import`
- **Worktree path:** N/A — branch is being operated through the authenticated GitHub connector rather than a local checkout because no repository checkout is mounted in the execution sandbox.
- **Remote branches visible at branch creation:** only `main`.
- **Open PRs visible at branch creation:** none.
- **Recent main activity:** Slice 10B bounded clinical-access-session work is already present on `main`; recent commits include durable session model/store/policy and migration-head updates. Treat its files as protected even though no separate remote branch remains.
- **Migration head at baseline:** `20260916_clinical_access_sessions` (from `scripts/run_pilot_migrations.py`).

## Product Contract

### New patient / onboarding

Create account → complete required onboarding → Add previous medical records → choose category → choose camera/photo/PDF where supported → upload → secure processing → review extracted information → correct/confirm where appropriate → preserve original source document → save provenance-aware structured result → show in correct record category → show in longitudinal timeline → continue onboarding or skip for now.

### Existing patient

Home / Records → Add external record → choose category → upload source → processing state → review extracted information → save → categorized record → timeline.

Initial categories:

- Prescription
- Lab report
- Imaging / radiology report
- Discharge summary
- Other medical record

Onboarding must offer **Skip for now** and importing documents must not be required to finish account setup.

## Security Invariants

1. Patient ownership is derived from the authenticated server-side patient session; a client-supplied patient UUID is never authority.
2. Patient self-import authority is distinct from provider treatment consent and `ClinicalAccessSession` authority.
3. A patient import cannot create provider access or treatment authority.
4. Provider consent/capability tokens cannot be repurposed as patient self-upload authority.
5. External source documents remain encrypted/traceable according to existing repository architecture.
6. Extraction candidates/evidence retain encryption and provenance guarantees already present in the pipeline.
7. OCR/AI output never silently becomes authoritative clinical truth.
8. `AUTO_COMMIT_ENABLED = False` and `AUTO_COMMIT_APPROVED = False` remain unchanged unless separately authorized by governance.
9. Patient-reviewed/corrected imported values remain patient-import provenance and are not relabeled clinician-created or clinician-verified.
10. Document content, extracted PII, raw bytes, object-storage keys, and sensitive identifiers must not leak into logs, URLs, analytics, or patient UI.
11. Security-sensitive transitions remain auditable using existing fail-closed governance semantics.
12. Duplicate/replay finalization must be idempotent or fail safely.
13. Patient merge/erasure/deletion handling must follow existing canonical lifecycle hooks rather than a parallel implementation.
14. Provider-authorized document-processing behavior must not regress.

## Existing Components Reused

Audit is in progress. Do not convert these placeholders into final classifications without repository evidence.

| Component | Classification | Evidence / notes |
|---|---|---|
| Patient-authenticated upload API | AUDIT PENDING | Determine whether a patient-owned route exists and what server-side identity binding it uses. |
| Provider-authorized upload API | AUDIT PENDING | Determine current consent/session gate and whether it is wrongly exposed as the only upload path. |
| Source document encrypted storage/archive | AUDIT PENDING | Inspect storage/encryption service and source retrieval behavior. |
| Extraction jobs/providers | AUDIT PENDING | Inspect job orchestration, retry, timeout, and failure states. |
| Field-level evidence | AUDIT PENDING | Inspect evidence persistence and source traceability. |
| Encrypted extraction candidate persistence | AUDIT PENDING | Verify encryption, patient binding, and lifecycle behavior. |
| Human review/adjudication | AUDIT PENDING | Identify existing boundary and whether UI is provider/engineering-oriented. |
| Typed clinical record commit | AUDIT PENDING | Map categories to existing typed models/services. |
| Timeline write | AUDIT PENDING | Verify provenance-capable timeline linkage. |
| Patient auth dependencies | AUDIT PENDING | Identify canonical dependency for patient self-owned routes. |
| Upload validation/idempotency | AUDIT PENDING | MIME/extension/size/empty/malformed/duplicate handling. |
| Audit vocabulary | AUDIT PENDING | Reuse semantically correct existing events where possible. |
| Erasure/merge lifecycle | AUDIT PENDING | Verify patient canonicalization and cryptographic erasure hooks. |
| Patient onboarding UI | AUDIT PENDING | Determine actual client architecture and registration flow. |
| Records/timeline UI | AUDIT PENDING | Determine patient-facing taxonomy and provenance presentation. |
| API client methods | AUDIT PENDING | Reuse repository client/auth state; no ad hoc fetch if prohibited. |

## Architecture Decisions

### 1. Isolate patient self-import authority from provider clinical authority

- **Decision:** Build/reuse a patient-owned import boundary that derives the patient from authenticated server context and never depends on provider treatment consent.
- **Reason:** Patient self-import is a B2C ownership action, not a provider treatment session.
- **Alternatives rejected:** Reusing `ClinicalAccessSession`, provider consent tokens, or a client-provided patient UUID.
- **Affected files:** To be finalized after audit; protected clinical-access-session files are excluded unless an unavoidable adapter dependency is proven.
- **Security consequence:** Prevents patient upload from granting provider authority and prevents cross-patient authority selection.

### 2. Reuse the existing secure document-processing primitives

- **Decision:** Preserve encrypted source storage, extraction, evidence, encrypted candidates, provenance, explicit review, typed record commit, timeline, and audit/outbox primitives wherever semantically compatible.
- **Reason:** Repository context states the provider-authorized document pipeline already has substantial qualified safety work.
- **Alternatives rejected:** Direct `upload → LLM/OCR → JSON → canonical record` implementation.
- **Affected files:** To be identified by audit.
- **Security consequence:** Keeps AI/OCR behind the existing human-review/provenance boundary.

### 3. Avoid a schema migration unless the audit proves one is necessary

- **Decision:** Prefer existing schema/state models. No patient-import migration will be created during audit.
- **Reason:** Baseline migration head is currently `20260916_clinical_access_sessions`; migration conflicts must be avoided.
- **Alternatives rejected:** Creating a speculative sibling migration before mapping existing persistence.
- **Affected files:** None yet.
- **Security consequence:** Avoids split migration heads and unqualified persistence changes.

## Work Log

### Entry 1

- **Timestamp:** 2026-09-16
- **Step:** Repository baseline and branch isolation.
- **Starting SHA:** `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`
- **Ending SHA:** branch creation initially points at starting SHA; this handoff creation commit will be recorded by the next metadata reconciliation step because a commit cannot contain its own not-yet-created SHA.
- **Files changed:** `docs/governance/PATIENT_EXTERNAL_RECORD_IMPORT_HANDOFF.md`
- **Behavior added/changed:** No product behavior. Created the mandatory living handoff and isolated task branch.
- **Security impact:** Positive process control only; no runtime security behavior changed.
- **Tests run:** None — documentation/baseline step only.
- **Results:** GitHub verified `main` SHA, only `main` branch existed, no open PRs, and migration head is `20260916_clinical_access_sessions`.
- **Known failures:** None.
- **Concurrent-branch overlap check:** No remote concurrent branch or open PR visible. Slice 10B changes are already on `main`; its files remain protected.
- **Next action:** Audit current document import architecture and classify every relevant component before feature implementation.

## Test / Qualification Matrix

| Area | State | Notes |
|---|---|---|
| Unit | NOT RUN | No runtime code changed yet. |
| Backend API | NOT RUN | Pending audit and first code delta. |
| PostgreSQL | NOT RUN | No schema/runtime change yet. |
| Redis | NOT RUN | Determine applicability during audit. |
| Privacy/security | NOT RUN | Focused adversarial set to be identified/added. |
| Mobile tests | NOT RUN | Client architecture audit pending. |
| Web tests | NOT RUN | Client architecture audit pending. |
| Next build | NOT RUN | Pending frontend changes. |
| Android compile | NOT RUN | Pending frontend/mobile changes. |
| iOS compile | NOT RUN | Pending frontend/mobile changes. |
| E2E | NOT RUN | Pending end-to-end implementation. |
| Vercel | NOT RUN | Required only if Vercel-deployed frontend changes. |

## Open Risks / Blockers

1. Remote branch/PR inventory is clean at baseline, but the prompt identifies bounded ClinicalAccessSession work as protected. Those files stay off-limits unless an unavoidable dependency is demonstrated.
2. The repository contains a newly advanced migration head (`20260916_clinical_access_sessions`); no patient-import migration should be created until persistence reuse is fully audited.
3. The exact patient-authentication dependency and patient-owned route conventions have not yet been mapped.
4. The current patient-facing frontend architecture and whether onboarding/records screens already exist are not yet classified.

## Merge / Rebase Safety Notes

- Base SHA: `2a103847e6c89efbf6a5c6b5e4e231e6c0a19eac`.
- Before every major implementation phase, re-read `main`, open PRs, and remote branches and compare changed files.
- Before final qualification, rebase only after inspecting semantic overlap. Never resolve conflicts with blind ours/theirs selection.
- Do not modify clinical-access-session migrations, migration-head governance, or protected session policy/service files unless explicitly recorded as an unavoidable dependency.

## Final Completion Checklist

- [ ] Authenticated patient can enter import from onboarding.
- [ ] Authenticated patient can enter import from Records.
- [ ] Category selection includes Prescription, Lab report, Imaging/radiology report, Discharge summary, Other medical record.
- [ ] Onboarding includes Skip for now and does not require document upload.
- [ ] Source upload is patient-owned, server-bound, validated, encrypted, and idempotent.
- [ ] Existing extraction/evidence pipeline is reused safely.
- [ ] AI/OCR cannot auto-commit authoritative clinical truth.
- [ ] Structured patient review/correction UX exists.
- [ ] Patient edits retain external-document + patient-review provenance.
- [ ] Final save writes appropriate typed record references.
- [ ] Timeline shows provenance-aware entry.
- [ ] Retained source can be viewed safely where policy allows.
- [ ] Retry/recovery behavior is patient-safe.
- [ ] Patient import does not grant provider authority or alter treatment consent.
- [ ] Provider document-processing regressions pass.
- [ ] Patient isolation/adversarial/privacy/audit tests pass.
- [ ] Lifecycle merge/erasure/delete behavior is qualified.
- [ ] Exactly one Alembic head exists if a migration is introduced.
- [ ] Complete frontend/backend qualification is green.
- [ ] Exact final SHA is recorded.
- [ ] Draft PR is ready for review and still draft until completion.
