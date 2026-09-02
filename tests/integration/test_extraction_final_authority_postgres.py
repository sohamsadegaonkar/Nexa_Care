"""PostgreSQL proof for the extraction clinical-write authority boundary."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api.v2.pipeline_routes import CommitJobRequest, commit_extraction_job
from app.models.patient import Patient
from app.models.patient_records import TimelineEvent, Vitals
from app.models.pipeline import (
    DocumentStorage,
    ExtractedFieldRecord,
    ExtractionJob,
    PipelineCommit,
)
from app.models.provider import HospitalRegistry
from app.security.audit_context import AuditContext, AuditDomain

pytestmark = pytest.mark.postgres


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_extraction_final_authority_denial_after_preflight_writes_nothing() -> (
    None
):
    engine = create_async_engine(_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    patient_id, tenant_id, document_id, job_id, field_id = (
        uuid.uuid4() for _ in range(5)
    )
    provider_id = str(uuid.uuid4())
    workflow_id = str(uuid.uuid4())
    provider = SimpleNamespace(
        actor_uid=provider_id,
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    capability = SimpleNamespace(request_id=workflow_id)
    audit_context = AuditContext.for_tenant(
        tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
    )
    try:
        async with factory() as db:
            db.add(Patient(patient_uuid=patient_id))
            db.add(
                HospitalRegistry(
                    id=tenant_id,
                    facility_code=f"EF-{uuid.uuid4().hex[:20]}",
                    legal_name="Extraction final authority synthetic facility",
                    display_name="Extraction final authority synthetic facility",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.flush()
            document = DocumentStorage(
                id=document_id,
                patient_id=patient_id,
                tenant_id=tenant_id,
                uploader_id=provider_id,
                storage_ref=f"local://final-authority/{document_id}",
                content_type="application/pdf",
                size=1,
                content_hash="a" * 64,
                uploaded_at=datetime.now(timezone.utc),
            )
            db.add(document)
            await db.flush()
            job = ExtractionJob(
                id=job_id,
                patient_id=patient_id,
                tenant_id=tenant_id,
                uploader_id=provider_id,
                authorization_provider_id=provider_id,
                consent_request_id=workflow_id,
                document_id=document_id,
                document_type="application/pdf",
                status="review_pending",
                request_id=f"final-authority-{uuid.uuid4().hex}",
                created_at=datetime.now(timezone.utc),
            )
            db.add(job)
            await db.flush()
            db.add(
                ExtractedFieldRecord(
                    id=field_id,
                    job_id=job_id,
                    patient_id=patient_id,
                    field_name="blood_pressure",
                    raw_value="120 over 80",
                    units="mmHg",
                    confidence=0.91,
                    risk_level="MEDIUM_RISK",
                    status="approved",
                    source_document_id=document_id,
                )
            )
            await db.commit()

        revoked = HTTPException(
            status_code=403,
            detail={"error_code": "DOCUMENT_PROCESSING_ACCESS_REQUIRED"},
        )
        authorization = AsyncMock(side_effect=[capability, capability, revoked])
        async with factory() as db:
            with (
                patch(
                    "app.api.v2.pipeline_routes.authorize_document_processing",
                    authorization,
                ),
                patch("app.api.v2.pipeline_routes.assert_job_authorization_binding"),
                patch(
                    "app.api.v2.pipeline_routes.current_audit_context",
                    return_value=audit_context,
                ),
                patch(
                    "app.api.v2.pipeline_routes.enforce_current_clinical_capability",
                    AsyncMock(return_value=provider),
                ),
                patch(
                    "app.api.v2.pipeline_routes.check_erasure_registry",
                    AsyncMock(return_value=None),
                ),
            ):
                with pytest.raises(HTTPException) as denial:
                    await commit_extraction_job(
                        Request({"type": "http", "method": "POST", "path": "/"}),
                        str(job_id),
                        CommitJobRequest(patient_id=str(patient_id)),
                        provider,
                        "synthetic-consent-capability",
                        db,
                    )
            assert denial.value.status_code == 403
            assert authorization.await_count == 3

        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(Vitals.id)).where(Vitals.patient_id == patient_id)
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    select(func.count(TimelineEvent.id)).where(
                        TimelineEvent.patient_id == patient_id
                    )
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    select(func.count(PipelineCommit.job_id)).where(
                        PipelineCommit.job_id == job_id
                    )
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM public.audit_outbox "
                        "WHERE patient_id = :patient_id"
                    ),
                    {"patient_id": str(patient_id)},
                )
            ).scalar_one() == 0
            durable_job = (
                await db.execute(
                    select(ExtractionJob).where(ExtractionJob.id == job_id)
                )
            ).scalar_one()
            assert durable_job.status == "review_pending"
    finally:
        await engine.dispose()
