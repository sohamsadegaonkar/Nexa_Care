"""Test suite for Workstream 5 AI Intelligence Scoring Engine.

Verifies:
1. Confidence scoring boosts well-formed values (e.g. NNN/NNN mmHg).
2. Confidence scoring penalizes unparseable or malformed values.
3. Base risk classification maps demographic, vital, and medication categories accurately.
4. Abnormal diagnostic laboratory values escalate from MEDIUM_RISK to HIGH_RISK.
5. Immunological allergy sensitivities are unconditionally assigned at least HIGH_RISK.
"""

from __future__ import annotations

from app.ai.confidence_scorer import score_field
from app.ai.risk_classifier import classify_risk
from app.ai.scoring_engine import score_extracted_field
from app.models.extracted_field import ExtractedField


def test_confidence_scoring_well_formed_value():
    """Test 1: Well-formed blood pressure format boosts extraction confidence."""
    conf = score_field("bp", "120/80 mmHg", extractor_confidence=0.94)
    assert conf == 0.98


def test_confidence_scoring_malformed_value():
    """Test 2: Malformed or unparseable blood pressure format heavily penalizes confidence."""
    conf = score_field("bp", "high bp", extractor_confidence=0.94)
    assert conf == 0.69


def test_risk_classification_per_field_type():
    """Test 3: Risk classification maps baseline clinical categories to expected risk tiers."""
    assert classify_risk("patient_name", "John Doe") == "LOW_RISK"
    assert classify_risk("bp", "120/80") == "MEDIUM_RISK"
    assert classify_risk("medication", "Lisinopril 10mg daily") == "HIGH_RISK"


def test_abnormal_lab_escalation():
    """Test 4: Abnormal diagnostic lab observation escalates from MEDIUM_RISK to HIGH_RISK."""
    val_res = {
        "is_valid": True,
        "reference_range": {"min": 4.0, "max": 5.6, "unit": "%", "is_abnormal": True},
    }
    risk = classify_risk("hba1c", "7.8%", val_res)
    assert risk == "HIGH_RISK"


def test_allergy_always_high():
    """Test 5: Allergy field is strictly assigned at least HIGH_RISK."""
    field = ExtractedField(
        field_id="f-alg",
        job_id="job-1",
        field_name="allergy",
        raw_value="Peanuts",
        confidence=0.99,
        risk_level="LOW_RISK",
    )
    scored = score_extracted_field(field)
    assert scored.risk_level == "HIGH_RISK"


def test_conflict_escalates_risk():
    """Test 6: Conflicting data flag escalates risk tier (MEDIUM_RISK -> HIGH_RISK)."""
    val_res = {"is_valid": True, "has_conflict": True}
    risk = classify_risk("sugar", "140 mg/dL", val_res)
    assert risk == "HIGH_RISK"


def test_failed_validation_escalates_risk():
    """Test 7: Failed validation checks escalate risk tier (MEDIUM_RISK -> HIGH_RISK)."""
    val_res = {"is_valid": False, "validation_errors": ["Out of bounds"]}
    risk = classify_risk("sugar", "999 mg/dL", val_res)
    assert risk == "HIGH_RISK"


def test_unknown_reference_lab_escalates_to_high_risk():
    """Unknown lab reference ranges are review-required and escalate risk."""
    val_res = {
        "is_valid": True,
        "validation_errors": ["unknown reference range requires review"],
        "reference_range": {
            "unit": "mg/dL",
            "is_abnormal": None,
            "reference_range_known": False,
            "unknown_reference_range": True,
            "requires_review": True,
        },
    }
    risk = classify_risk("lab_result", "450 mg/dL", val_res)
    assert risk == "HIGH_RISK"


def test_score_extracted_field_marks_unknown_lab_high_risk():
    """End-to-end scoring keeps generic unknown labs out of LOW/MEDIUM risk."""
    field = ExtractedField(
        field_id="f-unknown-lab",
        job_id="job-1",
        field_name="lab_result",
        raw_value="450 mg/dL",
        confidence=0.99,
        risk_level="LOW_RISK",
    )
    scored = score_extracted_field(field)
    assert scored.risk_level == "HIGH_RISK"
    assert scored.validation_result is not None
    assert scored.validation_result.reference_range is not None  # type: ignore[union-attr]
    assert scored.validation_result.reference_range["unknown_reference_range"] is True  # type: ignore[union-attr]
