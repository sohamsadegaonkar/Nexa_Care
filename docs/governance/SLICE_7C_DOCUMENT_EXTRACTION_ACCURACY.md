# Slice 7C — Document Extraction Accuracy Qualification

Status: **QUALIFICATION CANDIDATE — live accuracy PASS not claimed**

Baseline `main`: `03aa3bed8097dac989fe81ce34097d17da62f58d`

Branch: `slice-7c-extraction-qualification`

## Objective

Slice 7C closes qualification loopholes between provider reachability, field
extraction metrics, and the actual fail-closed patient-identity decision.

Core rule:

```text
PROVIDER REACHABLE != EXTRACTION QUALIFIED != IDENTITY DECISION QUALIFIED
```

A report is not a Slice 7C PASS unless all three relevant boundaries are proven
by the same sanitized benchmark result.

## Verified starting evidence

The committed 15-document corpus is synthetic and contains 53 expected field
occurrences.  The recorded authorized capture reached Amazon Textract for all
15/15 documents with zero provider failures.  The current sanitized replay
records:

- 95 authentic evidence records;
- 61 semantic candidates;
- 49/53 exact one-to-one matches;
- exact occurrence precision `0.8032786885245902`;
- exact occurrence recall `0.9245283018867925`;
- page accuracy `1.0`;
- source-text accuracy `0.9183673469387755`.

Those facts establish provider reachability and useful extraction diagnostics;
they do not establish an overall accuracy PASS.

## Identity qualification defect found in 7C

Before this slice, the benchmark exposed `identity_metrics` and
`identity_outcome_counts`, but its legacy top-level identity gate
`patient_identity_mismatch_detection` was calculated from raw bound-identity
equality rather than from the actual `IdentityDecisionState` outcome.

That distinction is security relevant.  The committed replay contains:

- `TRUE_MATCH_ACCEPTED = 13`;
- `TRUE_MATCH_REJECTED = 1`;
- `MISMATCH_REJECTED = 1`;
- `MISMATCH_ACCEPTED = 0`.

The rejected true-match is synthetic case 12.  Textract captured the displayed
name as `Synthetic Patient lota` while the bound synthetic identity is
`Synthetic Patient Iota`.  Nexa correctly fails closed on that discrepancy.
Slice 7C does **not** introduce fuzzy identity matching or reinterpret the OCR
output to manufacture a PASS.

## Slice 7C qualification contract

`scripts/validate_textract_accuracy_qualification.py` is the machine-checkable
Slice 7C qualification boundary.  It consumes only sanitized aggregate benchmark
JSON and performs no AWS calls.

A PASS requires:

1. the underlying extraction benchmark already reports `benchmark_valid=true`
   and `metrics_valid=true`;
2. every attempted synthetic document succeeds and provider error counts are
   zero;
3. the established extraction thresholds are independently rechecked, including
   exact precision/recall, raw accuracy, evidence support, normalization, units,
   repeated fields, table rows, source text, page evidence, bounding boxes and
   confidence provenance;
4. failure classification reconciliation is true;
5. actual identity-decision metrics show:
   - true-match acceptance rate `1.0`;
   - mismatch rejection rate `1.0`;
   - false-accept rate `0.0`;
   - false-reject rate `0.0`;
6. identity outcome counts reconcile to successful documents, contain at least
   one true-match and mismatch case, and contain zero `TRUE_MATCH_REJECTED` and
   zero `MISMATCH_ACCEPTED` outcomes.

The validator intentionally keeps the existing conservative field-extraction
thresholds stable.  It adds a stricter decision-policy gate rather than lowering
any threshold to fit the current replay.

## Regression evidence

`tests/ai_extraction/test_textract_slice_7c_qualification.py` proves that:

- a fully reconciled synthetic qualification report can pass;
- a legacy-style report cannot hide a rejected true match behind a nominal
  top-level benchmark PASS;
- accepting a mismatch is a hard qualification failure;
- extraction thresholds and failure-classification reconciliation are rechecked;
- the committed sanitized replay remains truthfully unqualified under the new
  Slice 7C contract.

## Current qualification boundary

The current committed replay is expected to fail Slice 7C because its actual
identity decision accepts 13/14 true-match cases and fails closed on one OCR
identity discrepancy.  This is safer than weakening identity authority.

A future accuracy PASS requires a separately authorized live synthetic Textract
run against the fixed corpus (or an explicitly reviewed corpus revision), a
sanitized report from that run, and a green Slice 7C validator result.  Provider
reachability alone is insufficient.

No live AWS call is authorized by this document or by offline replay tests.

## Exit gate

The software/evaluator portion of 7C is merge-qualified only when the exact final
head has:

- Backend CI A/B/C success with zero skipped qualification tests;
- Frontend CI success where repository policy requires it;
- the committed sanitized replay still classified according to measured facts;
- no unresolved review finding that weakens extraction or identity authority.

A **full Slice 7C accuracy PASS** additionally requires an authorized live
synthetic benchmark result satisfying the validator.  Until that happens, the
live accuracy status remains **NOT QUALIFIED**.

## Explicit nonclaims

This slice does not claim production medical accuracy, hospital readiness,
clinical certification, live HPR/HFR qualification, external FHIR conformance,
physical Slice 6I completion, or authorization to process real patient PHI in a
benchmark.
