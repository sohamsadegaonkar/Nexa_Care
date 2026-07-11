# Extraction Accuracy Report — Ground-Truth Evaluation

**Generated at:** 2026-07-07T21:23:43.601715+00:00

## Measured Engine Accuracy

| Component | Accuracy | Correct / Total | Status |
|---|---|---|---|
| Validation Engine | 100.0% | 46 / 46 | ✅ PASS |
| Risk Classifier | 100.0% | 15 / 15 | ✅ PASS |
| Auto-Approval Engine | 100.0% | 12 / 12 | ✅ PASS |
| Conflict Detector | 100.0% | 5 / 5 | ✅ PASS |
| Full Pipeline (E2E) | 100.0% | 7 / 7 | ✅ PASS |

## Per-Field-Type Accuracy

### Validation Engine

| Field Type | Accuracy | Status |
|---|---|---|
| allergy | 100.0% | ✅ PASS |
| blood_pressure | 100.0% | ✅ PASS |
| bp | 100.0% | ✅ PASS |
| date | 100.0% | ✅ PASS |
| dob | 100.0% | ✅ PASS |
| dosage | 100.0% | ✅ PASS |
| drug | 100.0% | ✅ PASS |
| fasting_glucose | 100.0% | ✅ PASS |
| frequency | 100.0% | ✅ PASS |
| hba1c | 100.0% | ✅ PASS |
| medication | 100.0% | ✅ PASS |
| prescription | 100.0% | ✅ PASS |
| strength | 100.0% | ✅ PASS |
| sugar | 100.0% | ✅ PASS |

### Risk Classifier

| Field Type | Accuracy | Status |
|---|---|---|
| allergen | 100.0% | ✅ PASS |
| allergy | 100.0% | ✅ PASS |
| bp | 100.0% | ✅ PASS |
| dob | 100.0% | ✅ PASS |
| hba1c | 100.0% | ✅ PASS |
| medication | 100.0% | ✅ PASS |
| patient_name | 100.0% | ✅ PASS |
| sugar | 100.0% | ✅ PASS |

### Full Pipeline (E2E)

| Field Type | Accuracy | Status |
|---|---|---|
| allergy | 100.0% | ✅ PASS |
| bp | 100.0% | ✅ PASS |
| hba1c | 100.0% | ✅ PASS |
| medication | 100.0% | ✅ PASS |
| sugar | 100.0% | ✅ PASS |

### Conflict Detector — Detailed Results

| Test Case | Result |
|---|---|
| no_conflict_sugar | ✅ PASS |
| conflict_sugar_discrepancy | ✅ PASS |
| no_conflict_bp_normalization | ✅ PASS |
| contraindication_penicillin_amoxicillin | ✅ PASS |
| no_false_cross_reactivity | ✅ PASS |

## Overall Engine Accuracy

**100.0%** (85/85) — ✅ MEDICAL READY

Medical-grade threshold: ≥ 97%

## Methodology

All metrics are **measured** by running the actual WS5 engine code
against a ground-truth test set — no synthetic or extrapolated numbers.

- **Validation accuracy**: `validate_field()` produces correct
  `is_valid` + `validation_errors` for known inputs.
- **Risk classification accuracy**: `classify_risk()` returns the
  correct risk tier for known field/value/validation combos.
- **Auto-approval accuracy**: `should_auto_approve()` makes the
  correct GO/NO-GO decision for known risk/confidence combos.
- **Conflict detection accuracy**: `detect_conflicts()` correctly
  flags or stays silent on known conflict/no-conflict batches.
- **Full pipeline accuracy**: `score_extracted_field()` →
  `should_auto_approve()` produces the correct final status.
