from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import ValidationError
import pytest

from app.api.v2.patient_external_record_routes import (
    PatientExternalRecordReviewDecisionRequest,
    PatientExternalRecordReviewItemResponse,
    _set_no_store,
)
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
        ("GET", "/api/v2/patient/me/external-records/upload-policy"),
        ("GET", "/api/v2/patient/me/external-records/{import_id}"),
        ("POST", "/api/v2/patient/me/external-records/{import_id}/process"),
        ("POST", "/api/v2/patient/me/external-records/{import_id}/retry"),
        ("POST", "/api/v2/patient/me/external-records/{import_id}/cancel"),
        ("GET", "/api/v2/patient/me/external-records/{import_id}/source"),
        ("POST", "/api/v2/patient/me/external-records/{import_id}/save"),
        ("GET", "/api/v2/patient/me/external-records/{import_id}/review"),
        (
            "POST",
            "/api/v2/patient/me/external-records/{import_id}/review/{review_item_id}",
        ),
    }
    actual = {
        (method, candidate.path)
        for candidate in router.routes
        for method in (getattr(candidate, "methods", set()) or set())
        if "external-records" in getattr(candidate, "path", "")
    }
    assert expected <= actual


def _client_parameter_names(route) -> set[str]:
    dependant = route.dependant
    return {
        item.name
        for collection in (
            dependant.path_params,
            dependant.query_params,
            dependant.header_params,
            dependant.body_params,
        )
        for item in collection
    }


def test_upload_authority_is_dependency_derived_not_patient_input() -> None:
    route = _route("/api/v2/patient/me/external-records", "POST")
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert "patient_id" not in _client_parameter_names(route)
    assert get_current_patient in dependency_calls


def test_process_authority_is_dependency_derived_and_has_no_provider_inputs() -> None:
    route = _route(
        "/api/v2/patient/me/external-records/{import_id}/process",
        "POST",
    )
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
    client_names = _client_parameter_names(route)

    assert get_current_patient in dependency_calls
    assert "patient_id" not in client_names
    assert {
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "consent_request_id",
        "clinical_access_session_id",
    }.isdisjoint(client_names)



def test_retry_cancel_authority_is_patient_dependency_only() -> None:
    forbidden = {
        "patient_id",
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "consent_request_id",
        "clinical_access_session_id",
        "treatment_token",
    }
    for path in (
        "/api/v2/patient/me/external-records/{import_id}/retry",
        "/api/v2/patient/me/external-records/{import_id}/cancel",
    ):
        route = _route(path, "POST")
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert get_current_patient in dependency_calls
        assert forbidden.isdisjoint(_client_parameter_names(route))


def test_review_routes_are_patient_dependency_derived_without_provider_authority() -> None:
    forbidden = {
        "patient_id",
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "consent_request_id",
        "clinical_access_session_id",
    }
    for method, path in [
        ("GET", "/api/v2/patient/me/external-records/{import_id}/review"),
        (
            "POST",
            "/api/v2/patient/me/external-records/{import_id}/review/{review_item_id}",
        ),
    ]:
        route = _route(path, method)
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert get_current_patient in dependency_calls
        assert forbidden.isdisjoint(_client_parameter_names(route))


def test_review_decision_payload_rejects_authority_injection() -> None:
    assert PatientExternalRecordReviewDecisionRequest.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        PatientExternalRecordReviewDecisionRequest(
            decision="accept",
            patient_id="00000000-0000-0000-0000-000000000000",
        )


def test_review_item_response_hides_internal_candidate_fields() -> None:
    fields = set(PatientExternalRecordReviewItemResponse.model_fields)
    assert "review_item_id" in fields
    assert "field_name" not in fields
    assert "clinical_fact_key" not in fields
    assert "extractor_provider" not in fields
    assert "extractor_version" not in fields


def test_save_authority_is_dependency_derived_without_provider_inputs() -> None:
    route = _route(
        "/api/v2/patient/me/external-records/{import_id}/save",
        "POST",
    )
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
    client_names = _client_parameter_names(route)

    assert get_current_patient in dependency_calls
    assert {
        "patient_id",
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "consent_request_id",
        "clinical_access_session_id",
    }.isdisjoint(client_names)


def test_patient_status_contract_never_exposes_internal_pipeline_lanes() -> None:
    visible = set(PATIENT_STATUS_MAP.values())
    assert visible == {
        "processing",
        "needs_review",
        "ready_to_save",
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
