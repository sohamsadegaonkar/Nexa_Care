"""Canonical Auto-Approval Decision Engine (Workstream 5, Days 9–11).

Single source of truth for whether a candidate AI extraction may be
auto-approved or must route to human steward review.  WS4's
``can_auto_approve()`` in ``app/services/pipeline_safety.py`` delegates
to ``should_auto_approve()`` here; no other module may implement
auto-approval logic.

Rules (Alpha v2.0.0-alpha)
--------------------------
+-----------------+---------------+-------------+------------+-------------------+
| Risk Level      | Confidence    | is_valid    | Conflict   | Auto-approve?     |
+=================+===============+=============+============+===================+
| CRITICAL_RISK   | Any           | Any         | Any        | **Never**         |
| HIGH_RISK       | Any           | Any         | Any        | **Never**         |
| MEDIUM_RISK     | >= 0.97       | True        | False      | Yes (alpha)       |
| MEDIUM_RISK     | < 0.97        | Any         | Any        | **Never**         |
| LOW_RISK        | >= 0.95       | True        | False      | Yes               |
| LOW_RISK        | < 0.95        | Any         | Any        | **Never**         |
| Any tier        | Any           | False       | Any        | **Never**         |
| Any tier        | Any           | Any         | True       | **Never**         |
| allergy/allergen| Any           | Any         | Any        | **Never** (forced |
|                 |               |             |            |  to HIGH_RISK)    |
+-----------------+---------------+-------------+------------+-------------------+
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.models.extracted_field import ExtractedField


class AutoApprovalDecision(BaseModel):
    """Structured result of the canonical auto-approval evaluation."""

    auto_approve: bool
    reason: str


# ── PII field names — values must be redacted in correction logs ────────────
_PII_FIELD_NAMES = frozenset({
    "patient_name", "phone", "aadhaar", "aadhaar_abha_id",
    "email", "dob", "nfc_uid", "bio_seed", "derived_alpha",
})

# ── Allergy field names — forced to HIGH_RISK, never auto-approved ──────────
_ALLERGY_FIELD_NAMES = frozenset({"allergy", "allergen"})

# ── Confidence thresholds per risk tier ─────────────────────────────────────
_THRESHOLDS: dict[str, float] = {
    "LOW_RISK": 0.95,
    "MEDIUM_RISK": 0.97,
    "HIGH_RISK": float("inf"),
    "CRITICAL_RISK": float("inf"),
}


def _extract_validation_diagnostics(
    field: ExtractedField,
) -> tuple[bool, bool, list[str]]:
    """Return ``(is_valid, has_conflict, validation_errors)`` from whatever
    shape ``validation_result`` takes (model, dict, or None).
    """
    val_res: Any = getattr(field, "validation_result", None)
    if val_res is None:
        return True, False, []

    if isinstance(val_res, dict):
        return (
            val_res.get("is_valid", True),
            val_res.get("has_conflict", False),
            val_res.get("validation_errors") or [],
        )

    # Pydantic ValidationResult model
    return (
        getattr(val_res, "is_valid", True),
        getattr(val_res, "has_conflict", False),
        getattr(val_res, "validation_errors", []) or [],
    )


def should_auto_approve(field: ExtractedField) -> AutoApprovalDecision:
    """Evaluate the canonical auto-approval decision for an extracted field.

    This is the **single source of truth**.  All callers (including WS4's
    ``can_auto_approve``) must delegate here.
    """
    fname = str(getattr(field, "field_name", "")).strip().lower()

    # ── 1. Allergy special-casing: forced to HIGH_RISK, always review ──────
    if fname in _ALLERGY_FIELD_NAMES:
        if hasattr(field, "risk_level"):
            field.risk_level = "HIGH_RISK"
        return AutoApprovalDecision(
            auto_approve=False,
            reason="Allergy fields are forced to HIGH_RISK and require review",
        )

    # ── 2. Confidence must exist and be a valid number in [0, 1] ───────────
    conf = getattr(field, "confidence", None)
    if conf is None or not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
        return AutoApprovalDecision(
            auto_approve=False,
            reason="Missing or invalid confidence score",
        )

    # ── 3. Top-level conflict flag ─────────────────────────────────────────
    if getattr(field, "has_conflict", False) is True:
        return AutoApprovalDecision(
            auto_approve=False,
            reason="Field has conflicting data",
        )

    # ── 4. Validation result diagnostics ───────────────────────────────────
    is_valid, vr_has_conflict, validation_errors = _extract_validation_diagnostics(field)

    if vr_has_conflict:
        return AutoApprovalDecision(
            auto_approve=False,
            reason="Validation result reports conflicting data",
        )

    if not is_valid:
        return AutoApprovalDecision(
            auto_approve=False,
            reason="Validation failed",
        )

    if validation_errors and isinstance(validation_errors, list) and len(validation_errors) > 0:
        return AutoApprovalDecision(
            auto_approve=False,
            reason=f"Validation errors: {', '.join(validation_errors[:3])}",
        )

    # ── 5. Risk-tier thresholds ────────────────────────────────────────────
    risk = str(getattr(field, "risk_level", "")).strip().upper()

    if risk in {"CRITICAL_RISK", "HIGH_RISK"}:
        return AutoApprovalDecision(
            auto_approve=False,
            reason=f"{risk} fields always require human review",
        )

    threshold = _THRESHOLDS.get(risk)
    if threshold is None:
        return AutoApprovalDecision(
            auto_approve=False,
            reason=f"Unknown risk tier '{risk}'",
        )

    if conf >= threshold:
        return AutoApprovalDecision(
            auto_approve=True,
            reason=f"{risk} confidence {conf:.2f} meets threshold {threshold:.2f}",
        )

    return AutoApprovalDecision(
        auto_approve=False,
        reason=f"{risk} confidence {conf:.2f} below threshold {threshold:.2f}",
    )
