"""Integration interface for Workstream 4 orchestrator and WS5 intelligence scoring."""

from __future__ import annotations

from app.ai.confidence_scorer import score_field
from app.ai.medical_validator import validate_field
from app.ai.risk_classifier import classify_risk
from app.models.extracted_field import ExtractedField


def score_extracted_field(field: ExtractedField) -> ExtractedField:
    """Score confidence and classify risk for an extracted field (WS4 -> WS5 seam).

    Ensures every field receives non-null confidence and risk_level metadata.
    Validation always runs against the full ``raw_value`` because
    ``normalized_value`` may be a processed/partial representation (e.g.
    ``"500mg"`` or ``"Standard"``) that is missing drug-name or frequency
    context required by the medical validation engine.
    """
    val_res = field.validation_result
    if val_res is None or (isinstance(val_res, dict) and not val_res.get("checks")):
        val_res = validate_field(field.field_name, field.raw_value)
        if getattr(field, "has_conflict", False):
            val_res.has_conflict = True
        field.validation_result = val_res

    conf = score_field(
        field_name=field.field_name,
        raw_value=field.raw_value,
        extractor_confidence=field.confidence,
        context={"normalized_value": field.normalized_value},
    )
    risk = classify_risk(
        field_name=field.field_name,
        normalized_value=field.normalized_value or field.raw_value,
        validation_result=field.validation_result,
    )

    # Enforce allergy invariant strictly on the model
    if field.field_name.lower().strip() in {"allergy", "allergen"}:
        if risk not in {"HIGH_RISK", "CRITICAL_RISK"}:
            risk = "HIGH_RISK"

    field.confidence = conf
    field.risk_level = risk
    return field
