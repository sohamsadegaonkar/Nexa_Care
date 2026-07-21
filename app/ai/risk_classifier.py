"""Risk Classifier for AI Ingestion Pipeline (Workstream 5).

Assigns deterministic clinical risk severity tiers (LOW_RISK, MEDIUM_RISK,
HIGH_RISK, CRITICAL_RISK) based on field category, abnormal diagnostic values,
immunological sensitivities, and validation/conflict escalation rules.
"""

from __future__ import annotations

from typing import Any


RISK_TIERS = ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"]


def _escalate_risk(current_risk: str, steps: int = 1) -> str:
    """Escalate a risk tier upwards by `steps` levels up to CRITICAL_RISK."""
    cur = current_risk.upper().strip()
    if cur not in RISK_TIERS:
        cur = "MEDIUM_RISK"
    idx = RISK_TIERS.index(cur)
    new_idx = min(len(RISK_TIERS) - 1, idx + steps)
    return RISK_TIERS[new_idx]


def classify_risk(
    field_name: str,
    normalized_value: str | None = None,
    validation_result: dict[str, Any] | Any | None = None,
) -> str:
    """Classify clinical risk level for an extracted observation."""
    fname = str(field_name).strip().lower()
    val = str(normalized_value or "").strip().lower()

    # 1. Base risk from field-to-risk catalog
    if fname in {"patient_name", "dob", "phone", "abha_id"}:
        base_risk = "LOW_RISK"
    elif fname in {
        "bp",
        "systolic_bp",
        "diastolic_bp",
        "heart_rate",
        "pulse",
        "resp_rate",
        "temp",
    }:
        base_risk = "MEDIUM_RISK"
    elif fname in {
        "sugar",
        "fasting_glucose",
        "postprandial",
        "hba1c",
        "lab_result",
        "lab_value",
        "cbc",
        "lipid_panel",
    }:
        base_risk = "MEDIUM_RISK"
    elif fname in {
        "medication",
        "prescription",
        "drug",
        "dosage",
        "strength",
        "frequency",
    }:
        base_risk = "HIGH_RISK"
    elif fname in {"allergy", "allergen", "sensitivity"}:
        base_risk = "HIGH_RISK"
    else:
        base_risk = "MEDIUM_RISK"

    # Check allergy special-casing (strict invariant: never below HIGH_RISK)
    if fname in {"allergy", "allergen", "sensitivity"}:
        if "anaphylax" in val or "shock" in val or "critical" in val:
            return "CRITICAL_RISK"
        return "HIGH_RISK"

    # Check validation_result diagnostic info
    is_valid = True
    has_conflict = False
    is_abnormal = False
    requires_review = False

    if validation_result is not None:
        if isinstance(validation_result, dict):
            is_valid = validation_result.get("is_valid", True)
            has_conflict = validation_result.get("has_conflict", False)
            requires_review = bool(validation_result.get("requires_review", False))
            ref = validation_result.get("reference_range")
            if isinstance(ref, dict):
                is_abnormal = bool(ref.get("is_abnormal", False))
                requires_review = requires_review or bool(
                    ref.get("requires_review")
                    or ref.get("unknown_reference_range")
                    or ref.get("reference_range_known") is False
                )
        else:
            is_valid = getattr(validation_result, "is_valid", True)
            has_conflict = getattr(validation_result, "has_conflict", False)
            requires_review = bool(getattr(validation_result, "requires_review", False))
            ref = getattr(validation_result, "reference_range", None)
            if isinstance(ref, dict):
                is_abnormal = bool(ref.get("is_abnormal", False))
                requires_review = requires_review or bool(
                    ref.get("requires_review")
                    or ref.get("unknown_reference_range")
                    or ref.get("reference_range_known") is False
                )
            elif ref is not None:
                is_abnormal = bool(getattr(ref, "is_abnormal", False))
                requires_review = requires_review or bool(
                    getattr(ref, "requires_review", False)
                    or getattr(ref, "unknown_reference_range", False)
                )

    # Check abnormal keywords
    if (
        "abnormal" in val
        or "critical" in val
        or ("high" in val and fname in {"hba1c", "sugar"})
    ):
        is_abnormal = True

    current_risk = base_risk

    # Escalate if abnormal lab value or unknown clinical context requires review.
    if is_abnormal and current_risk in {"LOW_RISK", "MEDIUM_RISK"}:
        current_risk = "HIGH_RISK"
        if "critical" in val:
            current_risk = "CRITICAL_RISK"

    if requires_review and current_risk in {"LOW_RISK", "MEDIUM_RISK"}:
        current_risk = "HIGH_RISK"

    # Escalate on validation failure
    if not is_valid:
        current_risk = _escalate_risk(current_risk, 1)

    # Escalate on conflicting data
    if has_conflict:
        current_risk = _escalate_risk(current_risk, 1)

    return current_risk
