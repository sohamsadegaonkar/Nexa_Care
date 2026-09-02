"""Route-level clinical trust wiring regressions for Provider Trust Slice 2."""

from __future__ import annotations

import inspect
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.params import Depends
from starlette.requests import Request

from app.api.v2 import (
    consent_routes,
    fhir_routes,
    nfc_routes,
    patient_discovery_routes,
    patient_record_routes,
    patient_routes,
    pipeline_routes,
)
from app.core.dependencies import (
    get_provider_context,
    get_scoped_session,
    require_clinical_capability,
)
from app.security.provider_capabilities import ClinicalCapability


def _provider_dependency(endpoint) -> object:
    default = inspect.signature(endpoint).parameters["provider"].default
    assert isinstance(default, Depends)
    return default.dependency


def _clinical_capability(endpoint) -> ClinicalCapability:
    dependency = _provider_dependency(endpoint)
    assert dependency is not None
    values = [cell.cell_contents for cell in (dependency.__closure__ or ())]
    return next(value for value in values if isinstance(value, ClinicalCapability))


def test_routine_interactive_routes_use_exact_typed_capabilities() -> None:
    expected = {
        nfc_routes.resolve_nfc_card: ClinicalCapability.PATIENT_DISCOVER,
        patient_discovery_routes.discover_patient: ClinicalCapability.PATIENT_DISCOVER,
        consent_routes.create_consent_request: ClinicalCapability.CONSENT_REQUEST,
        consent_routes.get_consent_request_status: ClinicalCapability.CONSENT_REQUEST,
        consent_routes.claim_approved_access: ClinicalCapability.CONSENT_REQUEST,
        patient_routes.reconstruct_patient_record: ClinicalCapability.RECORD_READ,
        fhir_routes.export_fhir_bundle: ClinicalCapability.RECORD_READ,
        patient_routes.get_emergency_summary: ClinicalCapability.EMERGENCY_ATTEMPT,
        consent_routes.issue_break_glass_consent_route: ClinicalCapability.EMERGENCY_ATTEMPT,
    }
    assert {
        endpoint: _clinical_capability(endpoint) for endpoint in expected
    } == expected


def test_record_read_routes_do_not_fall_back_to_legacy_role_auth() -> None:
    for endpoint in (
        patient_record_routes.get_patient_summary,
        patient_record_routes.get_patient_timeline,
        patient_record_routes.get_patient_structured_record,
    ):
        assert _clinical_capability(endpoint) is ClinicalCapability.RECORD_READ


def test_document_operations_use_typed_review_commit_and_upload_capabilities() -> None:
    expected = {
        pipeline_routes.upload_pipeline_document: ClinicalCapability.DOCUMENTS_UPLOAD,
        pipeline_routes.get_extraction_job: ClinicalCapability.DOCUMENTS_REVIEW,
        pipeline_routes.get_extraction_job_document: ClinicalCapability.DOCUMENTS_REVIEW,
        pipeline_routes.get_review_queue: ClinicalCapability.DOCUMENTS_REVIEW,
        pipeline_routes.review_extracted_field: ClinicalCapability.DOCUMENTS_REVIEW,
        pipeline_routes.approve_extracted_field: ClinicalCapability.DOCUMENTS_REVIEW,
        pipeline_routes.reject_extracted_field: ClinicalCapability.DOCUMENTS_REVIEW,
        pipeline_routes.edit_extracted_field: ClinicalCapability.DOCUMENTS_REVIEW,
        pipeline_routes.commit_extraction_job: ClinicalCapability.DOCUMENTS_COMMIT,
        pipeline_routes.commit_human_adjudication: ClinicalCapability.DOCUMENTS_COMMIT,
    }
    assert {
        endpoint: _clinical_capability(endpoint) for endpoint in expected
    } == expected


def test_generic_and_patient_paths_keep_their_separate_authority_models() -> None:
    assert _provider_dependency(consent_routes.validate_consent) is get_provider_context
    assert (
        _provider_dependency(
            consent_routes.revoke_break_glass_consent_route
        ).__qualname__
        == "require_role.<locals>._require_role"
    )
    patient_default = (
        inspect.signature(consent_routes.approve_signed_consent)
        .parameters["patient_id"]
        .default
    )
    assert isinstance(patient_default, Depends)
    assert patient_default.dependency is get_scoped_session


def test_clinical_gate_rejects_basic_auth_before_any_clinical_lookup() -> None:
    gate = require_clinical_capability(ClinicalCapability.RECORD_READ)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Basic Zm9vOmJhcg==")],
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(gate(request=request, provider=object(), db=object()))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == {"error_code": "CLINICAL_SESSION_REQUIRED"}


def test_final_commit_paths_use_the_central_reauthentication_enforcer() -> None:
    pipeline_source = Path(pipeline_routes.__file__).read_text(encoding="utf-8")
    adjudication_source = (
        Path(pipeline_routes.__file__)
        .parents[2]
        .joinpath("services", "adjudication.py")
        .read_text(encoding="utf-8")
    )
    commit = pipeline_source[pipeline_source.index("async def commit_extraction_job") :]
    human_commit = pipeline_source[
        pipeline_source.index("async def commit_human_adjudication") :
    ]

    assert "await enforce_current_clinical_capability(" in commit
    assert commit.index("await enforce_current_clinical_capability(") < commit.index(
        "await ingest_extracted_fields("
    )
    assert commit.rindex("await authorize_document_processing(") < commit.index(
        "await ingest_extracted_fields("
    )
    assert commit.index("await check_erasure_registry(") < commit.index(
        "await ingest_extracted_fields("
    )
    assert "before_clinical_mutation=_revalidate_clinical_commit_authority" in commit
    assert commit.rindex("await _revalidate_clinical_commit_authority()") < (
        commit.index("await db.commit()")
    )
    assert "before_clinical_mutation=_final_commit_check" in human_commit
    service_commit = adjudication_source[
        adjudication_source.index("async def commit_submission") :
    ]
    final_check = service_commit.index(
        "current_provider = await before_clinical_mutation()"
    )
    assert (
        service_commit.index("record = await _existing_vital(")
        < final_check
        < service_commit.index("provider=current_provider")
        < service_commit.index("db.add(record)")
    )
    assert service_commit.count("await before_clinical_mutation()") == 2
