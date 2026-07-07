# Clinical Field-to-Risk Mapping Catalog (Workstream 5)

This authoritative catalog defines the deterministic baseline clinical risk tier assignments mapped from extracted clinical entity categories (`field_name`).

---

## Canonical Field-to-Risk Mapping Table

| Extracted Field Name (`field_name`) | Baseline Clinical Category | Baseline Risk Tier | Condition / Override Rules | Final Assigned Risk Tier |
| :--- | :--- | :--- | :--- | :--- |
| `patient_name`, `dob`, `phone`, `abha_id` | Demographics & Identification | `LOW_RISK` | Standard demographic header extraction | `LOW_RISK` |
| `bp`, `systolic_bp`, `diastolic_bp` | Vital Signs (Blood Pressure) | `MEDIUM_RISK` | Standard observation within normal physiological range | `MEDIUM_RISK` |
| `heart_rate`, `pulse`, `resp_rate`, `temp`| Vital Signs | `MEDIUM_RISK` | Standard observation within normal physiological range | `MEDIUM_RISK` |
| `sugar`, `fasting_glucose`, `postprandial`| Diagnostic Lab / Vitals | `MEDIUM_RISK` | Value outside standard physiological range ($\ge 126$ mg/dL fasting) | `HIGH_RISK` |
| `hba1c` | Diagnostic Lab | `MEDIUM_RISK` | Value outside standard reference range ($\ge 6.5\%$) | `HIGH_RISK` |
| `lab_value`, `cbc`, `lipid_panel`, *general labs* | Diagnostic Laboratory | `MEDIUM_RISK` | Automated reference range check flags `is_abnormal == true` | `HIGH_RISK` |
| `medication`, `prescription`, `drug` | Active Prescriptions | `HIGH_RISK` | High clinical impact on patient safety and pharmacotherapy | `HIGH_RISK` |
| `dosage`, `strength`, `frequency` | Prescription Dosing | `HIGH_RISK` | Incorrect dosing creates severe adverse drug event risk | `HIGH_RISK` |
| `allergy`, `allergen`, `sensitivity` | Immunological Sensitivities | `HIGH_RISK` | **Strict Rule:** Forced to `HIGH_RISK` (or `CRITICAL_RISK` if anaphylaxis) | `HIGH_RISK` / `CRITICAL_RISK` |

---

## Enforcement Invariants
1. **Never Downgrade Allergies:** Any candidate extraction where `field_name` matches `allergy` or `allergen` is unconditionally assigned at least `HIGH_RISK`. Even if remote AI inference scores the observation as `LOW_RISK`, Workstream 5 safety logic overrides the tier to `HIGH_RISK`.
2. **Abnormal Lab Escalation:** Any diagnostic laboratory observation where `validation_result.is_abnormal == true` (or outside established biological reference limits) escalates immediately from `MEDIUM_RISK` to `HIGH_RISK`.
