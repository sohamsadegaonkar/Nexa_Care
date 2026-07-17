"""Unit contracts for upload validation and extraction metadata."""

import pytest
from fastapi import HTTPException

from app.api.v2.pipeline_routes import _validate_commit_field_metadata, _validated_upload_type


@pytest.mark.parametrize("name,mime,data", [
    ("report.pdf", "application/pdf", b"%PDF-1.7\n"),
    ("scan.png", "image/png", b"\x89PNG\r\n\x1a\nrest"),
    ("scan.jpg", "image/jpeg", b"\xff\xd8\xffrest"),
])
def test_supported_document_magic_and_mime(name, mime, data):
    safe_name, detected = _validated_upload_type(name, mime, data)
    assert safe_name == name and detected == mime


@pytest.mark.parametrize("name,mime,data", [
    ("payload.exe", "application/octet-stream", b"MZ"),
    ("report.pdf", "application/pdf", b"not a pdf"),
    ("report.pdf", "image/png", b"%PDF-1.7"),
])
def test_unsupported_or_mismatched_documents_are_rejected(name, mime, data):
    with pytest.raises(HTTPException) as exc:
        _validated_upload_type(name, mime, data)
    assert exc.value.status_code == 415


def test_path_components_are_removed_from_filename():
    name, _ = _validated_upload_type("../../report.pdf", "application/pdf", b"%PDF-1.7")
    assert name == "report.pdf"


@pytest.mark.parametrize("field", [
    {"risk_level": "LOW_RISK"},
    {"confidence": 0.8},
    {"confidence": True, "risk_level": "LOW_RISK"},
    {"confidence": 1.1, "risk_level": "LOW_RISK"},
    {"confidence": 0.8, "risk_level": "UNKNOWN"},
])
def test_invalid_extraction_metadata_is_rejected(field):
    with pytest.raises(HTTPException):
        _validate_commit_field_metadata(field)


def test_valid_extraction_metadata_is_accepted():
    _validate_commit_field_metadata({"confidence": 0.8, "risk_level": "HIGH_RISK"})
