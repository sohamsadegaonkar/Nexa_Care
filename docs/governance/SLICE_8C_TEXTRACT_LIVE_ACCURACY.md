# Slice 8C — Textract Live Extraction Accuracy

Status: **LIVE QUALIFICATION GATE IMPLEMENTED; BLOCKED BY PILOT AWS OIDC / LIVE BENCHMARK NOT_RUN**

## Purpose

Slice 8C makes the existing Slice 7C evaluator executable against live AWS
Textract using only the committed 15-document synthetic benchmark corpus. It
does not weaken identity binding, reinterpret OCR output, or replace the corpus
with easier examples to manufacture a PASS.

## Permanent live gate

`.github/workflows/textract-live-accuracy.yml` is a manually dispatched workflow
in the protected `pilot` environment. It requires an approved AWS OIDC role and
uses short-lived GitHub-issued identity rather than static AWS access keys.

A successful run must:

1. authenticate to AWS using OIDC;
2. execute all 15 committed synthetic documents against the live Textract
   provider;
3. prove `provider_mode=live_capture`;
4. prove `attempted_documents=15` and `live_provider_calls=15`;
5. satisfy the existing benchmark/evaluator gates without changing their
   thresholds or identity semantics;
6. capture the sanitized replay form; and
7. replay that capture with zero live provider calls and reproduce the live
   benchmark result including the identity-case decisions.

Provider reachability alone is not a PASS.

## Measured live attempt

The gate was actually attempted on isolated head
`53e28126f5939f6d1479a6e4477e5533f658da53`:

- workflow run: `34400385106`
- result: fail closed at `Require approved AWS OIDC role`
- observed blocker: `TEXTRACT_LIVE_QUALIFICATION=BLOCKED_MISSING_OIDC_ROLE`
- AWS credential configuration executed: **NO**
- AWS identity check executed: **NO**
- Textract provider calls executed: **0**
- benchmark result produced: **NO**

Therefore the current 8C live state is **BLOCKED BY AWS IDENTITY WIRING / NOT_RUN**,
not “benchmark failed”.

## Historical case-12 evidence remains valid

The prior authorized recorded Slice 7C capture reached Textract for all 15
synthetic documents but did not qualify. Synthetic case 12 displayed
`Synthetic Patient Iota`; the recorded OCR evidence contained
`Synthetic Patient lota`. Nexa rejected the true-match binding because the
observed identity was discrepant.

That corpus is intentionally preserved for longitudinal comparison. 8C does
not rename the patient, change the expected identity to the OCR error, add fuzzy
matching, or lower an identity threshold merely to improve the chance of PASS.
A fresh live run must show what the current provider actually returns.

## Completion condition

Live extraction accuracy becomes qualified only after protected pilot AWS OIDC
wiring exists and a live workflow run completes all provider and replay steps
with `TEXTRACT_LIVE_QUALIFICATION=PASS`.

## Nonclaims

This repository-side gate does not claim:

- current Textract accuracy PASS;
- any production or patient-data extraction run;
- AWS pilot deployment;
- that the historical case-12 discrepancy has disappeared.
