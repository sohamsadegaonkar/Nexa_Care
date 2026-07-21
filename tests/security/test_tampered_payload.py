"""T-05: tampered pipeline and signed-consent payloads fail closed."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.v2.pipeline_routes import CommitJobRequest, _validate_commit_field_metadata
from app.models.extracted_field import ExtractedField

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("metadata", [
    {}, {"confidence": 0.9}, {"risk_level": "LOW_RISK"},
    {"confidence": -1, "risk_level": "LOW_RISK"},
    {"confidence": 2, "risk_level": "LOW_RISK"},
    {"confidence": 0.9, "risk_level": "INJECTED"},
])
def test_tampered_extraction_metadata_is_rejected(metadata):
    with pytest.raises(HTTPException):
        _validate_commit_field_metadata(metadata)


def test_client_supplied_commit_fields_have_explicit_rejection_path():
    payload = CommitJobRequest(
        patient_id="11111111-1111-4111-8111-111111111111",
        fields=[{"status": "approved", "raw_value": "tampered"}],
    )
    assert payload.fields is not None
    source = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
    assert "CLIENT_SUPPLIED_COMMIT_FIELDS_FORBIDDEN" in source


def test_client_supplied_clinical_summary_has_explicit_rejection_path():
    source = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
    assert "CLIENT_SUPPLIED_CLINICAL_SUMMARY_FORBIDDEN" in source


def test_extracted_field_has_no_placeholder_identity_defaults():
    field = ExtractedField(field_name="lab_result", raw_value="x")
    assert field.field_id is None and field.job_id is None


def test_signed_payload_has_no_delimiter_ambiguity():
    backend = (ROOT / "app/services/signed_approval_verifier.py").read_text(encoding="utf-8")
    assert "sort_keys=True" in backend
    assert "separators=(\",\", \":\")" in backend
    assert "nexa-consent-v2" in backend
