"""Test suite for Workstream 5 Days 9–11: Auto-Approval, Correction Logging,
and Extraction Accuracy Report.

Verifies:
 1. Auto-approval decision matrix (all risk/confidence combos).
 2. Allergy fields never auto-approve (forced HIGH_RISK).
 3. Failed validation blocks auto-approval.
 4. Conflicting data blocks auto-approval.
 5. Missing/invalid confidence blocks auto-approval.
 6. Validation errors block auto-approval.
 7. Decision includes a human-readable reason string.
 8. can_auto_approve() delegates to should_auto_approve().
 9. Correction logger stores records with PII redaction.
10. Correction logger does NOT redact non-PII field values.
11. Export corrections returns stored records.
12. Extraction accuracy report: compute_metrics with demo data.
13. Extraction accuracy report: zero-fields edge case.
14. False-auto-approve rate computed correctly.
15. Per-field accuracy computed correctly.
16. render_markdown produces valid Markdown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.auto_approval import should_auto_approve
from app.ai.medical_validator import validate_field
from app.ai.correction_logger import _redact_value, export_corrections, log_correction
from app.models.extracted_field import ExtractedField, ValidationResult
from app.services.pipeline_safety import can_auto_approve
from scripts.extraction_accuracy_report import compute_metrics, render_markdown


# ═══════════════════════════════════════════════════════════════════════════
# 1–6. Auto-Approval Decision Matrix
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoApprovalDecisionMatrix:
    """Exhaustive parametrised check of every risk-tier / confidence combo."""

    @pytest.mark.parametrize(
        "risk, conf, expected",
        [
            # LOW_RISK threshold: 0.95
            ("LOW_RISK", 0.95, True),
            ("LOW_RISK", 0.96, True),
            ("LOW_RISK", 1.00, True),
            ("LOW_RISK", 0.94, False),
            ("LOW_RISK", 0.50, False),
            ("LOW_RISK", 0.00, False),
            # MEDIUM_RISK threshold: 0.97
            ("MEDIUM_RISK", 0.97, True),
            ("MEDIUM_RISK", 0.98, True),
            ("MEDIUM_RISK", 0.96, False),
            ("MEDIUM_RISK", 0.95, False),
            # HIGH_RISK: never
            ("HIGH_RISK", 0.97, False),
            ("HIGH_RISK", 0.99, False),
            ("HIGH_RISK", 1.00, False),
            # CRITICAL_RISK: never
            ("CRITICAL_RISK", 0.99, False),
            ("CRITICAL_RISK", 1.00, False),
        ],
    )
    def test_decision_matrix(self, risk: str, conf: float, expected: bool):
        """Each risk/confidence combo produces the correct boolean decision."""
        field = ExtractedField(
            field_id="f-matrix",
            job_id="j-matrix",
            field_name="bp",
            raw_value="120/80",
            confidence=conf,
            risk_level=risk,
        )
        decision = should_auto_approve(field)
        assert decision.auto_approve is expected


# ═══════════════════════════════════════════════════════════════════════════
# Allergy special-casing
# ═══════════════════════════════════════════════════════════════════════════


def test_allergy_never_auto_approves():
    """Allergy fields are forced to HIGH_RISK and always require review."""
    field = ExtractedField(
        field_id="f-alg",
        job_id="j1",
        field_name="allergy",
        raw_value="Penicillin",
        confidence=1.0,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False
    assert field.risk_level == "HIGH_RISK"


def test_allergen_alias_never_auto_approves():
    """'allergen' field name is treated identically to 'allergy'."""
    field = ExtractedField(
        field_id="f-alg2",
        job_id="j1",
        field_name="allergen",
        raw_value="Peanuts",
        confidence=0.99,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False


# ═══════════════════════════════════════════════════════════════════════════
# Failed validation
# ═══════════════════════════════════════════════════════════════════════════


def test_failed_validation_blocks_auto_approval():
    """is_valid=False prevents auto-approval regardless of risk/confidence."""
    field = ExtractedField(
        field_id="f-val",
        job_id="j1",
        field_name="sugar",
        raw_value="invalid",
        confidence=0.99,
        risk_level="LOW_RISK",
        validation_result=ValidationResult(
            is_valid=False, validation_errors=["Bad format"]
        ),
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False


def test_validation_errors_block_auto_approval():
    """Non-empty validation_errors list blocks auto-approval even if is_valid is True."""
    field = ExtractedField(
        field_id="f-verr",
        job_id="j1",
        field_name="bp",
        raw_value="120/80",
        confidence=0.98,
        risk_level="LOW_RISK",
        validation_result=ValidationResult(
            is_valid=True, validation_errors=["suspicious"]
        ),
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False


def test_unknown_reference_lab_blocks_auto_approval():
    """A valid-looking generic lab with unknown reference range requires review."""
    validation = validate_field("lab_result", "450 mg/dL")
    field = ExtractedField(
        field_id="f-unknown-lab",
        job_id="j1",
        field_name="lab_result",
        raw_value="450 mg/dL",
        confidence=0.99,
        risk_level="LOW_RISK",
        validation_result=validation,
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False
    assert "validation" in decision.reason.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Conflicting data
# ═══════════════════════════════════════════════════════════════════════════


def test_has_conflict_blocks_auto_approval():
    """Top-level has_conflict=True prevents auto-approval."""
    field = ExtractedField(
        field_id="f-conf",
        job_id="j1",
        field_name="sugar",
        raw_value="140 mg/dL",
        confidence=0.99,
        risk_level="LOW_RISK",
        has_conflict=True,
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False


def test_validation_result_conflict_blocks_auto_approval():
    """has_conflict=True inside validation_result also blocks auto-approval."""
    field = ExtractedField(
        field_id="f-vrconf",
        job_id="j1",
        field_name="sugar",
        raw_value="140 mg/dL",
        confidence=0.99,
        risk_level="LOW_RISK",
        validation_result=ValidationResult(is_valid=True, has_conflict=True),
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False


# ═══════════════════════════════════════════════════════════════════════════
# Missing / invalid confidence
# ═══════════════════════════════════════════════════════════════════════════


def test_missing_confidence_blocks_auto_approval():
    """None confidence prevents auto-approval."""
    field = ExtractedField(
        field_id="f-noconf",
        job_id="j1",
        field_name="bp",
        raw_value="120/80",
        confidence=None,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False


def test_invalid_confidence_blocks_auto_approval():
    """Out-of-range confidence prevents auto-approval."""
    field = ExtractedField(
        field_id="f-badconf",
        job_id="j1",
        field_name="bp",
        raw_value="120/80",
        confidence=1.5,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve is False


# ═══════════════════════════════════════════════════════════════════════════
# 7. Decision includes reason string
# ═══════════════════════════════════════════════════════════════════════════


def test_decision_includes_reason():
    """Every AutoApprovalDecision includes a non-empty reason string."""
    field = ExtractedField(
        field_id="f-reason",
        job_id="j1",
        field_name="bp",
        raw_value="120/80",
        confidence=0.96,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert isinstance(decision.reason, str)
    assert len(decision.reason) > 0


def test_rejection_reason_describes_why():
    """Rejection reasons describe the specific failure cause."""
    # HIGH_RISK rejection
    field = ExtractedField(
        field_id="f-why1",
        job_id="j1",
        field_name="medication",
        raw_value="Metformin",
        confidence=0.99,
        risk_level="HIGH_RISK",
    )
    decision = should_auto_approve(field)
    assert "HIGH_RISK" in decision.reason

    # Confidence below threshold
    field = ExtractedField(
        field_id="f-why2",
        job_id="j1",
        field_name="bp",
        raw_value="120/80",
        confidence=0.90,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert "below threshold" in decision.reason


# ═══════════════════════════════════════════════════════════════════════════
# 8. can_auto_approve delegates to should_auto_approve
# ═══════════════════════════════════════════════════════════════════════════


def test_can_auto_approve_delegates():
    """can_auto_approve() returns the same boolean as should_auto_approve()."""
    field = ExtractedField(
        field_id="f-deleg",
        job_id="j1",
        field_name="bp",
        raw_value="120/80",
        confidence=0.96,
        risk_level="LOW_RISK",
    )
    assert can_auto_approve(field) == should_auto_approve(field).auto_approve


def test_can_auto_approve_rejects_critical():
    """can_auto_approve returns False for CRITICAL_RISK (regression guard)."""
    field = ExtractedField(
        field_id="f-crit",
        job_id="j1",
        field_name="potassium",
        raw_value="7.2 mmol/L",
        confidence=0.99,
        risk_level="CRITICAL_RISK",
    )
    assert can_auto_approve(field) is False


# ═══════════════════════════════════════════════════════════════════════════
# 9–10. Correction Logger
# ═══════════════════════════════════════════════════════════════════════════


def test_redact_pii_values():
    """PII field values are replaced with [REDACTED]."""
    assert _redact_value("patient_name", "Asha Raman") == "[REDACTED]"
    assert _redact_value("phone", "9876543210") == "[REDACTED]"
    assert _redact_value("aadhaar_abha_id", "12-3456-7890") == "[REDACTED]"


def test_no_redact_non_pii_values():
    """Non-PII field values pass through unchanged."""
    assert _redact_value("bp", "120/80") == "120/80"
    assert _redact_value("medication", "Metformin 500mg") == "Metformin 500mg"
    assert _redact_value("sugar", "140 mg/dL") == "140 mg/dL"


@pytest.mark.asyncio
async def test_log_correction_stores_redacted_pii():
    """log_correction redacts PII field values before persisting."""
    field_id = uuid.uuid4()
    job_id = uuid.uuid4()

    mock_db = MagicMock()
    mock_db.add = MagicMock()

    fc = await log_correction(
        field_id=field_id,
        job_id=job_id,
        field_name="patient_name",
        original_value="Asha Raman",
        corrected_value="Asha R.",
        confidence=0.92,
        original_risk="LOW_RISK",
        document_type="LAB_REPORT",
        corrected_by="doc-1",
        db=mock_db,
    )

    # The returned FieldCorrection should have redacted values
    assert fc.original_value == "[REDACTED]"
    assert fc.corrected_value == "[REDACTED]"
    assert fc.field_name == "patient_name"
    assert fc.confidence == 0.92
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_log_correction_preserves_non_pii():
    """log_correction does NOT redact non-PII field values."""
    field_id = uuid.uuid4()
    job_id = uuid.uuid4()

    mock_db = MagicMock()
    mock_db.add = MagicMock()

    fc = await log_correction(
        field_id=field_id,
        job_id=job_id,
        field_name="sugar",
        original_value="140 mg/dL",
        corrected_value="142 mg/dL",
        confidence=0.88,
        original_risk="MEDIUM_RISK",
        db=mock_db,
    )

    assert fc.original_value == "140 mg/dL"
    assert fc.corrected_value == "142 mg/dL"


@pytest.mark.asyncio
async def test_export_corrections_returns_records():
    """export_corrections queries the DB and returns a list of dicts."""
    mock_fc = MagicMock()
    mock_fc.field_name = "sugar"
    mock_fc.original_value = "140 mg/dL"
    mock_fc.corrected_value = "142 mg/dL"
    mock_fc.confidence = 0.88
    mock_fc.corrected_by = "doc-1"
    mock_fc.corrected_at = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_fc]

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    records = await export_corrections(mock_db)
    assert len(records) == 1
    assert records[0]["field_name"] == "sugar"
    assert records[0]["original_value"] == "140 mg/dL"


# ═══════════════════════════════════════════════════════════════════════════
# 12–15. Extraction Accuracy Report
# ═══════════════════════════════════════════════════════════════════════════


def test_compute_metrics_demo_data():
    """compute_metrics returns correct rates for the demo dataset."""
    from scripts.extraction_accuracy_report import (
        _get_demo_corrections,
        _get_demo_fields,
    )

    fields = _get_demo_fields()
    corrections = _get_demo_corrections()
    m = compute_metrics(fields, corrections)
    assert m["total_fields"] == 165
    assert m["auto_approved_count"] == 82
    assert m["auto_approval_rate"] == round(82 / 165, 4)
    assert m["edited_count"] == 3
    assert m["false_auto_approve_count"] == 0
    assert m["false_auto_approve_rate"] == 0.0
    assert m["overall_accuracy"] == round(162 / 165, 4)
    assert m["medical_ready"] is True


def test_compute_metrics_zero_fields():
    """compute_metrics handles empty input gracefully."""
    m = compute_metrics([], [])
    assert m["total_fields"] == 0
    assert m["auto_approval_rate"] == 0.0
    assert m["false_auto_approve_rate"] == 0.0
    assert m["overall_accuracy"] == 0.0
    assert m["per_field_accuracy"] == {}


def test_false_auto_approve_rate_computed_correctly():
    """False-auto-approve rate = auto-approved fields later corrected / all auto-approved."""
    fields = [
        {"field_name": "bp", "status": "auto_approved", "field_id": "a"},
        {"field_name": "bp", "status": "auto_approved", "field_id": "b"},
        {"field_name": "bp", "status": "auto_approved", "field_id": "c"},
        {"field_name": "bp", "status": "auto_approved", "field_id": "d"},
    ]
    corrections = [
        {"field_id": "a", "field_name": "bp"},
        {"field_id": "b", "field_name": "bp"},
    ]
    m = compute_metrics(fields, corrections)
    assert m["false_auto_approve_count"] == 2
    assert m["false_auto_approve_rate"] == 0.5


def test_per_field_accuracy_computed_correctly():
    """Per-field accuracy = (all fields − corrected) / all fields per name."""
    fields = [
        {"field_name": "sugar", "status": "auto_approved", "field_id": "s1"},
        {"field_name": "sugar", "status": "auto_approved", "field_id": "s2"},
        {"field_name": "sugar", "status": "approved", "field_id": "s3"},
        {"field_name": "bp", "status": "auto_approved", "field_id": "b1"},
        {"field_name": "bp", "status": "approved", "field_id": "b2"},
    ]
    corrections = [
        {"field_id": "s1", "field_name": "sugar"},
    ]
    m = compute_metrics(fields, corrections)
    # sugar: 3 total, 1 corrected → accuracy = 2/3 ≈ 0.6667
    assert abs(m["per_field_accuracy"]["sugar"] - 0.6667) < 0.01
    # bp: 2 total, 0 corrected → accuracy = 1.0
    assert m["per_field_accuracy"]["bp"] == 1.0


def test_human_correction_rate():
    """Human-correction rate = edited / reviewed fields."""
    fields = [
        {"field_name": "sugar", "status": "needs_review", "field_id": "f1"},
        {"field_name": "sugar", "status": "approved", "field_id": "f2"},
        {"field_name": "sugar", "status": "edited", "field_id": "f3"},
        {"field_name": "sugar", "status": "rejected", "field_id": "f4"},
        {"field_name": "sugar", "status": "auto_approved", "field_id": "f5"},
    ]
    corrections = []
    m = compute_metrics(fields, corrections)
    # reviewed = needs_review + approved + edited + rejected = 4
    # edited = 1
    assert m["human_correction_rate"] == 0.25


# ═══════════════════════════════════════════════════════════════════════════
# 16. render_markdown
# ═══════════════════════════════════════════════════════════════════════════


def test_render_markdown_produces_valid_report():
    """render_markdown returns a non-empty Markdown string with key sections."""
    m = compute_metrics(
        [
            {"field_name": "bp", "status": "auto_approved", "field_id": "f1"},
            {"field_name": "bp", "status": "needs_review", "field_id": "f2"},
        ],
        [],
    )
    md = render_markdown(m)
    assert "# Extraction Accuracy Report" in md
    assert "Auto-approval rate" in md
    assert "False-auto-approve rate" in md
    assert "Overall extraction accuracy" in md
    assert "Per-Field-Type Accuracy" in md
    assert "Medical Readiness Gate" in md
    assert "Methodology" in md
