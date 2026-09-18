from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from fastapi.responses import Response
import pytest

from app.api.v2 import patient_external_record_routes as route_module
from app.api.v2.patient_external_record_routes import (
    PatientExternalRecordActions,
    PatientExternalRecordResponse,
    _response,
    read_external_record_upload_policy,
)
from app.api.v2.patient_routes import router
from app.core.dependencies import get_current_patient
from app.services.patient_external_record_import import (
    PATIENT_UPLOAD_EXTENSIONS,
    PATIENT_UPLOAD_MIME_TYPES,
    validate_patient_upload_type,
)


MIB = 1024 * 1024


def _row(status: str, *, retryable: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        category="LAB_REPORT",
        status=status,
        retryable=retryable,
        source_document_id=uuid.uuid4(),
        created_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("status", "retryable", "expected"),
    [
        (
            "UPLOADED",
            False,
            {
                "can_process": True,
                "can_retry": False,
                "can_cancel": True,
                "can_review": False,
                "can_save": False,
            },
        ),
        (
            "PROCESSING",
            False,
            {
                "can_process": False,
                "can_retry": False,
                "can_cancel": False,
                "can_review": False,
                "can_save": False,
            },
        ),
        (
            "FAILED_RETRYABLE",
            True,
            {
                "can_process": False,
                "can_retry": True,
                "can_cancel": True,
                "can_review": False,
                "can_save": False,
            },
        ),
        (
            "FAILED_TERMINAL",
            False,
            {
                "can_process": False,
                "can_retry": False,
                "can_cancel": False,
                "can_review": False,
                "can_save": False,
            },
        ),
        (
            "REVIEW_REQUIRED",
            False,
            {
                "can_process": False,
                "can_retry": False,
                "can_cancel": True,
                "can_review": True,
                "can_save": False,
            },
        ),
        (
            "READY_TO_SAVE",
            False,
            {
                "can_process": False,
                "can_retry": False,
                "can_cancel": True,
                "can_review": True,
                "can_save": True,
            },
        ),
        (
            "COMPLETED",
            False,
            {
                "can_process": False,
                "can_retry": False,
                "can_cancel": False,
                "can_review": False,
                "can_save": False,
            },
        ),
        (
            "CANCELLED",
            False,
            {
                "can_process": False,
                "can_retry": False,
                "can_cancel": False,
                "can_review": False,
                "can_save": False,
            },
        ),
    ],
)
def test_response_actions_are_server_derived_for_every_import_state(
    status: str,
    retryable: bool,
    expected: dict[str, bool],
) -> None:
    response = _response(_row(status, retryable=retryable))

    assert response.actions.model_dump() == {
        **expected,
        "can_view_source": True,
    }


def test_failed_retryable_requires_durable_retryable_flag() -> None:
    response = _response(_row("FAILED_RETRYABLE", retryable=False))

    assert response.actions.can_retry is False
    assert response.actions.can_cancel is True


def test_client_reentry_distinguishes_uploaded_from_processing_without_raw_state() -> None:
    uploaded = _response(_row("UPLOADED"))
    processing = _response(_row("PROCESSING"))

    assert uploaded.status == processing.status == "processing"
    assert uploaded.actions.can_process is True
    assert processing.actions.can_process is False
    assert "internal_status" not in PatientExternalRecordResponse.model_fields
    assert "status" in PatientExternalRecordResponse.model_fields


def test_cancelled_does_not_advertise_idempotent_cancel_transport() -> None:
    cancelled = _response(_row("CANCELLED"))

    assert cancelled.actions.can_cancel is False


def test_source_capability_is_explicitly_advisory() -> None:
    assert "Advisory" in (
        PatientExternalRecordActions.model_fields["can_view_source"].description or ""
    )
    assert "does not guarantee" in (
        PatientExternalRecordResponse.model_fields["source_available"].description or ""
    )


def test_remote_upload_limit_uses_runtime_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(23 * MIB))
    monkeypatch.setattr(
        route_module,
        "get_document_extraction_config",
        lambda: SimpleNamespace(provider="remote"),
    )

    assert route_module._upload_limit() == 23 * MIB


def test_textract_upload_limit_clamps_to_sync_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = route_module.TEXTRACT_MAX_SYNC_BYTES + MIB
    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(configured))
    monkeypatch.setattr(
        route_module,
        "get_document_extraction_config",
        lambda: SimpleNamespace(provider="aws_textract"),
    )

    assert route_module._upload_limit() == route_module.TEXTRACT_MAX_SYNC_BYTES


def test_textract_upload_limit_keeps_lower_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = route_module.TEXTRACT_MAX_SYNC_BYTES - 1
    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(configured))
    monkeypatch.setattr(
        route_module,
        "get_document_extraction_config",
        lambda: SimpleNamespace(provider="aws_textract"),
    )

    assert route_module._upload_limit() == configured


@pytest.mark.asyncio
async def test_upload_policy_reflects_runtime_and_shared_validation_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(17 * MIB))
    monkeypatch.setattr(
        route_module,
        "get_document_extraction_config",
        lambda: SimpleNamespace(provider="remote"),
    )

    response = Response()
    policy = await read_external_record_upload_policy(
        response=response,
        auth=SimpleNamespace(patient_id=str(uuid.uuid4())),
    )

    assert policy.max_upload_bytes == 17 * MIB
    assert policy.accepted_extensions == PATIENT_UPLOAD_EXTENSIONS
    assert policy.accepted_mime_types == PATIENT_UPLOAD_MIME_TYPES
    assert policy.accepted_extensions == (".pdf", ".png", ".jpg", ".jpeg")
    assert policy.accepted_mime_types == (
        "application/pdf",
        "image/png",
        "image/jpeg",
    )
    assert response.headers["cache-control"] == "private, no-store"


def test_public_upload_formats_exactly_match_validator_policy() -> None:
    samples = {
        ".pdf": ("application/pdf", b"%PDF-1.4\n%%EOF\n"),
        ".png": (
            "image/png",
            b"\x89PNG\r\n\x1a\nsynthetic\x00\x00\x00\x00IEND\xaeB\x60\x82",
        ),
        ".jpg": ("image/jpeg", b"\xff\xd8\xffsynthetic\xff\xd9"),
        ".jpeg": ("image/jpeg", b"\xff\xd8\xffsynthetic\xff\xd9"),
    }

    assert set(samples) == set(PATIENT_UPLOAD_EXTENSIONS)
    for extension, (mime_type, data) in samples.items():
        safe_name, validated_mime = validate_patient_upload_type(
            f"record{extension}",
            mime_type,
            data,
        )
        assert safe_name == f"record{extension}"
        assert validated_mime == mime_type

    with pytest.raises(HTTPException) as tiff:
        validate_patient_upload_type(
            "record.tiff",
            "image/tiff",
            b"II*\x00synthetic",
        )
    assert tiff.value.status_code == 415
    assert tiff.value.detail["error_code"] == "UNSUPPORTED_DOCUMENT_TYPE"


def _route(path: str, method: str):
    for candidate in router.routes:
        methods = getattr(candidate, "methods", set()) or set()
        if getattr(candidate, "path", None) == path and method in methods:
            return candidate
    raise AssertionError(f"route not found: {method} {path}")


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


def test_upload_policy_authority_is_authenticated_patient_self_only() -> None:
    route = _route("/api/v2/patient/me/external-records/upload-policy", "GET")
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
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

    assert get_current_patient in dependency_calls
    assert forbidden.isdisjoint(_client_parameter_names(route))
