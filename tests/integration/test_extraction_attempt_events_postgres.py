"""Real PostgreSQL qualification for immutable provider subattempt history."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.ai_models import ProviderAttemptOutcome, ProviderAttemptTrace
from app.models.pipeline import DocumentStorage, ExtractionJob
from app.models.provider import HospitalRegistry
from app.services.extraction_attempt_history import (
    event_id_for,
    persist_provider_attempt_events,
)


pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_provider_attempt_events_are_exact_value_free_and_immutable() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, document_id, job_id = (uuid.uuid4() for _ in range(4))
    trace = ProviderAttemptTrace(
        provider_subattempt_number=1,
        provider_adapter="qualification",
        provider_contract_version="qualification/1.0",
        provider_model_version="X",
        outcome=ProviderAttemptOutcome.INVALID_RESPONSE,
        error_code="EXTRACTION_RESPONSE_INVALID",
        response_complete=False,
        occurred_at=datetime.now(timezone.utc),
    )
    event_id = event_id_for(
        job_id=job_id, job_attempt_number=1, provider_subattempt_number=1
    )
    try:
        async with factory() as db:
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:patient)"),
                {"patient": patient},
            )
            db.add(
                HospitalRegistry(
                    id=tenant,
                    facility_code=f"B1-{uuid.uuid4().hex[:12]}",
                    legal_name="B1 synthetic qualification facility",
                    display_name="B1 synthetic qualification facility",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.flush()
            db.add(
                DocumentStorage(
                    id=document_id,
                    patient_id=patient,
                    tenant_id=tenant,
                    uploader_id="qualification-provider",
                    storage_ref=f"qualification-{document_id}",
                    content_type="application/pdf",
                    size=32,
                    content_hash=uuid.uuid4().hex * 2,
                    original_filename=None,
                    upload_purpose="qualification",
                    consent_session_id="qualification-consent",
                    source_system="SYNTHETIC_POLICY_FIXTURE",
                    uploaded_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                ExtractionJob(
                    id=job_id,
                    patient_id=patient,
                    tenant_id=tenant,
                    uploader_id="qualification-provider",
                    authorization_provider_id="qualification-provider",
                    consent_request_id="qualification-consent",
                    document_id=document_id,
                    document_type="application/pdf",
                    status="extracting",
                    request_id=f"qualification-{uuid.uuid4().hex}",
                    attempt_count=1,
                    retryable=True,
                    version=1,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            job = await db.execute(
                text("SELECT id FROM public.extraction_jobs WHERE id = :job"),
                {"job": job_id},
            )
            assert job.scalar_one() == job_id
            await persist_provider_attempt_events(
                db,
                job=(await db.get(ExtractionJob, job_id, with_for_update=True)),
                traces=(trace,),
            )
            await persist_provider_attempt_events(
                db,
                job=(await db.get(ExtractionJob, job_id, with_for_update=True)),
                traces=(trace,),
            )
            row = (
                await db.execute(
                    text(
                        "SELECT provider_adapter, provider_contract_version, "
                        "provider_model_version, outcome, error_code, response_complete "
                        "FROM public.extraction_attempt_events WHERE id = :id"
                    ),
                    {"id": event_id},
                )
            ).one()
            assert row == (
                "qualification",
                "qualification/1.0",
                "X",
                "INVALID_RESPONSE",
                "EXTRACTION_RESPONSE_INVALID",
                False,
            )
            await db.commit()

        other_tenant, other_patient, other_document_id = (
            uuid.uuid4() for _ in range(3)
        )
        async with factory() as db:
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:patient)"),
                {"patient": other_patient},
            )
            db.add(
                HospitalRegistry(
                    id=other_tenant,
                    facility_code=f"B1-OTHER-{uuid.uuid4().hex[:8]}",
                    legal_name="B1 synthetic qualification facility other",
                    display_name="B1 synthetic qualification facility other",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.flush()
            db.add(
                DocumentStorage(
                    id=other_document_id,
                    patient_id=other_patient,
                    tenant_id=other_tenant,
                    uploader_id="qualification-provider",
                    storage_ref=f"qualification-{other_document_id}",
                    content_type="application/pdf",
                    size=32,
                    content_hash=uuid.uuid4().hex * 2,
                    original_filename=None,
                    upload_purpose="qualification",
                    consent_session_id="qualification-consent",
                    source_system="SYNTHETIC_POLICY_FIXTURE",
                    uploaded_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        mismatch_rows = (
            (tenant, other_patient, document_id),
            (other_tenant, patient, document_id),
            (tenant, patient, other_document_id),
            (other_tenant, other_patient, other_document_id),
        )
        for subattempt, (event_tenant, event_patient, event_document) in enumerate(
            mismatch_rows, start=2
        ):
            async with factory() as db:
                with pytest.raises(DBAPIError) as mismatch:
                    await db.execute(
                        text(
                            "INSERT INTO public.extraction_attempt_events "
                            "(id, tenant_id, patient_id, job_id, source_document_id, "
                            "job_attempt_number, provider_subattempt_number, "
                            "provider_adapter, provider_contract_version, outcome, "
                            "error_code, response_complete, occurred_at) "
                            "VALUES (:id, :tenant, :patient, :job, :document, 1, "
                            ":subattempt, 'qualification', 'qualification/1.0', "
                            "'INVALID_RESPONSE', 'EXTRACTION_RESPONSE_INVALID', false, :now)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "tenant": event_tenant,
                            "patient": event_patient,
                            "job": job_id,
                            "document": event_document,
                            "subattempt": subattempt,
                            "now": datetime.now(timezone.utc),
                        },
                    )
                assert getattr(mismatch.value.orig, "sqlstate", None) == "23514"
                await db.rollback()

        for statement in (
            "UPDATE public.extraction_attempt_events "
            "SET occurred_at = occurred_at WHERE id = :id",
            "DELETE FROM public.extraction_attempt_events WHERE id = :id",
        ):
            async with factory() as db:
                with pytest.raises(DBAPIError) as immutable:
                    await db.execute(text(statement), {"id": event_id})
                assert getattr(immutable.value.orig, "sqlstate", None) == "55000"
                await db.rollback()
    finally:
        await engine.dispose()
