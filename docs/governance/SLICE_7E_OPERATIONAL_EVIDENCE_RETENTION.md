# Slice 7E — Operational Evidence, Audit, and Retention Closure

Status: **SOFTWARE / GOVERNANCE CANDIDATE — HUMAN RETENTION APPROVAL PENDING**

Qualification base `main`: `5f9f328ff41cd7b960767890181cb9e31da52fee`

Branch: `slice-7e-operational-evidence`

## Objective

Make audit-integrity and operational qualification evidence reproducible while
keeping retention approval where it belongs: with named human security and
privacy/legal reviewers.

Core rule:

```text
AUDIT TOOL EXISTS != CANONICAL VERIFIER RECONCILED
ENGINEERING PROPOSAL != RETENTION APPROVAL
CI GUARDRAIL != HUMAN GOVERNANCE DECISION
```

## Canonical audit verifier reconciliation

The current audit ledger is partitioned by `chain_scope` and has durable
`audit_chain_heads` state. `scripts/verify_audit_partitions.py` is the canonical
operator verifier for that architecture. It:

- verifies each partition independently from exactly one `GENESIS` event;
- rejects duplicate record hashes, forks, cycles, disconnected components, and
  sequence-number discontinuities;
- recalculates record hashes using the row's audit protocol version;
- checks protocol-version and chain-scope binding for V2 events;
- compares the calculated partition tip with `audit_chain_heads`;
- marks a failing partition unhealthy unless run with `--dry-run`, causing the
  runtime append boundary to fail closed for that partition.

The historical `scripts/verify_audit_chain.py` assumed one global chain. That
assumption is stale for a healthy multi-partition ledger because multiple
partition genesis events can appear as global forks/orphans.

Slice 7E replaces the historical implementation at that path with a compatibility
wrapper into the canonical partition verifier. Older operator commands therefore
fail safely into current behavior instead of running stale integrity logic.

`scripts/run_integration_suite.sh` now invokes:

`python -m scripts.verify_audit_partitions --dry-run`

directly.

## Sanitized machine-readable evidence

`scripts/verify_audit_integrity_evidence.py` wraps the canonical verifier in
mandatory dry-run mode and emits schema:

`nexa-slice-7e-audit-integrity-evidence-v1`

The output contains only:

- PASS / FAIL / ERROR;
- all-partitions versus single-partition scope;
- failure count;
- value-free failure classifications;
- explicit assertions that raw audit payloads, hashes, and event IDs are absent.

Detailed operator-only reasons remain in the canonical verifier/logging path and
must not be copied into a public or broadly shared qualification manifest without
separate review.

The evidence wrapper never changes `audit_chain_heads.is_healthy`; operational
incident handling may intentionally run the canonical verifier without
`--dry-run` when an authorized operator wants a verified integrity failure to
quarantine a partition.

The evidence entry point is directly executable from the repository root. An
explicitly requested nonexistent partition fails closed as `PARTITION_NOT_FOUND`
instead of being accepted as an empty healthy partition. Malformed serialized
audit payloads are classified as `INVALID_PAYLOAD` integrity failures rather
than escaping the verifier through an unclassified JSON-decoding exception.

## Operational snapshot boundary

Repository CI can qualify verifier code and controlled tamper/fork/sequence
regressions. It cannot prove the integrity of a specific pilot or production
database snapshot without connecting to that authorized database.

A future operational evidence record must therefore bind:

- immutable application/repository version;
- environment classification;
- sanitized snapshot/evidence identifier;
- verifier schema/version;
- invocation mode (`dry-run` for evidence capture unless incident procedure
  explicitly requires quarantine mutation);
- PASS/FAIL result and value-free failure codes;
- operator/run timestamp held in the controlled evidence system.

No raw audit payload is required in the evidence manifest.

## Retention gate remains human-owned

`docs/governance/MILESTONE_6_PILOT_RETENTION_DECISION.md` remains:

**DRAFT — NOT APPROVED — NOT IN EFFECT**

The operational owner approved only a 14-day evidence-review window. Security
reviewer and privacy/legal reviewer remain unassigned/pending. Proposed 30-day,
7-day, and 7-day lifecycle values are proposals only.

Slice 7E does not change those approval states and does not select final
retention durations.

## Engineering non-regression guard

While the retention document remains `PENDING APPROVAL`, CI now verifies that:

- the document still says it is not approved/in effect;
- security and privacy/legal reviewers remain explicitly unassigned unless a
  real human governance update changes the decision;
- the explicit `DO NOT CONFIGURE THE S3 LIFECYCLE RULE` boundary remains;
- application/scripts/GitHub/infra source does not contain an S3 lifecycle
  implementation using the guarded lifecycle configuration APIs/structures.

This guard is intentionally fail-closed: if engineering later adds S3 lifecycle
configuration while the decision is still pending, CI fails. Once authorized
reviewers genuinely approve a policy, the governance artifact and guard must be
updated together with measured apply/read-back evidence.

Engineering tests cannot turn PENDING into APPROVED.

## Incident and evidence preservation

An audit-integrity failure is evidence, not a reason to rewrite or silently heal
the ledger. Existing partition-verifier behavior preserves that boundary by
marking unhealthy state rather than reconstructing the chain.

Retention/lifecycle automation also must not delete qualification or incident
artifacts under a merely proposed policy. Any future approved lifecycle rollout
must include read-back verification and an incident/evidence-preservation
exception procedure before being called operationally qualified.

## Exit gate for the software portion

The software/governance portion of 7E may merge only when the exact final head
has:

- Ruff success;
- Backend Partition A/B/C success with zero qualification skips;
- Frontend CI success where repository policy runs it;
- tests proving the legacy verifier is only a compatibility wrapper;
- tests proving the integration suite calls the canonical partition verifier;
- tests for sanitized failure classification;
- tests preserving the pending human-retention boundary and absence of S3
  lifecycle implementation;
- no unresolved review finding that weakens audit integrity or manufactures
  retention approval.

## Measured pre-attestation qualification evidence

The code head `acf4047e29c2749259f77c5283e31d8ddfe49bb5` was tested through the
pull-request synthetic merge `e5383da2102816f2ef568a137899b52bd80cc1d5` against
qualification base `main` `5f9f328ff41cd7b960767890181cb9e31da52fee`.

Backend CI #410 completed successfully:

- Ruff: PASS;
- Partition A: 3,647 executed, 0 failures, 0 errors, 0 skips;
- Partition B: 272 executed, 0 failures, 0 errors, 0 skips;
- Partition C: 124 executed, 0 failures, 0 errors, 0 skips.

Frontend CI #359 completed successfully after a targeted Android rerun:

- frontend tests / production build / workspace packages: PASS;
- iOS native generation, CocoaPods installation, and source compilation: PASS;
- Android native project generation and source compilation: PASS on rerun.

The first Android attempt failed before Nexa source compilation because Android
SDK tooling rejected the downloaded NDK `27.1.12297006` archive as corrupt
(`Archive is not a ZIP archive` / unknown archive). No application-source compile
error was reached. The same exact PR head was retried on a fresh hosted runner;
the NDK/setup stage then completed and Android native source compilation passed.
The workflow conclusion is therefore PASS without a code or dependency change
being made to mask an infrastructure artifact failure.

At the time this evidence was recorded, PR #24 had no submitted reviews and no
inline review comments. This evidence commit changes the governance attestation,
so it is **not itself merge evidence**. The resulting exact head must pass the
same required backend and frontend workflows before merge.

## Remaining non-software gates

After this software merges, these still remain outside CI authority:

1. a real authorized operational database snapshot verification result;
2. named security approval of retention;
3. named privacy/legal approval of retention;
4. final approved lifecycle durations;
5. actual S3 lifecycle application and read-back verification after approval.

Until those occur, retention remains **PENDING**.

## Explicit nonclaims

This slice does not claim legal advice, statutory retention compliance,
production audit certification, an approved S3 lifecycle policy, external FHIR
certification, Textract accuracy PASS, live HPR/HFR qualification, or physical
Slice 6I completion.
