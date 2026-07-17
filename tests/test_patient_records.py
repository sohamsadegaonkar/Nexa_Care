"""Patient record schemas reject malformed provenance and fabricated fallbacks."""

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v2.patient_record_routes import (
    AppendLabResultRequest, AppendVitalsRequest, _enrich_timeline_provenance, _parse_uuid,
)

ROOT = Path(__file__).resolve().parents[1]
CODE = (ROOT / "app/api/v2/patient_record_routes.py").read_text(encoding="utf-8")


def test_patient_identifier_is_strict_uuid():
    value = uuid.uuid4()
    assert _parse_uuid(str(value)) == value
    with pytest.raises(HTTPException) as exc:
        _parse_uuid("pat-123")
    assert exc.value.status_code == 422


def test_recorded_timestamp_is_schema_validated():
    with pytest.raises(ValidationError):
        AppendVitalsRequest(
            systolic_bp=120, diastolic_bp=80, heart_rate=70,
            temperature_celsius=37, sp_o2_percentage=98,
            recorded_at="not-a-timestamp",
        )


def test_source_document_identifier_is_schema_validated():
    with pytest.raises(ValidationError):
        AppendLabResultRequest(
            test_name="x", value="x", unit="x", reference_range="x",
            recorded_at="2026-07-17T00:00:00Z", source_document_id="doc-1",
        )


def test_ai_timeline_without_optional_metadata_does_not_invent_it():
    event = _enrich_timeline_provenance(
        "1", "LAB", "Lab", "Reviewed lab", "2026-07-17T00:00:00+00:00", "ai_extracted",
    )
    assert event["confidence"] is None
    assert event["risk_level"] is None
    assert event["review_status"] is None
    assert event["source_detail"] is None


def test_manual_timeline_without_provider_does_not_invent_identity():
    event = _enrich_timeline_provenance(
        "1", "NOTE", "Note", "Reviewed note", "2026-07-17T00:00:00+00:00", "manual",
    )
    assert event["source_display"] == "Manual entry"


def test_unknown_blood_group_is_null_not_a_medical_guess():
    assert '"blood_group": None' in CODE
    assert '"blood_group_verification": "unknown"' in CODE


def test_write_responses_have_no_fabricated_ledger_hash():
    assert "a8f902" not in CODE
    assert CODE.count('"audit_ledger_hash": None') == 5


def test_invalid_dates_are_not_replaced_with_current_time():
    assert "datetime.fromisoformat(payload.recorded_at" not in CODE
    assert "rec_dt = datetime.now" not in CODE


def test_explicit_server_ids_replace_response_only_uuid_fallbacks():
    assert "id=uuid.uuid4()" in CODE
    assert ".id or uuid.uuid4()" not in CODE
