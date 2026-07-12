# Clinical Medical Validation Rule Catalog (Workstream 5)

This authoritative specification defines the deterministic validation rules executed by Workstream 5 before candidate medical observations are scored or adjudicated. Each rule produces a `ValidationCheck` object recorded inside `ValidationResult.checks`.

---

## 1. Catalog of Automated Validation Rules

### 1.1 Blood Pressure Format Check (`bp_format_check`)
- **Applicability:** `field_name` in `{"bp", "blood_pressure"}`.
- **Rule Specification:** The extracted string must match the pattern `^\d{2,3}/\d{2,3}(\s*mmHg)?$`.
- **Failure Action:** Sets `is_valid = false`, appends error `"Blood pressure must follow NNN/NNN mmHg format"`.

### 1.2 Blood Sugar & Unit Verification (`sugar_unit_check`)
- **Applicability:** `field_name` in `{"sugar", "fasting_glucose", "random_glucose"}`.
- **Rule Specification:** The candidate value must parse as a positive numeric float attached to a recognized clinical unit (`mg/dL` or `mmol/L`).
- **Failure Action:** Sets `is_valid = false`, appends error `"Blood sugar requires numeric value and valid unit (mg/dL or mmol/L)"`.

### 1.3 Prescription Dosage Completeness (`dosage_completeness_check`)
- **Applicability:** `field_name` in `{"medication", "prescription", "dosage"}`.
- **Rule Specification:** Prescription candidate text must contain both a quantitative strength (`mg`, `g`, `ml`, `mcg`) and a dosing frequency (`daily`, `BID`, `TID`, `q8h`, `as directed`).
- **Failure Action:** Sets `is_valid = false`, appends error `"Prescription dosing must include both quantitative strength and administration frequency"`.

### 1.4 Temporal & Clinical Date Verification (`date_plausibility_check`)
- **Applicability:** Any extracted date field (`recorded_at`, `prescribed_at`, `dob`).
- **Rule Specification:** Extracted timestamps must represent valid calendar dates and cannot lie in the future (`timestamp > now() + 300s`) or prior to reasonable biological limits (`year < 1900`).
- **Failure Action:** Sets `is_valid = false`, appends error `"Temporal check failed: timestamp cannot be in the future or historically impossible"`.

### 1.5 Pharmaceutical Vocabulary Fuzzy-Match (`medication_formulary_check`)
- **Applicability:** `field_name` in `{"medication", "prescription", "drug"}`.
- **Rule Specification:** Extracted drug name must achieve $\ge 85\%$ fuzzy-string similarity against the established clinical formulary list (e.g., `Metformin`, `Lisinopril`, `Amoxicillin`, `Telmisartan`, `Atorvastatin`).
- **Failure Action:** Sets `is_valid = false`, appends error `"Medication name failed formulary verification match"`.

### 1.6 Diagnostic Laboratory Reference Interval Evaluation (`abnormal_lab_flag_check`)
- **Applicability:** Diagnostic laboratory evaluations with configured ranges (`sugar`, `fasting_glucose`, `hba1c`).
- **Rule Specification:** Evaluates quantitative value against established reference bounds (`reference_range.min` to `reference_range.max`). Generic labs (`lab_result`, `lab_value`, `cbc`, `lipid_panel`) require a numeric value and recognized unit but are marked `reference_range_known=false` when no configured range exists.
- **Failure Action:** If value falls outside configured bounds, sets `reference_range.is_abnormal = true` and escalates observation risk tier to `HIGH_RISK`. Unknown/generic ranges set `unknown_reference_range=true` and `requires_review=true`, which blocks auto-approval.

---

## 2. Validation Execution & Pipeline Integration

When `can_auto_approve(...)` evaluates an observation:
If any automated validation check fails (`ValidationResult.is_valid == false`, `ValidationResult.validation_errors` contains items, or `reference_range.requires_review == true`), the candidate field is strictly blocked from auto-approval and assigned `status="needs_review"`.
