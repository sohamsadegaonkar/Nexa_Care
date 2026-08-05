# Synthetic Textract benchmark corpus

This directory contains a deterministic, fully synthetic, single-page corpus
for the opt-in `scripts/run_textract_accuracy_benchmark.py` qualification
harness. Every rendered page is visibly marked `NOT REAL PATIENT DATA` and
uses synthetic people, facilities, clinicians, identifiers, and clinical text.

Local manifest and image validation is not OCR accuracy and is not evidence of
Amazon Textract performance. No accuracy percentage may be claimed until the
live benchmark is separately authorized and run against these synthetic files.

## Document inventory

| File | Format | Category | Layout |
|---|---|---|---|
| `01-simple-laboratory-form.png` | PNG | Simple laboratory form | Bordered form |
| `02-multiple-laboratory-rows.jpg` | JPEG | Multiple laboratory rows | Wide table |
| `03-repeated-glucose-table.png` | PNG | Repeated glucose measurements | Compact table |
| `04-hba1c-report.jpg` | JPEG | HbA1c report | Borderless form |
| `05-vital-sign-form.png` | PNG | Vital-sign form | Two-column-style summary |
| `06-blood-pressure-pulse-report.jpg` | JPEG | Blood pressure and pulse | Bordered form |
| `07-medication-table.png` | PNG | Multiple medicines | Wide table |
| `08-prescription-list.jpg` | JPEG | Prescription-style medication list | Borderless list |
| `09-diagnosis-form.png` | PNG | Diagnosis form | Bordered repeated labels |
| `10-mixed-clinical-summary.jpg` | JPEG | Mixed clinical summary | Two-column-style summary |
| `11-alternate-label-synonyms.png` | PNG | Supported alternate labels | Uppercase borderless form |
| `12-repeated-conflicting-values.jpg` | JPEG | Repeated conflicting values | Compact form |
| `13-incomplete-lab-row.png` | PNG | Missing unit/reference range | Incomplete table |
| `14-incomplete-medication-row.jpg` | JPEG | Missing frequency/duration | Incomplete table |
| `15-identity-mismatch.png` | PNG | Identity mismatch | Bordered form |

Inventory totals: 15 documents, 8 PNG, 7 JPEG, 0 PDF. PDF was intentionally
omitted because the project has no existing lightweight PDF/image-generation
dependency; PNG/JPEG coverage is sufficient for this milestone.

## Ground-truth inventory

The manifest contains 53 expected field occurrences:

| Canonical field | Occurrences |
|---|---:|
| `patient_name` | 15 |
| `aadhaar_abha_id` | 2 |
| `phone` | 1 |
| `hba1c` | 8 |
| `blood_glucose` | 7 |
| `blood_pressure` | 5 |
| `heart_rate` | 4 |
| `medication` | 8 |
| `diagnosis` | 3 |

There are 17 occurrences in seven repeated-field groups, representing ten
occurrences beyond the first value in each group. Conflicting values remain in
ground truth. Eleven expected fields are table rows: seven laboratory rows and
four medication rows, including one incomplete row of each kind.

All 15 documents carry a synthetic bound identity. Fourteen are identity-match
cases and one is a deliberate mismatch. The mismatch page contains only its
displayed mismatch identity; its different expected binding exists only in the
manifest and is never rendered.

## Reproduction and qualification boundary

`generate_documents.ps1` reproducibly renders the committed assets with
Windows' built-in drawing library and adds no project dependency. Running that
generator performs no network or AWS operation. The local validation test
checks the schema contract, paths, image containers, field support, synthetic
identity markers, repetition, and mismatch coverage. It does not invoke the
live benchmark harness or assess OCR quality.

The manifest gates are conservative initial qualification thresholds, not
production medical-accuracy claims. Higher-is-better metrics use typed
`minimum_gates`; lower-is-better `unexpected_provider_failure_rate` uses
`maximum_gates`. Provider/API failure is
not clinical `QUARANTINE` and the benchmark does not infer a clinical route.

## First live-run integrity incident

The first authorized live synthetic run was invalid: all 15 provider requests
failed, while the old zero-denominator fallback incorrectly displayed the
undefined accuracy metrics as `1.0` and returned success. No extraction-accuracy
claim resulted from that run.

The corrected harness reports attempted, successful, and failed document
counts; field occurrence counts; and aggregate stable provider error codes. It
never emits exception messages, document filenames, source values, or identity
values. Undefined metrics serialize as `null`, set `metrics_valid=false`, and
make `benchmark_valid=false`. Any unexpected provider failure also fails the
current corpus.

A subsequent authorized run successfully executed Textract for all 15/15
documents with no provider errors, but it was not an accuracy qualification
pass. It reported 53 expected occurrences, 97 provider evidence occurrences,
and an impossible 80 matches. Its multiset calculation found 49/53 exact raw
occurrences; the 97 count included Query/Form/Table provenance multiplicity,
while the 80 count reused expected occurrences and was inflated. Page accuracy
was 0, source-text accuracy was 0.275, and identity detection was
0.9333333333333333. Evaluation now groups only location/provenance-linked
evidence, preserves all support, and matches expected and semantic occurrences
one-to-one. No production accuracy claim may be made and a live rerun remains
pending separate authorization.

The corrected 15-document diagnostic then reported 97 evidence records, 63
semantic candidates, 34 duplicate-provenance records, 49 exact one-to-one
matches, 14 unmatched candidates, four unmatched expectations, and missing page
evidence on all 97 records. A sanitized document-15 inspection confirmed one
PAGE block and authentic PAGE ancestry but no numeric `Block.Page` anywhere.
Its deliberate identity mismatch was detected correctly; the remaining
identity error belongs to another synthetic case and remains unqualified.

The provider now passes its already validated `DocumentMetadata.Pages == 1`
context into the parser. Direct numeric page values still win. Otherwise a
target must reach exactly one PAGE ancestor through authentic CHILD, ANSWER or
VALUE relationships, and page 0 is used only when that ancestor is the graph's
single PAGE block. Callers without validated context, unrelated blocks, absent
or multiple PAGE blocks, ambiguous ancestry and malformed cycles remain
unknown. Case-level diagnostics expose only manifest ordinals, canonical field
names, source-type signatures and numeric reason counts. No accuracy claim may
be made; another live run requires separate authorization.

## Metric denominators

- Canonical presence recall uses distinct expected canonical types.
- Exact occurrence recall and exact raw accuracy use expected occurrences;
  exact occurrence precision uses semantic candidates.
- Evidence support uses semantic candidates; duplicate provenance uses all
  evidence records and counts support beyond the first record per candidate.
- Normalized value, unit, source text, page, bounding-box and confidence rates
  use one-to-one exact matches. Repeated recall uses expected occurrences whose
  canonical field repeats within a document.
- Table-row accuracy uses expected CELL occurrences and requires matched,
  structured CELL support with the complete expected row text.
- Identity detection uses successful documents. Provider rates use attempted
  documents. A zero denominator is `null`, never perfect, and invalidates the
  benchmark when the metric is required.
