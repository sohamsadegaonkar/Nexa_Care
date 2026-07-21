"""Patient-bound, fail-closed extraction job orchestration."""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extractor import DocumentExtractionError, get_medical_document_extractor
from app.ai.scoring_engine import score_extracted_field
from app.core.config import get_document_extraction_config
from app.models.extracted_field import ExtractedField, ValidationResult
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.models.pipeline import ExtractedFieldRecord, ExtractionJob, ReviewQueueItem
from app.models.shards import NexaVault
from app.observability.audit_ledger import append_audit_log_or_503
from app.observability.safe_exceptions import log_safe_exception
from app.services.document_storage import DocumentStorageError, get_document_storage
from app.services.crypto_kms import EncryptedField, EncryptionError, get_encryption_provider

logger = logging.getLogger("nexa_logger")


class ExtractedIdentityMismatch(RuntimeError):
    pass


async def _validate_extracted_identity(extracted: Any, patient_id: uuid.UUID, db: AsyncSession) -> None:
    """Quarantine non-empty OCR identity that disagrees with the bound patient."""
    extracted_values = {
        "patient_name": str(extracted.patient_name or "").strip(),
        "phone": str(extracted.phone or "").strip(),
        "aadhaar_abha_id": str(extracted.aadhaar_abha_id or "").strip(),
    }
    supplied = {name: value for name, value in extracted_values.items() if value}
    if not supplied:
        return
    row = (await db.execute(
        select(NexaVault).where(NexaVault.masked_internal_id == str(patient_id)).limit(1)
    )).scalar_one_or_none()
    if row is None:
        raise ExtractedIdentityMismatch()
    kms = get_encryption_provider()
    for field_name, extracted_value in supplied.items():
        stored = getattr(row, field_name, None)
        if not stored:
            raise ExtractedIdentityMismatch()
        canonical = await kms.decrypt_field(
            str(patient_id), field_name, EncryptedField.deserialize(stored, field_name), db
        )
        normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.casefold())
        if not secrets.compare_digest(normalize(canonical), normalize(extracted_value)):
            raise ExtractedIdentityMismatch()


def _candidate_fields(document: Any) -> list[dict[str, str]]:
    """Convert clinical arrays only; OCR identity is never a chart candidate."""

    candidates: list[dict[str, str]] = []
    candidates.extend({"field_name": "diagnosis", "raw_value": str(value)} for value in document.diagnoses)
    candidates.extend({"field_name": "lab_result", "raw_value": str(value)} for value in document.lab_results)
    candidates.extend({"field_name": "medication", "raw_value": str(value)} for value in document.prescriptions)
    return candidates


async def process_extraction_job(job_id: str, db: AsyncSession) -> dict[str, Any]:
    try:
        job_uuid = uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return {"status": "extraction_failed_terminal", "error_code": "INVALID_JOB_ID"}

    job = (await db.execute(
        select(ExtractionJob).where(ExtractionJob.id == job_uuid).with_for_update()
    )).scalar_one_or_none()
    if job is None:
        return {"status": "extraction_failed_terminal", "error_code": "JOB_NOT_FOUND"}
    if job.status in {"extracted", "validation_pending", "review_pending", "ready_for_commit", "committed"}:
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
        actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
        event_type="EXTRACTION_JOB_STARTED",
        target_id=str(job.id),
        status="STARTED",
        metadata={
            "document_id": str(job.document_id), "patient_id": str(job.patient_id),
            "tenant_id": str(job.tenant_id) if job.tenant_id else None,
            "request_id": job.request_id, "attempt_count": job.attempt_count,
        },
    )

    try:
        document = (await db.execute(
            select(DocumentStorageRecord).where(DocumentStorageRecord.id == job.document_id)
        )).scalar_one_or_none()
        if document is None or job.tenant_id is None:
            raise DocumentStorageError("Document metadata unavailable")
        storage = get_document_storage()
        document_bytes = await storage.get_document_bytes(
            document.storage_ref, tenant_id=str(job.tenant_id), patient_id=str(job.patient_id)
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
        review_count = 0
        for item in candidates:
            field_id = uuid.uuid4()
            field = score_extracted_field(ExtractedField(
                field_id=str(field_id),
                job_id=str(job.id),
                field_name=item["field_name"],
                raw_value=item["raw_value"],
                normalized_value=item["raw_value"],
                confidence=float(extracted.extraction_confidence),
                risk_level="MEDIUM_RISK",
                source_page=1,
                source_document_id=str(job.document_id),
                status="needs_review",
            ))
            # The current provider contract supplies only document-level
            # confidence. It is retained as provenance but can never authorize
            # auto-commit; every such field requires human review.
            risk = str(field.risk_level or "HIGH_RISK")
            if field.field_name.lower() in {"allergy", "allergen"}:
                risk = "HIGH_RISK"
            validation = field.validation_result
            validation_json = (
                validation.model_dump() if isinstance(validation, ValidationResult)
                else validation if isinstance(validation, dict)
                else {"is_valid": False, "validation_errors": ["field_level_confidence_unavailable"]}
            )
            db.add(ExtractedFieldRecord(
                id=field_id,
                job_id=job.id,
                patient_id=job.patient_id,
                field_name=field.field_name,
                raw_value=field.raw_value,
                normalized_value=field.normalized_value,
                confidence=float(field.confidence or 0.0),
                risk_level=risk,
                validation_result=validation_json,
                source_page=field.source_page,
                source_bbox=field.source_bbox,
                status="needs_review",
                source_document_id=job.document_id,
                extractor_provider=config.provider,
                extractor_version=job.extractor_version,
            ))
            db.add(ReviewQueueItem(
                id=uuid.uuid4(), job_id=job.id, field_id=field_id,
                patient_id=job.patient_id, queued_at=datetime.now(timezone.utc), status="pending",
            ))
            review_count += 1

        job.status = "review_pending" if review_count else "ready_for_commit"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await append_audit_log_or_503(
            actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
            event_type="EXTRACTION_JOB_VALIDATED",
            target_id=str(job.id), status="SUCCESS",
            metadata={"patient_id": str(job.patient_id), "tenant_id": str(job.tenant_id),
                      "request_id": job.request_id, "review_count": review_count},
        )
        return {"job_id": str(job.id), "status": job.status, "needs_review_count": review_count}
    except ExtractedIdentityMismatch:
        job.status = "identity_mismatch"
        job.error_code = "EXTRACTED_IDENTITY_MISMATCH"
        job.retryable = False
    except EncryptionError:
        job.status = "validation_failed"
        job.error_code = "IDENTITY_VALIDATION_UNAVAILABLE"
        job.retryable = False
    except DocumentExtractionError as exc:
        retry_budget = get_document_extraction_config().max_attempts
        exhausted = exc.retryable and job.attempt_count >= retry_budget
        job.status = (
            "quarantined" if exhausted
            else "extraction_failed_retryable" if exc.retryable
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
        actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
        event_type="EXTRACTION_JOB_FAILED", target_id=str(job.id), status="FAILED",
        metadata={"patient_id": str(job.patient_id), "tenant_id": str(job.tenant_id) if job.tenant_id else None,
                  "request_id": job.request_id, "error_code": job.error_code,
                  "retryable": job.retryable, "attempt_count": job.attempt_count},
    )
    return {"job_id": str(job.id), "status": job.status, "error_code": job.error_code,
            "retryable": job.retryable}