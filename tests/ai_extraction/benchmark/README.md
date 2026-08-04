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
cases and one is a deliberate mismatch. The mismatch binds `Synthetic Patient
Bound` while the page contains `Synthetic Patient Mismatch`.

## Reproduction and qualification boundary

`generate_documents.ps1` reproducibly renders the committed assets with
Windows' built-in drawing library and adds no project dependency. Running that
generator performs no network or AWS operation. The local validation test
checks the schema contract, paths, image containers, field support, synthetic
identity markers, repetition, and mismatch coverage. It does not invoke the
live benchmark harness or assess OCR quality.

The manifest gates are conservative initial qualification thresholds, not
production medical-accuracy claims. `fail_closed_quarantine_rate` deliberately
has no gate until a live synthetic qualification establishes a baseline.
