"""Patient-bound, fail-closed extraction job orchestration."""

from __future__ import annotations

from app.security.audit_context import AuditContext, AuditDomain

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extractor import DocumentExtractionError, get_medical_document_extractor
from app.core.config import get_document_extraction_config
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.models.pipeline import ExtractionJob
from app.models.shards import NexaVault
from app.observability.audit_ledger import append_audit_log_or_503
from app.observability.safe_exceptions import log_safe_exception
from app.services.document_storage import DocumentStorageError, get_document_storage
from app.services.extraction_evidence_adapter import (
    CurrentExtractionBinding,
    adapt_current_extracted_field,
)
from app.services.crypto_kms import (
    EncryptedField,
    EncryptionError,
    get_encryption_provider,
)

logger = logging.getLogger("nexa_logger")


class ExtractedIdentityMismatch(RuntimeError):
    pass


class IncompleteFieldEvidence(RuntimeError):
    """Current provider output cannot safely enter staging persistence."""


async def _validate_extracted_identity(
    extracted: Any, patient_id: uuid.UUID, db: AsyncSession
) -> None:
    """Quarantine non-empty OCR identity that disagrees with the bound patient."""
    extracted_values = {
        "patient_name": str(extracted.patient_name or "").strip(),
        "phone": str(extracted.phone or "").strip(),
        "aadhaar_abha_id": str(extracted.aadhaar_abha_id or "").strip(),
    }
    supplied = {name: value for name, value in extracted_values.items() if value}
    if not supplied:
        return
    row = (
        await db.execute(
            select(NexaVault)
            .where(NexaVault.masked_internal_id == str(patient_id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExtractedIdentityMismatch()
    kms = get_encryption_provider()
    for field_name, extracted_value in supplied.items():
        stored = getattr(row, field_name, None)
        if not stored:
            raise ExtractedIdentityMismatch()
        canonical = await kms.decrypt_field(
            str(patient_id),
            field_name,
            EncryptedField.deserialize(stored, field_name),
            db,
        )

        def normalize(value: str) -> str:
            return re.sub(r"[^a-z0-9]", "", value.casefold())

        if not secrets.compare_digest(normalize(canonical), normalize(extracted_value)):
            raise ExtractedIdentityMismatch()


def _candidate_fields(document: Any) -> list[dict[str, str]]:
    """Convert clinical arrays only; OCR identity is never a chart candidate."""

    candidates: list[dict[str, str]] = []
    candidates.extend(
        {"field_name": "diagnosis", "raw_value": str(value)}
        for value in document.diagnoses
    )
    candidates.extend(
        {"field_name": "lab_result", "raw_value": str(value)}
        for value in document.lab_results
    )
    candidates.extend(
        {"field_name": "medication", "raw_value": str(value)}
        for value in document.prescriptions
    )
    return candidates


async def process_extraction_job(job_id: str, db: AsyncSession) -> dict[str, Any]:
    try:
        job_uuid = uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return {"status": "extraction_failed_terminal", "error_code": "INVALID_JOB_ID"}

    job = (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_uuid).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return {"status": "extraction_failed_terminal", "error_code": "JOB_NOT_FOUND"}
    audit_context = AuditContext.for_tenant(
        tenant_id=str(job.tenant_id),
        domain=AuditDomain.PIPELINE,
    )
    if job.status in {
        "extracted",
        "validation_pending",
        "review_pending",
        "ready_for_commit",
        "committed",
    }:
        return {"job_id": str(job.id), "status": job.status, "idempotent": True}
    now = datetime.now(timezone.utc)
    if job.status == "extracting" and isinstance(job.processing_started_at, datetime):
        started = job.processing_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if now - started < timedelta(minutes=15):
            return {"job_id": str(job.id), "status": "extracting", "idempotent": True}

    job.status = "extracting"
    job.processing_started_at = now
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.error_code = None
    job.retryable = False
    await db.commit()

    await append_audit_log_or_503(
        audit_context=audit_context,
        actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
        event_type="EXTRACTION_JOB_STARTED",
        target_id=str(job.id),
        status="STARTED",
        metadata={
            "document_id": str(job.document_id),
            "patient_id": str(job.patient_id),
            "tenant_id": str(job.tenant_id) if job.tenant_id else None,
            "request_id": job.request_id,
            "attempt_count": job.attempt_count,
        },
    )

    try:
        document = (
            await db.execute(
                select(DocumentStorageRecord).where(
                    DocumentStorageRecord.id == job.document_id
                )
            )
        ).scalar_one_or_none()
        if document is None or job.tenant_id is None:
            raise DocumentStorageError("Document metadata unavailable")
        storage = get_document_storage()
        document_bytes = await storage.get_document_bytes(
            document.storage_ref,
            tenant_id=str(job.tenant_id),
            patient_id=str(job.patient_id),
        )
        config = get_document_extraction_config()
        job.extractor_provider = config.provider
        extractor = get_medical_document_extractor()
        extracted = await extractor.extract_bytes(
            document_bytes,
            mime_type=document.content_type,
            request_id=job.request_id or str(job.id),
        )
        del document_bytes
        await _validate_extracted_identity(extracted, job.patient_id, db)

        job.status = "extracted"
        await db.flush()
        job.status = "validation_pending"
        candidates = _candidate_fields(extracted)
        extracted_at = datetime.now(timezone.utc)
        evidence_records = []
        for item in candidates:
            evidence_records.append(
                adapt_current_extracted_field(
                    document=extracted,
                    field_name=item["field_name"],
                    raw_value=item["raw_value"],
                    binding=CurrentExtractionBinding(
                        patient_id=str(job.patient_id),
                        tenant_id=str(job.tenant_id),
                        organization_id=str(job.tenant_id),
                        source_document_id=str(job.document_id),
                        source_document_hash=document.content_hash,
                        ingestion_id=str(document.id),
                        job_id=str(job.id),
                        workflow_id=job.consent_request_id,
                        request_id=job.request_id,
                        attempt_number=job.attempt_count,
                        attempt_id=f"{job.id}:{job.attempt_count}",
                        created_at=job.created_at,
                        extracted_at=extracted_at,
                        source_received_at=document.uploaded_at,
                        provider_name=config.provider,
                        model_name=None,
                        model_version=job.extractor_version,
                        consent_reference=job.consent_request_id,
                    ),
                )
            )

        # The staging schema requires a numeric field confidence. The current
        # provider exposes document confidence only, so writing these candidates
        # would require fabricating confidence. Fail before staging persistence;
        # a later milestone may route this immutable evidence without changing it.
        if any(
            not evidence.model.has_genuine_field_confidence
            for evidence in evidence_records
        ):
            raise IncompleteFieldEvidence()

        review_count = 0
        job.status = "ready_for_commit"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await append_audit_log_or_503(
            audit_context=audit_context,
            actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
            event_type="EXTRACTION_JOB_VALIDATED",
            target_id=str(job.id),
            status="SUCCESS",
            metadata={
                "patient_id": str(job.patient_id),
                "tenant_id": str(job.tenant_id),
                "request_id": job.request_id,
                "review_count": review_count,
            },
        )
        return {
            "job_id": str(job.id),
            "status": job.status,
            "needs_review_count": review_count,
        }
    except ExtractedIdentityMismatch:
        job.status = "identity_mismatch"
        job.error_code = "EXTRACTED_IDENTITY_MISMATCH"
        job.retryable = False
    except IncompleteFieldEvidence:
        job.status = "validation_failed"
        job.error_code = "FIELD_EVIDENCE_INCOMPLETE"
        job.retryable = False
    except EncryptionError:
        job.status = "validation_failed"
        job.error_code = "IDENTITY_VALIDATION_UNAVAILABLE"
        job.retryable = False
    except DocumentExtractionError as exc:
        retry_budget = get_document_extraction_config().max_attempts
        exhausted = exc.retryable and job.attempt_count >= retry_budget
        job.status = (
            "quarantined"
            if exhausted
            else "extraction_failed_retryable"
            if exc.retryable
            else "extraction_failed_terminal"
        )
        job.error_code = exc.error_code
        job.retryable = exc.retryable and not exhausted
    except DocumentStorageError:
        job.status = "extraction_failed_terminal"
        job.error_code = "DOCUMENT_STORAGE_UNAVAILABLE"
        job.retryable = False
    except Exception as exc:
        log_safe_exception(
            logger,
            exc,
            subsystem="extraction",
            operation="extraction_job_processing",
            fields={"job_id": str(job.id), "request_id": job.request_id},
        )
        job.status = "extraction_failed_terminal"
        job.error_code = "EXTRACTION_INTERNAL_ERROR"
        job.retryable = False

    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await append_audit_log_or_503(
        audit_context=audit_context,
        actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
        event_type="EXTRACTION_JOB_FAILED",
        target_id=str(job.id),
        status="FAILED",
        metadata={
            "patient_id": str(job.patient_id),
            "tenant_id": str(job.tenant_id) if job.tenant_id else None,
            "request_id": job.request_id,
            "error_code": job.error_code,
            "retryable": job.retryable,
            "attempt_count": job.attempt_count,
        },
    )
    return {
        "job_id": str(job.id),
        "status": job.status,
        "error_code": job.error_code,
        "retryable": job.retryable,
    }
