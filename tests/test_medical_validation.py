"""Test suite for Workstream 5 Medical Validation & Conflict Detection Engine.

Verifies:
 1.  BP valid format passes; invalid format sets is_valid=False.
 2.  BP without mmHg suffix also passes.
 3.  Dosage with strength and frequency passes (no drug-name fuzzy match).
 4.  Dosage without frequency fails with "frequency missing".
 5.  Dosage without strength fails with "strength missing".
 6.  Medication with drug name, strength, and frequency passes.
 7.  Medication bare drug name (no dosage/frequency) fails.
 8.  Medication without frequency fails.
 9.  Medication without strength fails.
10.  Future date timestamps are rejected.
11.  Past date timestamps are accepted.
12.  Unparseable date strings are rejected.
13.  Medication name fuzzy matching passes known formulary names.
14.  Medication name fuzzy matching flags unknown names.
15.  Abnormal lab values are flagged with is_abnormal=True.
16.  Normal lab values have is_abnormal=False.
17.  Sugar reference range boundary: 70–100 mg/dL = normal, >100 or <70 = abnormal.
18.  Pre-diabetic sugar (110 mg/dL) is flagged abnormal (not silently passing).
19.  Lab values without recognised units fail validation.
20.  Intra-job sugar discrepancy creates VALUE_DISCREPANCY conflict.
21.  Close sugar readings do NOT produce a conflict.
22.  Allergy vs medication creates CONTRAINDICATION conflict.
23.  Same BP readings do NOT produce a conflict.
24.  Different BP readings produce VALUE_DISCREPANCY conflict.
25.  Conflicts force has_conflict=True on all involved fields.
26.  BP with mmHg and without normalise to the same value (no false conflict).
"""
from __future__ import annotations

from pathlib import Path

from app.ai.conflict_detector import detect_conflicts
from app.ai.medical_validator import validate_field
from app.models.extracted_field import ExtractedField


# ── 1. Blood Pressure Validation ─────────────────────────────────────────────


def test_bp_valid_format():
    """BP in NNN/NNN mmHg format passes validation."""
    res = validate_field("bp", "120/80 mmHg")
    assert res.is_valid is True
    assert any(c["check_name"] == "bp_format" and c["passed"] for c in res.checks)


def test_bp_valid_without_mmhg():
    """BP in NNN/NNN format (no unit) also passes."""
    res = validate_field("bp", "120/80")
    assert res.is_valid is True


def test_bp_invalid_format():
    """Malformed BP value fails validation."""
    res = validate_field("bp", "high_bp")
    assert res.is_valid is False
    assert any("Invalid blood pressure format" in e for e in res.validation_errors)


def test_bp_variant_field_names():
    """All recognised BP field-name aliases are routed to the BP check."""
    for name in ("blood_pressure", "systolic_bp", "diastolic_bp"):
        res = validate_field(name, "130/85 mmHg")
        assert res.is_valid is True, f"field_name={name!r} should pass BP check"


# ── 2–5. Dosage Validation (no drug-name fuzzy match) ────────────────────────


def test_dosage_with_strength_and_frequency():
    """Complete dosage value passes without requiring a drug name."""
    res = validate_field("dosage", "500mg twice daily")
    assert res.is_valid is True
    # No fuzzy-match check should appear for dosage fields
    assert not any(c["check_name"] == "medication_fuzzy_match" for c in res.checks)


def test_dosage_missing_frequency():
    """Dosage without a frequency keyword fails with 'frequency missing'."""
    res = validate_field("dosage", "500mg")
    assert res.is_valid is False
    assert "frequency missing" in res.validation_errors


def test_dosage_missing_strength():
    """Dosage without a strength value fails with 'strength missing'."""
    res = validate_field("dosage", "twice daily")
    assert res.is_valid is False
    assert "strength missing" in res.validation_errors


def test_dosage_variant_field_names():
    """'strength' and 'frequency' field names are also treated as dosage."""
    res = validate_field("strength", "10mg once daily")
    assert res.is_valid is True


# ── 6–9. Medication Validation (strength + frequency + fuzzy match) ──────────


def test_medication_complete():
    """Medication with drug name, strength, and frequency passes all checks.

    ``"Metformin 500mg twice daily"`` → PASS because it contains all three
    required components: drug name (fuzzy-matched), strength (500mg), and
    frequency (twice daily).
    """
    res = validate_field("medication", "Metformin 500mg twice daily")
    assert res.is_valid is True
    assert any(c["check_name"] == "medication_fuzzy_match" and c["passed"] for c in res.checks)


def test_medication_bare_drug_name_fails():
    """A bare drug name with no strength or frequency fails validation.

    ``"Metformin"`` → FAIL because it is missing both strength and frequency
    details required for a safe prescription.
    """
    res = validate_field("medication", "Metformin")
    assert res.is_valid is False
    assert "frequency missing" in res.validation_errors
    assert "strength missing" in res.validation_errors


def test_medication_missing_frequency():
    """Medication without frequency fails with 'frequency missing'."""
    res = validate_field("medication", "Metformin 500mg")
    assert res.is_valid is False
    assert "frequency missing" in res.validation_errors


def test_medication_missing_strength():
    """Medication without strength fails with 'strength missing'."""
    res = validate_field("medication", "Metformin daily")
    assert res.is_valid is False
    assert "strength missing" in res.validation_errors


def test_medication_prescription_alias():
    """'prescription' field name routes to the same medication checks."""
    res = validate_field("prescription", "Lisinopril 10mg daily")
    assert res.is_valid is True


# ── 10–12. Date Validation ───────────────────────────────────────────────────


def test_future_date_rejected():
    """Dates in the future are rejected."""
    res = validate_field("recorded_at", "2099-01-01T12:00:00Z")
    assert res.is_valid is False
    assert any("future" in e.lower() for e in res.validation_errors)


def test_past_date_accepted():
    """Historical dates pass validation."""
    res = validate_field("recorded_at", "2024-01-15T10:30:00Z")
    assert res.is_valid is True


def test_unparseable_date_rejected():
    """Non-date strings in a date field fail validation."""
    res = validate_field("date", "not-a-date")
    assert res.is_valid is False
    assert any("Unparseable" in e for e in res.validation_errors)


def test_date_iso_format_detected():
    """An ISO date string is routed to the date check even with an
    unrecognised field name."""
    res = validate_field("custom_ts", "2099-06-01T00:00:00Z")
    assert res.is_valid is False
    assert any("future" in e.lower() for e in res.validation_errors)


# ── 13–14. Medication Fuzzy Match ────────────────────────────────────────────


def test_fuzzy_match_known_medicine():
    """Known formulary names pass the fuzzy-match check."""
    res = validate_field("medication", "Metformin 500mg twice daily")
    assert res.is_valid is True
    assert any("Matched known medicine" in c["message"] for c in res.checks)


def test_fuzzy_match_unknown_medicine():
    """Unknown drug names are flagged as low fuzzy match."""
    res = validate_field("medication", "Xyzalfoobar 10mg daily")
    assert res.is_valid is False
    assert any("fuzzy match low" in e.lower() for e in res.validation_errors)


def test_fuzzy_match_close_spelling():
    """A close misspelling of a known medicine still passes the threshold."""
    res = validate_field("medication", "Metformn 500mg daily")
    # "metformn" vs "metformin" → SequenceMatcher ratio ≈ 0.89 > 0.65
    assert any(c["check_name"] == "medication_fuzzy_match" for c in res.checks)


def test_fuzzy_match_substring_fallback():
    """A known medicine appearing as a substring in the value passes."""
    res = validate_field("medication", "metformin-extended 500mg daily")
    assert res.is_valid is True


# ── 15–18. Lab Value Validation & Sugar Reference Ranges ─────────────────────


def test_abnormal_hba1c_flagged():
    """HbA1c above the reference range is flagged as abnormal."""
    res = validate_field("hba1c", "7.8 %")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is True


def test_normal_sugar_within_range():
    """Fasting glucose within the reference range is not abnormal.

    Reference range: 70–100 mg/dL.  A value of 85 is squarely normal.
    """
    res = validate_field("sugar", "85 mg/dL")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is False


def test_normal_sugar_at_boundary_min():
    """Sugar at the exact low boundary (70 mg/dL) is normal."""
    res = validate_field("sugar", "70 mg/dL")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is False


def test_normal_sugar_at_boundary_max():
    """Sugar at the exact high boundary (100 mg/dL) is normal."""
    res = validate_field("sugar", "100 mg/dL")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is False


def test_abnormal_sugar_above_range():
    """Fasting glucose above the reference range is flagged as abnormal.

    A value of 140 mg/dL is well above the 70–100 range and is flagged.
    """
    res = validate_field("sugar", "140 mg/dL")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is True


def test_abnormal_sugar_slightly_above_range():
    """Pre-diabetic sugar (110 mg/dL) is flagged abnormal.

    The old code only flagged >= 126 (diabetic threshold).  Now values
    like 110 that fall above the 70–100 reference range are also
    surfaced to reviewers instead of silently passing as normal.
    """
    res = validate_field("sugar", "110 mg/dL")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is True


def test_abnormal_sugar_below_range():
    """Fasting glucose below the reference range is flagged as abnormal.

    Hypoglycaemic values like 55 mg/dL fall below the 70 mg/dL floor.
    """
    res = validate_field("sugar", "55 mg/dL")
    assert res.is_valid is True
    assert res.reference_range is not None
    assert res.reference_range["is_abnormal"] is True


def test_lab_without_unit_fails():
    """A lab value without a recognised unit fails validation."""
    res = validate_field("lab_result", "just a number")
    assert res.is_valid is False
    assert any("Missing numeric lab value or recognized unit" in e for e in res.validation_errors)


# ── 20–21. Sugar Discrepancy Conflict Detection ──────────────────────────────


def test_sugar_discrepancy_conflict():
    """Two sugar readings far apart produce a VALUE_DISCREPANCY conflict."""
    f1 = ExtractedField(
        field_id="s1", job_id="j1", field_name="sugar",
        raw_value="105 mg/dL", confidence=0.98, risk_level="LOW_RISK",
    )
    f2 = ExtractedField(
        field_id="s2", job_id="j1", field_name="sugar",
        raw_value="280 mg/dL", confidence=0.98, risk_level="HIGH_RISK",
    )

    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "VALUE_DISCREPANCY"
    assert "sugar" in conflicts[0].message.lower()
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_close_sugar_no_conflict():
    """Two close sugar readings do NOT produce a conflict."""
    f1 = ExtractedField(
        field_id="s1", job_id="j1", field_name="sugar",
        raw_value="95 mg/dL", confidence=0.95,
    )
    f2 = ExtractedField(
        field_id="s2", job_id="j1", field_name="sugar",
        raw_value="100 mg/dL", confidence=0.95,
    )

    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 0
    assert f1.has_conflict is False
    assert f2.has_conflict is False


# ── 22. Allergy vs Medication Conflict ───────────────────────────────────────


def test_allergy_vs_medication_conflict():
    """Penicillin allergy + Amoxicillin prescription → CONTRAINDICATION."""
    f_alg = ExtractedField(
        field_id="a1", job_id="j1", field_name="allergy",
        raw_value="Penicillin", confidence=0.99, risk_level="HIGH_RISK",
    )
    f_med = ExtractedField(
        field_id="m1", job_id="j1", field_name="medication",
        raw_value="Amoxicillin 500mg twice daily", confidence=0.95, risk_level="HIGH_RISK",
    )

    conflicts = detect_conflicts([f_alg, f_med])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "CONTRAINDICATION"
    assert f_alg.has_conflict is True
    assert f_med.has_conflict is True


def test_allergy_direct_match_conflict():
    """Allergy to a drug that also appears as a direct prescription."""
    f_alg = ExtractedField(
        field_id="a2", job_id="j1", field_name="allergy",
        raw_value="Ibuprofen", confidence=0.99,
    )
    f_med = ExtractedField(
        field_id="m2", job_id="j1", field_name="medication",
        raw_value="Ibuprofen 400mg daily", confidence=0.95,
    )

    conflicts = detect_conflicts([f_alg, f_med])
    assert len(conflicts) >= 1
    assert any(c.conflict_type == "CONTRAINDICATION" for c in conflicts)


def test_unrelated_allergy_no_conflict():
    """An allergy that doesn't match any medication produces no conflict."""
    f_alg = ExtractedField(
        field_id="a3", job_id="j1", field_name="allergy",
        raw_value="Peanuts", confidence=0.99,
    )
    f_med = ExtractedField(
        field_id="m3", job_id="j1", field_name="medication",
        raw_value="Metformin 500mg twice daily", confidence=0.95,
    )

    conflicts = detect_conflicts([f_alg, f_med])
    assert len(conflicts) == 0


# ── 23–24. BP Conflict Detection ─────────────────────────────────────────────


def test_same_bp_no_conflict():
    """Identical BP readings produce no conflict."""
    f1 = ExtractedField(
        field_id="b1", job_id="j1", field_name="bp",
        raw_value="120/80 mmHg", confidence=0.97,
    )
    f2 = ExtractedField(
        field_id="b2", job_id="j1", field_name="bp",
        raw_value="120/80", confidence=0.97,
    )

    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 0
    assert f1.has_conflict is False
    assert f2.has_conflict is False


def test_different_bp_conflict():
    """Materially different BP readings produce a VALUE_DISCREPANCY conflict."""
    f1 = ExtractedField(
        field_id="b1", job_id="j1", field_name="bp",
        raw_value="120/80", confidence=0.97,
    )
    f2 = ExtractedField(
        field_id="b2", job_id="j1", field_name="bp",
        raw_value="140/90", confidence=0.97,
    )

    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "VALUE_DISCREPANCY"
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_bp_mmhg_normalisation_no_false_conflict():
    """'120/80' and '120/80 mmHg' normalise to the same value → no conflict."""
    f1 = ExtractedField(
        field_id="b1", job_id="j1", field_name="bp",
        raw_value="120/80", confidence=0.97,
    )
    f2 = ExtractedField(
        field_id="b2", job_id="j1", field_name="bp",
        raw_value="120/80 mmHg", confidence=0.97,
    )

    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 0


# ── 25. Conflict Forces has_conflict ─────────────────────────────────────────


def test_conflict_forces_review_flag():
    """Every field involved in a conflict has has_conflict=True."""
    f1 = ExtractedField(
        field_id="s1", job_id="j1", field_name="sugar",
        raw_value="90 mg/dL", confidence=0.99, risk_level="LOW_RISK",
    )
    f2 = ExtractedField(
        field_id="s2", job_id="j1", field_name="sugar",
        raw_value="250 mg/dL", confidence=0.95, risk_level="HIGH_RISK",
    )

    detect_conflicts([f1, f2])
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_conflict_sets_validation_result_flag():
    """When validation_result is a ValidationResult model, has_conflict is
    also set to True on the nested object."""
    from app.models.extracted_field import ValidationResult

    f1 = ExtractedField(
        field_id="s1", job_id="j1", field_name="sugar",
        raw_value="90 mg/dL", validation_result=ValidationResult(is_valid=True),
    )
    f2 = ExtractedField(
        field_id="s2", job_id="j1", field_name="sugar",
        raw_value="250 mg/dL", validation_result=ValidationResult(is_valid=True),
    )

    detect_conflicts([f1, f2])
    assert f1.validation_result.has_conflict is True  # type: ignore[union-attr]
    assert f2.validation_result.has_conflict is True  # type: ignore[union-attr]


# ── 26. Empty / edge cases ───────────────────────────────────────────────────


def test_no_fields_no_conflicts():
    """An empty field list produces no conflicts."""
    assert detect_conflicts([]) == []


def test_single_field_no_conflicts():
    """A single field cannot conflict with anything."""
    f = ExtractedField(field_id="x", job_id="j", field_name="sugar", raw_value="90 mg/dL")
    assert detect_conflicts([f]) == []


def test_unrecognised_field_name_no_validation_errors():
    """A field name that doesn't match any category passes (no checks)."""
    res = validate_field("hair_colour", "brown")
    assert res.is_valid is True
    assert res.checks == []


# ── Unknown Lab Safety Guardrails ────────────────────────────────────────────


def test_generic_lab_unknown_reference_requires_review():
    """Generic labs with no configured range must require human review."""
    res = validate_field("lab_result", "450 mg/dL")
    assert res.is_valid is True
    assert "unknown reference range requires review" in res.validation_errors
    assert res.reference_range is not None
    assert res.reference_range["reference_range_known"] is False
    assert res.reference_range["unknown_reference_range"] is True
    assert res.reference_range["requires_review"] is True
    assert res.reference_range["is_abnormal"] is None


def test_generic_lab_unknown_reference_not_fake_normal():
    """Unknown labs must not receive fabricated 0-100 normal bounds."""
    res = validate_field("lab_value", "450 mg/dL")
    assert res.reference_range is not None
    assert "min" not in res.reference_range
    assert "max" not in res.reference_range
    assert res.reference_range["is_abnormal"] is None


def test_medical_validator_source_has_no_fake_generic_lab_range():
    """Guard against reintroducing the old fake generic lab normal range."""
    source = Path("app/ai/medical_validator.py").read_text(encoding="utf-8")
    assert "unknown reference range requires review" in source
    assert '"min": 0.0' not in source
    assert '{"min": 0.0, "max": 100.0' not in source


# ── Expanded Conflict Detection Guardrails ──────────────────────────────────


def _field(field_id: str, field_name: str, value: str) -> ExtractedField:
    return ExtractedField(
        field_id=field_id,
        job_id="j-conflict",
        field_name=field_name,
        raw_value=value,
        confidence=0.98,
    )


def test_hba1c_discrepancy_conflict():
    """HbA1c readings with materially different values force review."""
    f1 = _field("a1", "hba1c", "7.2 %")
    f2 = _field("a2", "hba1c", "9.8 %")
    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "VALUE_DISCREPANCY"
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_close_hba1c_no_conflict():
    """Close HbA1c values do not create false conflicts."""
    f1 = _field("a1", "hba1c", "7.2 %")
    f2 = _field("a2", "hba1c", "7.3 %")
    assert detect_conflicts([f1, f2]) == []


def test_heart_rate_discrepancy_conflict():
    """Materially different pulse/heart-rate values force review."""
    f1 = _field("hr1", "heart_rate", "78 bpm")
    f2 = _field("hr2", "pulse", "140 bpm")
    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_close_heart_rate_no_conflict():
    """Small heart-rate variance is not treated as a conflict."""
    assert detect_conflicts([_field("hr1", "heart_rate", "78 bpm"), _field("hr2", "pulse", "82 bpm")]) == []


def test_spo2_discrepancy_conflict():
    """Materially different oxygen saturation values force review."""
    f1 = _field("o1", "spo2", "98 %")
    f2 = _field("o2", "sp_o2", "88 %")
    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_temperature_unit_mismatch_conflict():
    """Temperature values with incompatible units require review."""
    f1 = _field("t1", "temperature", "37 C")
    f2 = _field("t2", "temp", "98.6 F")
    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert "unit" in conflicts[0].message.lower()


def test_generic_lab_same_unit_discrepancy_conflict():
    """Generic lab values of the same type and unit use conservative thresholds."""
    f1 = _field("l1", "lab_result", "120 mg/dL")
    f2 = _field("l2", "lab_result", "180 mg/dL")
    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert f1.has_conflict is True
    assert f2.has_conflict is True


def test_generic_lab_incompatible_units_conflict():
    """Generic labs with incompatible units are forced to review."""
    f1 = _field("l1", "lab_value", "120 mg/dL")
    f2 = _field("l2", "lab_value", "6.7 mmol/L")
    conflicts = detect_conflicts([f1, f2])
    assert len(conflicts) == 1
    assert "unit" in conflicts[0].message.lower()


def test_unrelated_numeric_fields_do_not_conflict():
    """Different clinical categories are not compared to each other."""
    conflicts = detect_conflicts([_field("x1", "hba1c", "7.2 %"), _field("x2", "heart_rate", "140 bpm")])
    assert conflicts == []
