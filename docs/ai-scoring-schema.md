# AI Scoring Schema & Auto-Approval Rules (Workstream 5)

This authoritative specification defines the intelligence layer schema for confidence scoring, clinical risk tier classification, and automated medical validation enforced across Nexa Care V2.

---

## 1. ValidationResult Schema

Every candidate extraction evaluated by the Workstream 5 engine attaches a canonical `ValidationResult` object inside the `ExtractedField` payload:

```typescript
interface ValidationCheck {
  check_name: string;   // Identifier of validation rule executed (e.g., "bp_format_check")
  passed: boolean;      // True if check passed, false on rule failure
  message: string;      // Diagnostic explanation or reference bounds
}

interface ValidationResult {
  is_valid: boolean;           // Overall boolean validity (true only if all checks pass)
  has_conflict: bool;          // True if conflicting values detected within job or history
  checks: ValidationCheck[];   // Granular execution trace of all diagnostic checks run
  validation_errors: string[]; // Aggregated error strings if is_valid === false
  reference_range?: {          // Diagnostic laboratory biological reference interval
    min?: number;
    max?: number;
    unit: string;
    is_abnormal?: boolean | null;
    reference_range_known?: boolean;
    unknown_reference_range?: boolean;
    requires_review?: boolean;
  };
}
```

---

## 2. Clinical Risk Classification Tiers

The intelligence scoring engine assigns one of four clinical risk severity tiers (`risk_level`) to every extracted observation:

| Risk Tier | Clinical Definition & Scope | Default Workflow Routing |
| :--- | :--- | :--- |
| `LOW_RISK` | Low-consequence demographics or routine physiological observations (`patient_name`, standard vitals within normal range). | Eligible for auto-approval if confidence $\ge 0.95$. |
| `MEDIUM_RISK` | Diagnostic observations requiring clinical awareness (`bp`, `heart_rate`, standard lab values within normal range). | Eligible for auto-approval if confidence $\ge 0.97$ (Alpha) / Review Queue (Pilot). |
| `HIGH_RISK` | Active pharmaceutical prescriptions, abnormal diagnostic laboratory evaluations, or immunological sensitivities (`allergy`). | **Strictly Review-Only:** Never auto-approved. |
| `CRITICAL_RISK`| Life-threatening physiological values or severe drug-allergy contraindications requiring immediate alert. | **Strictly Review-Only:** Never auto-approved. |

---

## 3. Authoritative Auto-Approval Matrix (`can_auto_approve`)

Workstream 4's pipeline orchestrator strictly enforces the following adjudication matrix via `app.services.pipeline_safety.can_auto_approve(...)`. No alternate or un-audited auto-approval paths exist.

| Field Risk Level | Confidence Threshold | Validation (`is_valid`) | Conflicting Data (`has_conflict`) | Allergy Special-Case | Final Routing Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `LOW_RISK` | $\ge 0.95$ | `true` | `false` | `false` | `auto_approved` |
| `LOW_RISK` | $< 0.95$ | Any | Any | Any | `needs_review` |
| `MEDIUM_RISK` | $\ge 0.97$ | `true` | `false` | `false` | `auto_approved` (Alpha) / `needs_review` (Pilot) |
| `MEDIUM_RISK` | $< 0.97$ | Any | Any | Any | `needs_review` |
| `HIGH_RISK` | Any ($0.0 - 1.0$) | Any | Any | Any | `needs_review` |
| `CRITICAL_RISK`| Any ($0.0 - 1.0$) | Any | Any | Any | `needs_review` |
| **Any Tier** | Any ($0.0 - 1.0$) | `false` | Any | Any | `needs_review` |
| **Any Tier** | Any ($0.0 - 1.0$) | `requires_review=true` | Any | Any | `needs_review` |
| **Any Tier** | Any ($0.0 - 1.0$) | Any | `true` | Any | `needs_review` |
| **Any Tier** | Any ($0.0 - 1.0$) | Any | Any | `true` (`field_name == "allergy"`) | Forced to `HIGH_RISK` $\rightarrow$ `needs_review` |

---

## 4. Hospital Governance Policy & Pilot Roadmap

To maintain strict clinical safety while enabling institutional flexibility at deployment scale, the auto-approval engine separates immediate alpha behavior from policy-driven hospital pilot configurations:

### Alpha vs. Pilot Medium-Risk Adjudication
- **Alpha Behavior (`v2.0.0-alpha`):**
  Some medium-risk fields (`MEDIUM_RISK`) may qualify for auto-approval only when `confidence >= 0.97`, validation passes (`is_valid == true`), no review-required diagnostic is present, source evidence exists (`source_page` / `source_bbox`), and zero clinical conflict is detected.
- **Pilot Default (`v2.1.0-pilot`):**
  Medium-risk fields route strictly to human review by default unless the healthcare organization explicitly enables a medium-risk auto-approval governance policy.

### Future Enhancement: Policy-Driven Adjudication (`HospitalPolicy`)
Before entering clinical pilot deployment, `can_auto_approve(...)` will dynamically ingest institutional governance parameters from a persistent `HospitalPolicy` configuration table rather than relying solely on hardcoded thresholds:

```typescript
interface HospitalPolicy {
  hospital_id: string;                     // UUIDv4 facility identifier
  allow_medium_risk_auto_approval: boolean;// Default false (review-first by default)
  low_risk_threshold: number;              // Default 0.95
  medium_risk_threshold: number;           // Default 0.97
  require_bbox: boolean;                   // Default true
  require_source_page: boolean;            // Default true
}
```
