# Slice 7 — Pilot Readiness, Interoperability, and Evidence Closure

Status: **SOFTWARE WORK THROUGH 7E MERGED — 7F EXTERNALLY BLOCKED / EXTERNAL-MANUAL GATES OPEN**

Closure baseline: `c811ba4abbb752a2ae7227d077d409e1d0738261`

Closure branch: `slice-7-closure-reconciliation`

## Goal

Slice 7 turns the internally qualified trust, consent, device, encryption, audit, and pipeline primitives into a current pilot-readiness program with explicit interoperability and evidence gates.

It does **not** convert external/manual blockers into PASS by assertion.

Core rule:

```text
IMPLEMENTED != INTERNALLY QUALIFIED != DEPLOYED != EXTERNALLY QUALIFIED != PHYSICALLY QUALIFIED
```

Each 7.x sub-slice states exactly which level it proves.

## Closure state at the current baseline

The repository has completed the internally executable software/evidence-harness work through 7E:

- **7A — MERGED / INTERNALLY QUALIFIED:** current authority-critical documentation and regression contracts were reconciled with Signed Consent V3, device lifecycle/recovery/rotation, FHIR export, pipeline authority, and Alembic head `20260909_device_trust_lifecycle`.
- **7B — SOFTWARE HARNESS MERGED / LIVE PILOT NOT_RUN:** the machine-checkable pilot-runtime evidence contract is qualified, but no live ECS/Fargate/KMS/S3 pilot runtime PASS is claimed.
- **7C — SOFTWARE/EVALUATOR MERGED / LIVE ACCURACY NOT QUALIFIED:** the evaluator now prevents provider reachability or nominal benchmark status from hiding extraction/identity failures. The committed synthetic replay remains truthfully unqualified.
- **7D — INTERNAL BASE-R4 CONTRACT MERGED / EXTERNAL VALIDATION NOT_RUN:** `nexa-fhir-r4-base-v1` is internally qualified. Official/full-validator, implementation-guide, partner-sandbox, certification, and production-exchange claims remain unrun.
- **7E — SOFTWARE/GOVERNANCE MERGED / OPERATIONAL SNAPSHOT AND RETENTION APPROVALS OPEN:** canonical partition-aware audit verification, sanitized evidence output, and retention non-regression guardrails are qualified. A real operational database snapshot and human retention approvals are not supplied by CI.
- **7F — EXTERNALLY BLOCKED:** authoritative ABDM/NHA HPR/HFR machine-contract material is still required before implementation/live qualification may proceed.

Parallel Slice 6I physical handset execution remains **BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**.

This closure state means the repository has exhausted the currently defined, internally executable Slice 7 software work. It does not mean all pilot-readiness gates have passed.

## Slice 7 sequencing and retained contracts

### 7A — Baseline and contract reconciliation

**Objective:** make current repository documentation and machine-readable contracts describe the software that actually exists after Slices 5–6.

Implemented/qualified scope includes:

- reconciliation of `docs/CURRENT-STATE.md`, `docs/API-CONTRACTS.md`, and `docs/API-CONTRACT-DEVIATIONS.md` with current authority;
- current migration/deployment references pinned to `20260909_device_trust_lifecycle`;
- historical Alpha documents explicitly prevented from overriding current runtime contracts;
- regression tests guarding Signed Consent V3, device authority paths, FHIR authorization/nonclaims, pipeline authority, and external/physical blockers.

Status: **MERGED / INTERNALLY QUALIFIED**.

### 7B — Pilot runtime and cloud-security qualification

**Objective:** qualify the current code in an immutable, synthetic-data pilot runtime rather than inferring deployment safety from local/CI tests.

The merged evidence contract requires a future live qualification to bind:

- exact backend image digest and frontend deployment identity;
- ECS/Fargate task-role credential path with no static AWS access keys;
- `ENCRYPTION_BACKEND=kms`, the intended KMS key, and encryption context;
- encrypted S3 document storage and public-access protections;
- dedicated PostgreSQL and TLS Redis/Upstash at the exact migration head;
- `/healthz` and `/health` readiness;
- audit-outbox health/backlog;
- trusted host/CORS/proxy boundaries;
- rollback plus session/consent invalidation;
- fail-closed Redis/KMS/S3/database dependency-outage scenarios.

Status: **SOFTWARE HARNESS MERGED / LIVE PILOT NOT_RUN**.

No real patient PHI is authorized by the harness merge. A future live PASS applies only to the exact immutable deployment represented by measured sanitized evidence.

### 7C — Document extraction accuracy qualification

**Objective:** close the gap between provider reachability and clinically useful extraction accuracy.

The merged evaluator preserves a fixed synthetic benchmark contract, separates provider failures from parser/classification/occurrence failures, prevents duplicate/inflated evidence matches, and independently gates the actual identity-decision outcomes.

The recorded authorized synthetic run reached AWS Textract for 15/15 documents without provider errors, but the committed replay remains unqualified. `benchmark_valid=false`, and the fail-closed identity decision rejects one true-match case whose OCR name differs from the bound synthetic identity.

Status: **SOFTWARE/EVALUATOR MERGED / LIVE ACCURACY NOT QUALIFIED**.

No benchmark PASS may be inferred from “15/15 provider calls succeeded,” and thresholds/identity authority must not be weakened to manufacture one.

### 7D — FHIR R4 conformance and interoperability

**Objective:** move from an internally tested export shape to a declared, validated interoperability contract.

The merged internal contract `nexa-fhir-r4-base-v1` declares FHIR `4.0.1` and the exact emitted base resource subset:

- `Condition`;
- `MedicationRequest`;
- `Observation`;
- `AllergyIntolerance`.

The export remains provider-trust and active-consent gated; audit failure and internal contract failure abort export. Internal validation covers declared identifiers/references, terminology, timestamps, Quantity/UCUM rules, resource constraints, empty/partial records, and malformed internal data within the local contract.

Status: **INTERNAL BASE-R4 CONTRACT MERGED / EXTERNAL VALIDATION NOT_RUN**.

Internal schema validation is not official/full FHIR validation, an implementation-guide PASS, ABDM profile compliance, partner-system interoperability, certification, or production exchange qualification.

### 7E — Operational evidence, audit, and retention closure

**Objective:** make operational evidence reproducible and remove stale governance ambiguity without allowing engineering code to self-approve legal/privacy decisions.

The merged software/governance work:

- makes `scripts/verify_audit_partitions.py` the canonical partition-aware operator verifier;
- retains `scripts/verify_audit_chain.py` only as a compatibility entry point into current behavior;
- provides sanitized `scripts/verify_audit_integrity_evidence.py` output without raw audit payloads, hashes, event IDs, or database exceptions;
- fails closed on controlled tamper/fork/cycle/disconnect/protocol/scope/sequence/head errors, malformed serialized payloads, and an explicitly requested nonexistent partition;
- preserves integrity failures as evidence instead of silently healing the ledger;
- keeps `docs/governance/MILESTONE_6_PILOT_RETENTION_DECISION.md` pending and prevents proposed retention values from silently becoming S3 lifecycle implementation.

Status: **SOFTWARE/GOVERNANCE MERGED / OPERATIONAL DATABASE SNAPSHOT NOT_RUN / RETENTION APPROVAL PENDING**.

Engineering may validate a lifecycle configuration **after** genuine human approval; it may not manufacture that approval.

### 7F — Official ABDM/NHA HPR/HFR machine-contract qualification

Status: **EXTERNALLY BLOCKED**.

The repository must not invent an official contract. Work may proceed only when authoritative NHA/ABDM material supplies the actual server-to-server endpoint/authentication/transport/error/rate-limit contract required for live qualification.

When available, qualification must include:

- exact source runtime configuration;
- auth/token lifecycle;
- TLS/host allow-list/SSRF controls;
- schema and disposition mapping;
- timeout/retry/backoff/rate limits;
- adverse/ambiguous source results;
- synthetic/non-production qualification identities unless explicitly authorized;
- current Provider Trust re-evaluation after imported observations;
- kill switch and fail-closed behavior.

Until then: no live production HPR/HFR source is registered or claimed.

## Parallel external/manual gate — Slice 6I

Slice 6I remains the physical handset boundary and is intentionally not redefined as a Slice 7 software test.

Status remains:

**BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**

When a genuine device is available, its seven physical scenarios can be run from a fresh branch based on the then-current reviewed `main`. A physical PASS must satisfy the existing machine-checkable evidence validator.

## Native NFC boundary

No qualified native NFC reader currently exists. If native NFC becomes a product requirement for a future pilot, it requires a separately approved, explicitly scoped implementation/qualification effort with actual iOS/Android reader behavior, opaque tag payloads, replay/expiry/session/provider/facility binding, and physical controller evidence.

The existing scanner abstraction is not a physical NFC implementation.

## Remaining evidence and authority gates

The following are still open after the 7E merge and cannot be closed by CI assertion:

1. Slice 6I physical handset execution — **BLOCKED BY PHYSICAL PLATFORM / NOT_RUN**.
2. Slice 7B live pilot runtime — **LIVE PILOT NOT_RUN**.
3. Slice 7C live synthetic extraction accuracy — **NOT QUALIFIED**.
4. Slice 7D external/full FHIR validation or partner interoperability — **NOT_RUN**.
5. Slice 7E authorized operational database snapshot integrity verification — **NOT_RUN**.
6. Named security and privacy/legal retention approval — **PENDING**; lifecycle apply/read-back evidence — **NOT_RUN**.
7. Slice 7F official ABDM/NHA HPR/HFR machine-contract qualification — **EXTERNALLY BLOCKED**.

## Merge discipline retained for future executable work

Any future measured qualification or newly approved sub-slice follows the established discipline:

1. branch from current verified `main`;
2. implement or execute only the explicitly scoped authority/evidence contract;
3. open a draft PR;
4. run exact-head CI and real PostgreSQL/Redis/cloud/interop/physical qualification where relevant;
5. fix failures without weakening authority;
6. record measured evidence and explicit nonclaims;
7. requalify the final evidence/documentation head;
8. merge with an expected-head guard;
9. verify `main`;
10. delete obsolete branches only after their work is proven merged.

## Next action after Slice 7 software closure

There is **no committed Slice 8 plan** at this closure baseline. This document does not infer or invent one.

The next legitimate task is whichever external/manual prerequisite becomes real first:

- execute Slice 6I on genuine supported handset hardware;
- execute Slice 7B against an authorized immutable synthetic-data cloud runtime;
- run a separately authorized Slice 7C live synthetic benchmark and validate its sanitized result;
- select an actual Slice 7D FHIR implementation guide/full validator/partner target and run it;
- capture Slice 7E evidence from an authorized operational database snapshot and obtain genuine security/privacy/legal retention approval before lifecycle rollout; or
- begin 7F only after authoritative NHA/ABDM server-to-server machine-contract material is available.

Until then, creating a new PASS, physical result, external conformance claim, human approval, official HPR/HFR contract, or Slice 8 scope by assertion would violate the Slice 7 evidence model.

## Measured closure qualification evidence

The pre-attestation closure head `02bb2c25b3d4acb8aae7f0ef442dc9fcd4a5c383` was tested through pull-request synthetic merge `2a45c9305503a55af5b160e0701b534681f97921` against qualification base `main` `c811ba4abbb752a2ae7227d077d409e1d0738261`.

Backend CI #413 completed successfully:

- Ruff: PASS;
- Partition A: 3,649 executed, 0 failures, 0 errors, 0 skips;
- Partition B: 272 executed, 0 failures, 0 errors, 0 skips;
- Partition C: 124 executed, 0 failures, 0 errors, 0 skips.

Frontend CI #362 completed successfully:

- frontend tests: PASS;
- Next production build: PASS;
- workspace package build: PASS;
- Android native project generation and source compilation: PASS;
- iOS native project generation, CocoaPods installation, and source compilation: PASS.

PR #25 had no inline review threads when this evidence was recorded. The measured run qualifies the closure documentation/regression shape that existed at `02bb2c25...`; it does **not** turn any external/manual gate above into PASS.

This attestation commit changes the branch head and is therefore **not itself merge evidence**. The exact resulting head must pass the same required backend and frontend workflows, including zero-skip backend qualification, before PR #25 is merge-eligible.
