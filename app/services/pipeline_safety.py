"""AI Ingestion Pipeline Safety & Auto-Approval Guardrails (Workstream 4 & 5).

Single authoritative decision point for whether candidate AI extractions can be
auto-approved or must route to human steward review queues.
"""

from __future__ import annotations

from typing import Any

from app.models.extracted_field import ExtractedField


def can_auto_approve(field: ExtractedField) -> bool:
    """Determine whether an extracted field qualifies for auto-approval per WS5 safety rules.

    Enforces (Alpha Rule v2.0.0-alpha):
    - CRITICAL_RISK -> never auto-approve.
    - HIGH_RISK -> never auto-approve.
    - MEDIUM_RISK -> may auto-approve only if confidence >= 0.97, validation is clean, and no conflict exists.
      (Pilot Rule v2.1.0-pilot: MEDIUM_RISK defaults to human review unless explicit hospital governance policy overrides).
    - LOW_RISK -> only if confidence >= 0.95.
    - Conflicting data flag -> never auto-approve.
    - Failed validation -> never auto-approve.
    - Allergy special-casing: field_name == "allergy" forced to HIGH_RISK -> always review.
    """
    # 1. Allergy special-casing: force HIGH_RISK -> always review
    fname = str(getattr(field, "field_name", "")).strip().lower()
    if fname in {"allergy", "allergen"}:
        if hasattr(field, "risk_level"):
            field.risk_level = "HIGH_RISK"
        return False

    # 2. Check confidence score existence and validity
    conf = getattr(field, "confidence", None)
    if conf is None or not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
        return False

    # 3. Check conflicting data flag on top-level model
    if getattr(field, "has_conflict", False) is True:
        return False

    # Check validation_result diagnostic object / dictionary
    val_res: Any = getattr(field, "validation_result", None)
    if val_res is not None:
        if isinstance(val_res, dict):
            if val_res.get("is_valid", True) is False:
                return False
            if val_res.get("has_conflict", False) is True:
                return False
            errs = val_res.get("validation_errors")
            if errs and isinstance(errs, list) and len(errs) > 0:
                return False
        elif hasattr(val_res, "is_valid"):
            if val_res.is_valid is False:
                return False
            if getattr(val_res, "has_conflict", False) is True:
                return False
            verrs = getattr(val_res, "validation_errors", None)
            if verrs and isinstance(verrs, list) and len(verrs) > 0:
                return False

    # 4. Check risk level rules against WS5 thresholds
    risk = str(getattr(field, "risk_level", "")).strip().upper()
    if risk in {"CRITICAL_RISK", "HIGH_RISK"}:
        return False
    elif risk == "MEDIUM_RISK":
        return conf >= 0.97
    elif risk == "LOW_RISK":
        return conf >= 0.95
    else:
        # Unknown risk tier -> never auto-approve
        return False
