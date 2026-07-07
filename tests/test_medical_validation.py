"""Test suite for Workstream 5 Medical Validation & Conflict Detection Engine.

Verifies:
1. BP valid format passes; invalid format sets is_valid=False.
2. Dosage with strength and frequency passes; missing frequency fails with "frequency missing".
3. Future date timestamps are rejected.
4. Medication name fuzzy matching passes known formulary names and flags low matches.
5. Abnormal lab values are identified and flag reference_range.is_abnormal=True.
6. Intra-job value discrepancies (e.g. conflicting blood sugar readings) create Conflict objects and mark has_conflict=True.
7. Contradictory clinical data (e.g. allergy vs active prescription) creates CONTRAINDICATION conflicts.
"""

from __future__ import annotations

from app.ai.conflict_detector import detect_conflicts
from app.ai.medical_validator import validate_field
from app.models.extracted_field import ExtractedField


def test_bp_validation():
    """Test 1: Blood pressure format validation."""
    valid_res = validate_field("bp", "120/80 mmHg")
    assert valid_res.is_valid is True

    invalid_res = validate_field("bp", "high_bp")
    assert invalid_res.is_valid is False
    assert any("Invalid blood pressure format" in e for e in invalid_res.validation_errors)


def test_dosage_frequency_validation():
    """Test 2: Prescription dosage completeness verification."""
    valid_res = validate_field("medication", "Metformin 500mg twice daily")
    assert valid_res.is_valid is True

    missing_freq_res = validate_field("medication", "Metformin 500mg")
    assert missing_freq_res.is_valid is False
    assert "frequency missing" in missing_freq_res.validation_errors


def test_future_date_rejected():
    """Test 3: Temporal date plausibility checks reject future timestamps."""
    future_dt = "2099-01-01T12:00:00Z"
    res = validate_field("recorded_at", future_dt)
    assert res.is_valid is False
    assert any("future" in e for e in res.validation_errors)


def test_medication_fuzzy_match():
    """Test 4: Pharmaceutical formulary fuzzy matching."""
    matched_res = validate_field("medication", "Metformin 500mg twice daily")
    assert matched_res.is_valid is True
    assert any("Matched known medicine" in c["message"] for c in matched_res.checks)

    unknown_res = validate_field("medication", "Xyzalfoobar 10mg daily")
    assert unknown_res.is_valid is False
    assert any("fuzzy match low" in e for e in unknown_res.validation_errors)


def test_abnormal_lab_flagged():
    """Test 5: Diagnostic lab reference evaluation flags abnormal values."""
    res = validate_field("hba1c", "7.8 %")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is True


def test_sugar_discrepancy_conflict_detection():
    """Test 6: Pairwise intra-job numeric evaluation identifies blood sugar discrepancies."""
    f1 = ExtractedField(field_id="s1", job_id="j1", field_name="sugar", raw_value="105 mg/dL", confidence=0.98, risk_level="LOW_RISK")
    f2 = ExtractedField(field_id="s2", job_id="j1", field_name="sugar", raw_value="280 mg/dL", confidence=0.98, risk_level="HIGH_RISK")

    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "VALUE_DISCREPANCY"
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_allergy_vs_medication_conflict_detection():
    """Test 7: Clinical contraindication evaluation identifies allergy vs active prescription conflicts."""
    f_alg = ExtractedField(field_id="a1", job_id="j1", field_name="allergy", raw_value="Penicillin", confidence=0.99, risk_level="HIGH_RISK")
    f_med = ExtractedField(field_id="m1", job_id="j1", field_name="medication", raw_value="Amoxicillin 500mg twice daily", confidence=0.95, risk_level="HIGH_RISK")

    conflicts = detect_conflicts([f_alg, f_med])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "CONTRAINDICATION"
    assert f_alg.has_conflict is True
    assert f_med.has_conflict is True
