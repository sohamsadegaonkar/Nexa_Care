from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.routing import APIRoute

from app.api.v2.pipeline_routes import (
    ExtractionJobStatusResponse,
    get_extraction_job,
    router,
)
from app.services.crypto_kms import EncryptedField


def _status_response() -> ExtractionJobStatusResponse:
    return ExtractionJobStatusResponse(
        job_id="00000000-0000-0000-0000-000000000001",
        patient_id="00000000-0000-0000-0000-000000000002",
        status="source_only",
        document_type="lab_report",
        provider="aws_textract",
        provider_version="queries-v1",
        document_confidence=None,
        routing_lane="SOURCE_ONLY",
        candidate_count=0,
        identity_validation="passed",
        created_at="2026-08-02T00:00:00+00:00",
    )


def test_job_status_response_serializes_safe_empty_array_defaults() -> None:
    first = _status_response()
    second = _status_response()

    assert first.model_dump()["routing_reasons"] == []
    assert first.model_dump()["extracted_fields"] == []
    assert first.model_dump()["candidates"] == []
    assert first.routing_reasons is not second.routing_reasons
    assert first.extracted_fields is not second.extracted_fields


def test_job_status_route_enforces_the_typed_response_model() -> None:
    route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v2/pipeline/jobs/{job_id}"
        and "GET" in route.methods
    )

    assert route.response_model is ExtractionJobStatusResponse


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _DB:
    def __init__(self, job, candidates):
        self.results = [_Result(job), _Result(candidates)]

    async def execute(self, _statement):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_status_suppresses_classification_failure_values_and_counts_reason():
    patient_id = "00000000-0000-0000-0000-000000000001"
    tenant_id = "00000000-0000-0000-0000-000000000002"
    document_id = "00000000-0000-0000-0000-000000000003"
    job = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000004",
        tenant_id=tenant_id,
        patient_id=patient_id,
        document_id=document_id,
        consent_request_id=None,
        status="quarantined",
        error_code=None,
        document_type="lab_report",
        extractor_provider="aws_textract",
        extractor_version="test",
        created_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    provider = SimpleNamespace(
        actor_uid="provider-1",
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    ineligible = SimpleNamespace(
        routing_eligible=False,
        eligibility_reason_code="INELIGIBLE_CLASSIFICATION_FAILED",
        job_id=job.id,
        source_document_id=document_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        authorization_provider_id=provider.actor_uid,
        lane="QUARANTINE",
        reason_codes=["ELIGIBILITY_CLASSIFICATION_FAILED"],
        document_confidence=None,
    )
    capability = SimpleNamespace(patient_id=patient_id)
    with (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing",
            AsyncMock(return_value=capability),
        ),
        patch(
            "app.api.v2.pipeline_routes.assert_job_authorization_binding",
        ),
    ):
        response = await get_extraction_job(
            str(job.id),
            provider=provider,
            x_consent_token=None,
            db=_DB(job, [ineligible]),
        )

    assert response["candidates"] == []
    assert response["ineligible_candidate_count"] == 1
    assert response["ineligible_count_by_reason"] == {
        "INELIGIBLE_CLASSIFICATION_FAILED": 1
    }


@pytest.mark.asyncio
async def test_identity_quarantine_suppresses_retained_candidate_without_decryption():
    patient_id = "00000000-0000-0000-0000-000000000011"
    tenant_id = "00000000-0000-0000-0000-000000000012"
    document_id = "00000000-0000-0000-0000-000000000013"
    job = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000014",
        tenant_id=tenant_id,
        patient_id=patient_id,
        document_id=document_id,
        consent_request_id=None,
        status="quarantined",
        error_code="EXTRACTED_IDENTITY_MISMATCH",
        document_type="lab_report",
        extractor_provider="aws_textract",
        extractor_version="test",
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    provider = SimpleNamespace(
        actor_uid="provider-1",
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    retained = SimpleNamespace(
        routing_eligible=True,
        eligibility_reason_code=None,
        job_id=job.id,
        source_document_id=document_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        authorization_provider_id=provider.actor_uid,
        lane="QUARANTINE",
        reason_codes=["IDENTITY_MISMATCH"],
        document_confidence=0.91,
    )
    capability = SimpleNamespace(patient_id=patient_id)
    kms = SimpleNamespace(decrypt_field=AsyncMock())
    kms_factory = Mock(return_value=kms)
    with (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing",
            AsyncMock(return_value=capability),
        ),
        patch("app.api.v2.pipeline_routes.assert_job_authorization_binding"),
        patch(
            "app.api.v2.pipeline_routes.get_encryption_provider", kms_factory
        ),
    ):
        response = await get_extraction_job(
            str(job.id),
            provider=provider,
            x_consent_token=None,
            db=_DB(job, [retained]),
        )

    assert response["status"] == "quarantined"
    assert response["routing_lane"] == "QUARANTINE"
    assert response["routing_reasons"] == ["IDENTITY_MISMATCH"]
    assert response["candidate_count"] == 1
    assert response["eligible_candidate_count"] == 1
    assert response["candidates"] == []
    assert response["identity_validation"] == "failed"
    kms_factory.assert_not_called()
    assert kms.decrypt_field.await_count == 0


@pytest.mark.parametrize(
    "identity_reason", ["IDENTITY_MISMATCH", "IDENTITY_UNAVAILABLE"]
)
@pytest.mark.asyncio
async def test_candidate_identity_reason_blocks_stale_source_only_value_visibility(
    identity_reason,
):
    patient_id = "00000000-0000-0000-0000-000000000021"
    tenant_id = "00000000-0000-0000-0000-000000000022"
    document_id = "00000000-0000-0000-0000-000000000023"
    job = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000024",
        tenant_id=tenant_id,
        patient_id=patient_id,
        document_id=document_id,
        consent_request_id=None,
        status="source_only",
        error_code=None,
        document_type="lab_report",
        extractor_provider="aws_textract",
        extractor_version="test",
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    provider = SimpleNamespace(
        actor_uid="provider-1",
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    evidence_id = "00000000-0000-0000-0000-000000000025"
    encrypted_value = EncryptedField(
        ciphertext=b"retained-ciphertext",
        iv=b"0" * 12,
        field_name=f"extraction_candidate_value:{evidence_id}",
        dek_version=1,
        algorithm="AES-256-GCM",
    ).serialize()
    retained = SimpleNamespace(
        evidence_id=evidence_id,
        routing_eligible=True,
        eligibility_reason_code=None,
        job_id=job.id,
        source_document_id=document_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        authorization_provider_id=provider.actor_uid,
        lane="QUARANTINE",
        reason_codes=[identity_reason],
        encrypted_raw_value=encrypted_value,
        encrypted_source_text=None,
        document_confidence=0.91,
    )
    capability = SimpleNamespace(patient_id=patient_id)
    kms = SimpleNamespace(
        decrypt_field=AsyncMock(return_value="must-not-be-visible")
    )
    kms_factory = Mock(return_value=kms)
    with (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing",
            AsyncMock(return_value=capability),
        ),
        patch("app.api.v2.pipeline_routes.assert_job_authorization_binding"),
        patch(
            "app.api.v2.pipeline_routes.get_encryption_provider", kms_factory
        ),
    ):
        response = await get_extraction_job(
            str(job.id),
            provider=provider,
            x_consent_token=None,
            db=_DB(job, [retained]),
        )

    assert response["candidates"] == []
    assert response["candidate_count"] == 1
    assert response["eligible_candidate_count"] == 1
    assert response["ineligible_candidate_count"] == 0
    assert response["routing_lane"] == "QUARANTINE"
    assert identity_reason in response["routing_reasons"]
    assert response["identity_validation"] == "failed"
    kms_factory.assert_not_called()
    assert kms.decrypt_field.await_count == 0
