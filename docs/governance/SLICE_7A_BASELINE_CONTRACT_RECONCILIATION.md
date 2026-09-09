# Slice 7A — Baseline and Contract Reconciliation

Status: **QUALIFIED CANDIDATE — final evidence-bearing head requalification pending**

Baseline `main`: `aa091e14cf38124ca81e32438b49bdad4d79be8b`

Branch: `slice-7-reconciliation`

Pre-attestation reconciliation head: `2ff82a4acd34994be6e95c032b4f46324cdd6d01`

Qualified candidate head: `3ec91b835d5c595a2838d6a801169bd5d09a57da`

## Objective

Slice 7A reconciles authority-critical documentation and regression contracts with the software that actually exists after Provider Trust and Slice 6. It does not add new runtime authority.

The reconciliation preserves the program-level separation:

```text
IMPLEMENTED != INTERNALLY QUALIFIED != DEPLOYED != EXTERNALLY QUALIFIED != PHYSICALLY QUALIFIED
```

## Reconciled current contracts

The current repository contract now records:

- Signed Consent V3 under `/api/v2/consent/v3/*`, including exact patient/device/key-version/context binding and explicit V2 authority retirement;
- bootstrap device enrollment, trusted-device enrollment, lost-device recovery, key rotation, revocation, and device inventory as separate patient authority operations;
- native alias-based mobile signing as the current routine signing path, without converting source/compile evidence into physical StrongBox/Secure Enclave proof;
- provider-trust and active-consent-gated FHIR export with audit failure as a fail-closed boundary;
- server-authoritative pipeline patient ownership and current auto-approval safety invariants;
- Alembic head `20260909_device_trust_lifecycle` as the current repository/pilot migration head.

Historical Alpha architecture remains preserved as history but now explicitly yields to `docs/CURRENT-STATE.md` and `docs/API-CONTRACTS.md` for current authority-critical behavior.

## Migration-head reconciliation

The executable migration tool pins:

`20260909_device_trust_lifecycle`

Slice 7A reconciles current-head references in:

- `docs/pilot-security-operations.md`;
- `docs/runbooks/MILESTONE_6_FARGATE_DEPLOYMENT.md`;
- `docs/governance/NEXA_CARE_ENGINEERING_CONSTITUTION.md`;
- the current migration-safety entry in `docs/governance/SECURITY_NON_REGRESSION.md`;
- deployment/documentation regression tests.

Historical statements that correctly describe an earlier phase's then-current head are retained as historical evidence rather than rewritten.

## API deviation report boundary

`docs/API-CONTRACT-DEVIATIONS.md` is treated as a historical July Alpha deviation report, not as the current defect list. Previously resolved security findings such as HIGH/CRITICAL auto-approval enforcement, low-confidence review routing, audit-chain verification, controlled break-glass reasons, and server-authoritative pipeline patient ownership are no longer presented as open current defects.

## Regression guards

`tests/test_current_authority_contract_docs.py` protects against:

- accidental reversion of the canonical consent contract to Signed Consent V2;
- omission of current device enrollment/recovery/rotation/revocation authority paths;
- loss of native-key custody/nonclaim boundaries;
- incorrect migration-head documentation;
- stale Alpha security findings being reasserted as current defects;
- accidental relabeling of physical/external blockers as completed;
- the frozen Alpha architecture being mistaken for the current authority contract.

`tests/test_pilot_deployment_hardening.py` derives current migration assertions from `scripts/run_pilot_migrations.py` instead of pinning a superseded migration literal.

## Qualification history

At implementation head `9a2a8b7092f062ca4e1e3ddcc886b21a38dab89e`:

- Backend CI #371, run `34334655088`:
  - Ruff passed;
  - PostgreSQL Qualification (Partition B) passed with zero-skip assertion;
  - PostgreSQL + Redis Qualification (Partition C) passed with zero-skip assertion;
  - Partition A failed only on three reconciliation assertions: two wording-sensitive new assertions and one pre-existing stale migration-head assertion.
- Frontend CI #320, run `34334655157`: **SUCCESS**.

The three Partition-A findings were corrected without changing runtime authorization semantics. The resulting reconciliation commit `2ff82a4acd34994be6e95c032b4f46324cdd6d01` was produced by an exact-parent, exact-string, self-removing one-use workflow. Its PR-triggered workflows were marked `action_required` because the commit author was `github-actions[bot]`; no CI jobs ran on that bot-authored commit.

## Qualified candidate evidence

Exact candidate head:

`3ec91b835d5c595a2838d6a801169bd5d09a57da`

### Backend CI

Backend CI #375, run `34344731792`: **SUCCESS**.

- Ruff: **SUCCESS** — `All checks passed!`.
- Partition A — Quality & Pure Unit:
  - **3609 passed**, 396 deselected;
  - JUnit total 3609, failures `0`, errors `0`, skipped `0`;
  - zero-skip qualification assertion: **SUCCESS**.
- Partition B — PostgreSQL Qualification:
  - **272 passed**, 3733 deselected;
  - JUnit total 272, failures `0`, errors `0`, skipped `0`;
  - zero-skip qualification assertion: **SUCCESS**;
  - disposable PostgreSQL migrated through `20260909_device_trust_lifecycle`.
- Partition C — PostgreSQL + Redis Qualification:
  - **124 passed**, 3881 deselected;
  - JUnit total 124, failures `0`, errors `0`, skipped `0`;
  - zero-skip qualification assertion: **SUCCESS**;
  - disposable PostgreSQL migrated through `20260909_device_trust_lifecycle` and the real CI Redis service was exercised.

### Frontend CI

Frontend CI #324, run `34344731780`: **SUCCESS**.

- frontend tests / Next production build / workspace package build: **SUCCESS**;
- iOS native project generation, CocoaPods install, and native-source compile: **SUCCESS**;
- Android native project generation and native-source compile: **SUCCESS**.

These native compile jobs are source/build evidence only. They are not physical StrongBox, Secure Enclave, biometric, NFC, or handset-execution evidence.

### PR review state at candidate qualification

PR #20 had:

- submitted reviews: **0**;
- inline review threads: **0**.

No authority-critical review finding was open at this checkpoint.

## Final exact-head gate

This evidence update moves the branch head. Therefore Slice 7A is not merge-qualified until the exact evidence-bearing documentation head again satisfies:

- Backend CI success;
- Ruff success;
- Partition A success with JUnit failures/errors/skips `0/0/0`;
- Partition B success with JUnit failures/errors/skips `0/0/0`;
- Partition C success with JUnit failures/errors/skips `0/0/0`;
- Frontend CI success, including configured native Android/iOS compile jobs;
- no unresolved PR review thread or submitted review requiring changes;
- `main` has not moved underneath the qualified PR base.

Only after those checks may PR #20 be marked ready and merged with an expected-head guard.

## Explicit nonclaims

Slice 7A does **not** claim:

- Slice 6I physical handset completion;
- physical StrongBox, Secure Enclave, biometric, or NFC execution;
- live official ABDM/NHA HPR/HFR machine-contract qualification;
- external FHIR certification or partner interoperability qualification;
- Textract extraction-accuracy qualification;
- a fresh production deployment qualification;
- security/privacy/legal approval of the pending pilot retention decision;
- that Nexa Care is fully secure.
