"""Test suite for Workstream 4 & 5 AI Ingestion Pipeline Safety Guardrails (`app/services/pipeline_safety.py`).

Verifies:
1. CRITICAL_RISK field never auto-approved.
2. HIGH_RISK field never auto-approved.
3. MEDIUM_RISK at 0.96 routes to review; at 0.97 auto-approves.
4. LOW_RISK at 0.94 routes to review; at 0.95 auto-approves.
5. Allergy special-casing always forces HIGH_RISK and routes to review even at 100% confidence.
6. Failed validation (is_valid=False) routes to review.
7. Conflicting data flag routes to review.
"""

from __future__ import annotations

from app.models.extracted_field import ExtractedField, ValidationResult
from app.services.pipeline_safety import can_auto_approve


def test_critical_field_never_auto_approved():
    """Test 1: CRITICAL_RISK field never auto-approves even at 99% confidence."""
    field = ExtractedField(
        field_id="f-1",
        job_id="job-1",
        field_name="potassium",
        raw_value="7.2 mmol/L",
        confidence=0.99,
        risk_level="CRITICAL_RISK",
    )
    assert can_auto_approve(field) is False


def test_high_risk_field_never_auto_approved():
    """Test 2: HIGH_RISK field never auto-approves even at 99% confidence."""
    field = ExtractedField(
        field_id="f-2",
        job_id="job-2",
        field_name="troponin",
        raw_value="Positive",
        confidence=0.99,
        risk_level="HIGH_RISK",
    )
    assert can_auto_approve(field) is False


def test_medium_risk_threshold():
    """Test 3: MEDIUM_RISK requires confidence >= 0.97 to auto-approve; 0.96 routes to review."""
    f_review = ExtractedField(
        field_id="f-3a",
        job_id="job-3",
        field_name="cholesterol",
        raw_value="240 mg/dL",
        confidence=0.96,
        risk_level="MEDIUM_RISK",
    )
    assert can_auto_approve(f_review) is False

    f_approve = ExtractedField(
        field_id="f-3b",
        job_id="job-3",
        field_name="cholesterol",
        raw_value="240 mg/dL",
        confidence=0.97,
        risk_level="MEDIUM_RISK",
    )
    assert can_auto_approve(f_approve) is True


def test_low_risk_threshold():
    """Test 4: LOW_RISK requires confidence >= 0.95 to auto-approve; 0.94 routes to review."""
    f_review = ExtractedField(
        field_id="f-4a",
        job_id="job-4",
        field_name="bp",
        raw_value="120/80",
        confidence=0.94,
        risk_level="LOW_RISK",
    )
    assert can_auto_approve(f_review) is False

    f_approve = ExtractedField(
        field_id="f-4b",
        job_id="job-4",
        field_name="bp",
        raw_value="120/80",
        confidence=0.95,
        risk_level="LOW_RISK",
    )
    assert can_auto_approve(f_approve) is True


def test_allergy_always_to_review():
    """Test 5: Allergy field forces HIGH_RISK and always routes to review even at 100% confidence."""
    field = ExtractedField(
        field_id="f-5",
        job_id="job-5",
        field_name="allergy",
        raw_value="Penicillin",
        confidence=1.0,
        risk_level="LOW_RISK",
    )
    assert can_auto_approve(field) is False
    assert field.risk_level == "HIGH_RISK"


def test_failed_validation_to_review():
    """Test 6: Field with failed validation (is_valid=False) always routes to review."""
    field = ExtractedField(
        field_id="f-6",
        job_id="job-6",
        field_name="sugar",
        raw_value="invalid_number",
        confidence=0.99,
        risk_level="LOW_RISK",
        validation_result=ValidationResult(is_valid=False, validation_errors=["Not a number"]),
    )
    assert can_auto_approve(field) is False


def test_conflicting_values_to_review():
    """Test 7: Field flagged with conflicting data always routes to review."""
    field = ExtractedField(
        field_id="f-7",
        job_id="job-7",
        field_name="sugar",
        raw_value="140 mg/dL",
        confidence=0.99,
        risk_level="LOW_RISK",
        has_conflict=True,
    )
    assert can_auto_approve(field) is False
