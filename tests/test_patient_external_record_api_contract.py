from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.responses import Response
import pytest

from app.api.v2.patient_external_record_routes import _set_no_store
from app.api.v2.patient_routes import router
from app.core.dependencies import get_current_patient
from app.models.patient_external_record_import import PatientExternalRecordImport
from app.services.patient_external_record_import import (
    PATIENT_STATUS_MAP,
    _request_matches_existing,
    validate_patient_upload_type,
)


def _route(path: str, method: str):
    for candidate in router.routes:
        methods = getattr(candidate, "methods", set()) or set()
        if getattr(candidate, "path", None) == path and method in methods:
            return candidate
    raise AssertionError(f"route not found: {method} {path}")


def test_patient_external_record_routes_are_registered_under_me_namespace() -> None:
    expected = {
        ("POST", "/api/v2/patient/me/external-records"),
        ("GET", "/api/v2/patient/me/external-records"),
        ("GET", "/api/v2/patient/me/external-records/{import_id}"),
        ("GET", "/api/v2/patient/me/external-records/{import_id}/source"),
    }
    actual = {
        (method, candidate.path)
        for candidate in router.routes
        for method in (getattr(candidate, "methods", set()) or set())
        if "external-records" in getattr(candidate, "path", "")
    }
    assert expected <= actual


def test_upload_authority_is_dependency_derived_not_patient_input() -> None:
    route = _route("/api/v2/patient/me/external-records", "POST")
    dependant = route.dependant
    client_names = {
        item.name
        for collection in (
            dependant.path_params,
            dependant.query_params,
            dependant.header_params,
            dependant.body_params,
        )
        for item in collection
    }
    dependency_calls = {dependency.call for dependency in dependant.dependencies}

    assert "patient_id" not in client_names
    assert get_current_patient in dependency_calls


def test_patient_status_contract_never_exposes_internal_pipeline_lanes() -> None:
    visible = set(PATIENT_STATUS_MAP.values())
    assert visible == {
        "processing",
        "needs_review",
        "imported",
        "retry_available",
        "could_not_process",
        "cancelled",
    }
    assert "SOURCE_ONLY" not in visible
    assert "QUARANTINE" not in visible
    assert "ADJUDICATION_PENDING" not in visible
    assert "OCR_CANDIDATE" not in visible
    assert "PIPELINE_LANE" not in visible


def test_upload_type_validation_accepts_supported_structural_envelopes() -> None:
    pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    png = b"\x89PNG\r\n\x1a\n" + b"synthetic" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    jpeg = b"\xff\xd8\xff" + b"synthetic" + b"\xff\xd9"

    assert validate_patient_upload_type("report.pdf", "application/pdf", pdf)[1] == "application/pdf"
    assert validate_patient_upload_type("scan.png", "image/png", png)[1] == "image/png"
    assert validate_patient_upload_type("photo.jpeg", "image/jpeg", jpeg)[1] == "image/jpeg"


def test_upload_type_validation_rejects_mismatch_malformed_and_unsupported() -> None:
    with pytest.raises(HTTPException) as mismatch:
        validate_patient_upload_type("report.pdf", "application/pdf", b"not-a-pdf")
    assert mismatch.value.status_code == 415
    assert mismatch.value.detail["error_code"] == "DOCUMENT_TYPE_MISMATCH"

    with pytest.raises(HTTPException) as malformed_pdf:
        validate_patient_upload_type("report.pdf", "application/pdf", b"%PDF-1.4\ntruncated")
    assert malformed_pdf.value.status_code == 422
    assert malformed_pdf.value.detail["error_code"] == "DOCUMENT_MALFORMED"

    with pytest.raises(HTTPException) as malformed_png:
        validate_patient_upload_type("scan.png", "image/png", b"\x89PNG\r\n\x1a\ntruncated")
    assert malformed_png.value.status_code == 422
    assert malformed_png.value.detail["error_code"] == "DOCUMENT_MALFORMED"

    with pytest.raises(HTTPException) as malformed_jpeg:
        validate_patient_upload_type("scan.jpg", "image/jpeg", b"\xff\xd8\xfftruncated")
    assert malformed_jpeg.value.status_code == 422
    assert malformed_jpeg.value.detail["error_code"] == "DOCUMENT_MALFORMED"

    with pytest.raises(HTTPException) as unsupported:
        validate_patient_upload_type("record.exe", "application/octet-stream", b"MZ")
    assert unsupported.value.status_code == 415
    assert unsupported.value.detail["error_code"] == "UNSUPPORTED_DOCUMENT_TYPE"


def test_idempotency_key_is_scoped_to_patient() -> None:
    constraints = list(PatientExternalRecordImport.__table__.constraints)
    matching = [
        constraint
        for constraint in constraints
        if getattr(constraint, "name", None) == "uq_patient_external_record_import_request"
    ]
    assert len(matching) == 1
    assert [column.name for column in matching[0].columns] == ["patient_id", "request_id"]


def test_idempotent_replay_requires_same_category_and_content() -> None:
    existing = SimpleNamespace(category="LAB_REPORT", content_hash="a" * 64)
    assert _request_matches_existing(
        existing, category="LAB_REPORT", content_hash="a" * 64
    )
    assert not _request_matches_existing(
        existing, category="PRESCRIPTION", content_hash="a" * 64
    )
    assert not _request_matches_existing(
        existing, category="LAB_REPORT", content_hash="b" * 64
    )


def test_patient_json_responses_use_no_store_cache_policy() -> None:
    response = Response()
    _set_no_store(response)
    assert response.headers["cache-control"] == "private, no-store"
