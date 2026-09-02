"""Disposable PostgreSQL enforcement proof for delegated initiation assurance."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.document_processing_gate import (
    DelegatedClinicalTrustError,
    DelegatedClinicalTrustQuarantineUnavailable,
    quarantine_delegated_clinical_trust_denial,
    recheck_delegated_document_processing_trust,
)
from app.models.pipeline import DocumentStorage, ExtractionJob
from app.models.provider import HospitalRegistry
from app.models.patient import Patient
from app.services.pipeline_orchestrator import process_extraction_job

pytestmark = pytest.mark.postgres


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _document(
    *, patient_id: uuid.UUID, tenant_id: uuid.UUID, ordinal: int
) -> DocumentStorage:
    return DocumentStorage(
        id=uuid.uuid4(),
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="MIGRATION_SYNTHETIC",
        storage_ref=f"local://migration-assurance/{ordinal}",
        content_type="application/pdf",
        size=1,
        content_hash=(str(ordinal) * 64)[:64],
        uploaded_at=datetime.now(timezone.utc),
    )


def _job(document: DocumentStorage, **assurance) -> ExtractionJob:
    return ExtractionJob(
        id=uuid.uuid4(),
        patient_id=document.patient_id,
        tenant_id=document.tenant_id,
        uploader_id="MIGRATION_SYNTHETIC",
        document_id=document.id,
        document_type="application/pdf",
        status="queued",
        request_id=f"migration-assurance-{uuid.uuid4().hex}",
        created_at=datetime.now(timezone.utc),
        **assurance,
    )


@pytest.mark.asyncio
async def test_delegated_assurance_tuple_is_all_or_nothing_on_postgresql() -> None:
    """Legacy all-null rows remain readable; partial new provenance is rejected."""

    engine = create_async_engine(_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    patient_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as db:
            db.add(Patient(patient_uuid=patient_id))
            db.add(
                HospitalRegistry(
                    id=tenant_id,
                    facility_code=f"DA-{uuid.uuid4().hex[:20]}",
                    legal_name="Delegated assurance synthetic facility",
                    display_name="Delegated assurance synthetic facility",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.flush()
            legacy_document = _document(
                patient_id=patient_id, tenant_id=tenant_id, ordinal=1
            )
            db.add(legacy_document)
            db.add(_job(legacy_document))
            await db.commit()

            partial_document = _document(
                patient_id=patient_id, tenant_id=tenant_id, ordinal=2
            )
            db.add(partial_document)
            db.add(
                _job(
                    partial_document,
                    authorization_initiated_at=datetime.now(timezone.utc),
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

            complete_document = _document(
                patient_id=patient_id, tenant_id=tenant_id, ordinal=3
            )
            now = datetime.now(timezone.utc)
            db.add(complete_document)
            complete_job = _job(
                complete_document,
                authorization_initiated_at=now,
                authorization_authentication_method="PROVIDER_SESSION",
                authorization_mfa_verified_at=now,
                authorization_assurance_policy_version=(
                    "clinical-contact-email-and-phone/v1"
                ),
            )
            db.add(complete_job)
            await db.commit()

            quarantined = await quarantine_delegated_clinical_trust_denial(
                db=db,
                job_id=complete_job.id,
                error_code="PROFESSIONAL_SUSPENDED",
            )
            assert quarantined.status == "quarantined"
            event_type, payload = (
                await db.execute(
                    text(
                        "SELECT event_type, payload FROM public.audit_outbox "
                        "WHERE idempotency_key LIKE :prefix"
                    ),
                    {"prefix": f"delegated-clinical-trust:{complete_job.id}:%"},
                )
            ).one()
            assert event_type == "DELEGATED_CLINICAL_TRUST_DENIED"
            assert isinstance(payload, dict)
            metadata = payload["metadata"]
            assert metadata["error_code"] == "PROFESSIONAL_SUSPENDED"
            assert "patient_id" not in metadata
            assert "session" not in metadata

            original_completed_at = quarantined.completed_at
            with pytest.raises(DelegatedClinicalTrustError) as later_denial:
                await recheck_delegated_document_processing_trust(
                    job=quarantined, db=db
                )
            assert later_denial.value.code == "DELEGATED_WORKFLOW_STATE_INVALID"

            preserved = await quarantine_delegated_clinical_trust_denial(
                db=db,
                job_id=complete_job.id,
                error_code=later_denial.value.code,
            )
            assert preserved.status == "quarantined"
            assert preserved.error_code == "PROFESSIONAL_SUSPENDED"
            assert preserved.completed_at == original_completed_at
            assert preserved.retryable is False
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM public.audit_outbox "
                        "WHERE idempotency_key LIKE :prefix"
                    ),
                    {"prefix": f"delegated-clinical-trust:{complete_job.id}:%"},
                )
            ).scalar_one() == 1

            with (
                patch(
                    "app.services.pipeline_orchestrator.get_document_storage"
                ) as storage_factory,
                patch(
                    "app.services.pipeline_orchestrator.AsyncTextractProvider"
                ) as provider_factory,
            ):
                replay = await process_extraction_job(str(complete_job.id), db)
            assert replay == {
                "job_id": str(complete_job.id),
                "status": "quarantined",
                "idempotent": True,
            }
            storage_factory.assert_not_called()
            provider_factory.assert_not_called()

            rollback_document = _document(
                patient_id=patient_id, tenant_id=tenant_id, ordinal=4
            )
            db.add(rollback_document)
            rollback_job = _job(
                rollback_document,
                authorization_initiated_at=now,
                authorization_authentication_method="PROVIDER_SESSION",
                authorization_mfa_verified_at=now,
                authorization_assurance_policy_version=(
                    "clinical-contact-email-and-phone/v1"
                ),
            )
            db.add(rollback_job)
            await db.commit()
            rollback_job_id = rollback_job.id

            with patch(
                "app.core.document_processing_gate.enqueue_audit_event",
                AsyncMock(side_effect=RuntimeError("forced synthetic outbox failure")),
            ):
                with pytest.raises(DelegatedClinicalTrustQuarantineUnavailable):
                    await quarantine_delegated_clinical_trust_denial(
                        db=db,
                        job_id=rollback_job_id,
                        error_code="PROFESSIONAL_SUSPENDED",
                    )

            durable_status, durable_error, durable_completed_at = (
                await db.execute(
                    text(
                        "SELECT status, error_code, completed_at "
                        "FROM extraction_jobs WHERE id = :job_id"
                    ),
                    {"job_id": rollback_job_id},
                )
            ).one()
            assert durable_status == "queued"
            assert durable_error is None
            assert durable_completed_at is None
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM public.audit_outbox "
                        "WHERE idempotency_key LIKE :prefix"
                    ),
                    {"prefix": f"delegated-clinical-trust:{rollback_job_id}:%"},
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()
