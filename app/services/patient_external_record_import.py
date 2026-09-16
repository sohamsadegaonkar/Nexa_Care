"""Patient-owned external medical record import service.

This service stages encrypted patient-self sources and workflow metadata only.
It deliberately does not invoke the provider-delegated extraction pipeline.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConfigError
from app.models.patient_external_record_import import PatientExternalRecordImport
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.document_storage import DocumentStorageError, get_document_storage

PATIENT_CATEGORY_MAP = {
    "prescription": "PRESCRIPTION",
    "lab_report": "LAB_REPORT",
    "imaging_report": "IMAGING_REPORT",
    "discharge_summary": "DISCHARGE_SUMMARY",
    "other_medical_record": "OTHER_MEDICAL_RECORD",
}
PATIENT_STATUS_MAP = {
    "UPLOADED": "processing",
    "PROCESSING": "processing",
    "REVIEW_REQUIRED": "needs_review",
    "READY_TO_SAVE": "needs_review",
    "COMPLETED": "imported",
    "FAILED_RETRYABLE": "retry_available",
    "FAILED_TERMINAL": "could_not_process",
    "CANCELLED": "cancelled",
}


@dataclass(frozen=True, slots=True)
class PatientImportUploadResult:
    import_row: PatientExternalRecordImport
    duplicate: bool


def validate_patient_upload_type(
    filename: str, content_type: str, data: bytes
) -> tuple[str, str]:
    """Validate extension, declared MIME, magic bytes and obvious truncation.

    This is a transport-integrity guard, not malware scanning and not a full
    clinical-document parser. Provider/decoder validation still owns deeper
    document interpretation during extraction.
    """
    safe_name = os.path.basename(filename.replace("\\", "/"))[:255]
    ext = os.path.splitext(safe_name)[1].lower()
    allowed = {
        ".pdf": (
            "application/pdf",
            lambda b: b.startswith(b"%PDF-"),
            lambda b: b.rstrip().endswith(b"%%EOF"),
        ),
        ".png": (
            "image/png",
            lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
            lambda b: b.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"),
        ),
        ".jpg": (
            "image/jpeg",
            lambda b: b.startswith(b"\xff\xd8\xff"),
            lambda b: b.endswith(b"\xff\xd9"),
        ),
        ".jpeg": (
            "image/jpeg",
            lambda b: b.startswith(b"\xff\xd8\xff"),
            lambda b: b.endswith(b"\xff\xd9"),
        ),
    }
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail={"error_code": "UNSUPPORTED_DOCUMENT_TYPE"},
        )
    expected, signature_check, completion_check = allowed[ext]
    if content_type != expected or not signature_check(data):
        raise HTTPException(
            status_code=415,
            detail={"error_code": "DOCUMENT_TYPE_MISMATCH"},
        )
    if not completion_check(data):
        raise HTTPException(
            status_code=422,
            detail={"error_code": "DOCUMENT_MALFORMED"},
        )
    return safe_name, expected


def patient_status(status: str) -> str:
    return PATIENT_STATUS_MAP.get(status, "could_not_process")


def _content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request_matches_existing(
    row: PatientExternalRecordImport, *, category: str, content_hash: str
) -> bool:
    return row.category == category and bool(row.content_hash) and secrets.compare_digest(
        str(row.content_hash), content_hash
    )


def _idempotency_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error_code": "IMPORT_IDEMPOTENCY_CONFLICT", "retryable": False},
    )


async def _find_import_by_request(
    db: AsyncSession, *, patient_id: uuid.UUID, request_id: str
) -> PatientExternalRecordImport | None:
    return (
        await db.execute(
            select(PatientExternalRecordImport).where(
                PatientExternalRecordImport.patient_id == patient_id,
                PatientExternalRecordImport.request_id == request_id,
            )
        )
    ).scalar_one_or_none()


async def _find_import_by_hash(
    db: AsyncSession, *, patient_id: uuid.UUID, content_hash: str
) -> PatientExternalRecordImport | None:
    return (
        await db.execute(
            select(PatientExternalRecordImport)
            .join(
                DocumentStorageRecord,
                PatientExternalRecordImport.source_document_id
                == DocumentStorageRecord.id,
            )
            .where(
                PatientExternalRecordImport.patient_id == patient_id,
                DocumentStorageRecord.patient_id == patient_id,
                DocumentStorageRecord.tenant_id.is_(None),
                DocumentStorageRecord.content_hash == content_hash,
            )
            .order_by(PatientExternalRecordImport.created_at.desc())
        )
    ).scalars().first()


async def _delete_staged_source(storage, storage_ref: str, *, patient_id: str) -> None:
    try:
        await storage.delete_patient_document(storage_ref, patient_id=patient_id)
    except DocumentStorageError:
        # The encrypted orphan is inaccessible without the authoritative
        # patient namespace and has no DB reference. Cleanup can be retried by
        # storage operations without changing the API result.
        pass


async def stage_patient_external_record(
    db: AsyncSession,
    *,
    patient_id: str,
    category_slug: str,
    filename: str,
    content_type: str,
    data: bytes,
    request_id: str,
) -> PatientImportUploadResult:
    try:
        patient_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid patient identity") from exc

    category = PATIENT_CATEGORY_MAP.get(category_slug)
    if category is None:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "UNSUPPORTED_RECORD_CATEGORY"},
        )

    safe_name, mime_type = validate_patient_upload_type(filename, content_type, data)
    content_hash = _content_digest(data)

    try:
        prior_request = await _find_import_by_request(
            db, patient_id=patient_uuid, request_id=request_id
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    if prior_request is not None:
        if not _request_matches_existing(
            prior_request, category=category, content_hash=content_hash
        ):
            raise _idempotency_conflict()
        return PatientImportUploadResult(prior_request, True)

    try:
        storage = get_document_storage()
        stored = await storage.put_patient_document(
            data,
            patient_id=patient_id,
            mime_type=mime_type,
        )
    except (DocumentStorageError, ConfigError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "SOURCE_STORAGE_UNAVAILABLE", "retryable": True},
        ) from exc

    try:
        duplicate = await _find_import_by_hash(
            db,
            patient_id=patient_uuid,
            content_hash=stored.content_hash,
        )
    except SQLAlchemyError as exc:
        await _delete_staged_source(storage, stored.storage_ref, patient_id=patient_id)
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    if duplicate is not None:
        await _delete_staged_source(storage, stored.storage_ref, patient_id=patient_id)
        if duplicate.category != category:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "DUPLICATE_SOURCE_CATEGORY_CONFLICT",
                    "retryable": False,
                },
            )
        return PatientImportUploadResult(duplicate, True)

    now = datetime.now(timezone.utc)
    source_id = uuid.uuid4()
    import_id = uuid.uuid4()
    source = DocumentStorageRecord(
        id=source_id,
        patient_id=patient_uuid,
        tenant_id=None,
        uploader_id=f"patient:{patient_id}",
        storage_ref=stored.storage_ref,
        content_type=mime_type,
        size=stored.size,
        content_hash=stored.content_hash,
        original_filename=safe_name,
        upload_purpose="patient_external_record_import",
        consent_session_id=None,
        source_system="patient_self",
        uploaded_at=now,
    )
    import_row = PatientExternalRecordImport(
        id=import_id,
        patient_id=patient_uuid,
        source_document_id=source_id,
        category=category,
        status="UPLOADED",
        request_id=request_id,
        content_hash=stored.content_hash,
        attempt_count=0,
        retryable=False,
        created_at=now,
    )
    db.add(source)
    db.add(import_row)

    try:
        await db.flush()
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            idempotency_key=f"patient-external-record-upload:{patient_id}:{request_id}",
            actor_id=f"patient:{patient_id}",
            event_type="PATIENT_EXTERNAL_RECORD_UPLOAD_ACCEPTED",
            target_id=str(import_id),
            patient_id=patient_id,
            metadata={
                "category": category,
                "mime_type": mime_type,
                "size": stored.size,
            },
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await _delete_staged_source(storage, stored.storage_ref, patient_id=patient_id)
        try:
            replay_request = await _find_import_by_request(
                db,
                patient_id=patient_uuid,
                request_id=request_id,
            )
            if replay_request is not None:
                if not _request_matches_existing(
                    replay_request,
                    category=category,
                    content_hash=content_hash,
                ):
                    raise _idempotency_conflict()
                return PatientImportUploadResult(replay_request, True)
            replay_hash = await _find_import_by_hash(
                db,
                patient_id=patient_uuid,
                content_hash=stored.content_hash,
            )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "IMPORT_PERSISTENCE_UNAVAILABLE",
                    "retryable": True,
                },
            ) from exc
        if replay_hash is not None and replay_hash.category == category:
            return PatientImportUploadResult(replay_hash, True)
        raise _idempotency_conflict() from None
    except SQLAlchemyError as exc:
        await db.rollback()
        await _delete_staged_source(storage, stored.storage_ref, patient_id=patient_id)
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    except Exception:
        await db.rollback()
        await _delete_staged_source(storage, stored.storage_ref, patient_id=patient_id)
        raise

    return PatientImportUploadResult(import_row, False)


async def list_patient_external_records(
    db: AsyncSession, *, patient_id: str
) -> list[PatientExternalRecordImport]:
    patient_uuid = uuid.UUID(patient_id)
    try:
        return list(
            (
                await db.execute(
                    select(PatientExternalRecordImport)
                    .where(PatientExternalRecordImport.patient_id == patient_uuid)
                    .order_by(PatientExternalRecordImport.created_at.desc())
                )
            ).scalars().all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc


async def get_patient_external_record(
    db: AsyncSession, *, patient_id: str, import_id: uuid.UUID
) -> PatientExternalRecordImport:
    patient_uuid = uuid.UUID(patient_id)
    try:
        row = (
            await db.execute(
                select(PatientExternalRecordImport).where(
                    PatientExternalRecordImport.id == import_id,
                    PatientExternalRecordImport.patient_id == patient_uuid,
                )
            )
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "EXTERNAL_RECORD_NOT_FOUND"},
        )
    return row


async def read_patient_external_record_source(
    db: AsyncSession, *, patient_id: str, import_id: uuid.UUID
) -> tuple[bytes, str, str]:
    row = await get_patient_external_record(
        db,
        patient_id=patient_id,
        import_id=import_id,
    )
    try:
        document = (
            await db.execute(
                select(DocumentStorageRecord).where(
                    DocumentStorageRecord.id == row.source_document_id,
                    DocumentStorageRecord.patient_id == uuid.UUID(patient_id),
                    DocumentStorageRecord.tenant_id.is_(None),
                )
            )
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    if document is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "SOURCE_DOCUMENT_NOT_FOUND"},
        )

    try:
        storage = get_document_storage()
        data = await storage.get_patient_document_bytes(
            document.storage_ref,
            patient_id=patient_id,
        )
    except (DocumentStorageError, ConfigError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "SOURCE_DOCUMENT_UNAVAILABLE", "retryable": True},
        ) from exc

    try:
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            idempotency_key=f"patient-external-record-source-view:{import_id}:{uuid.uuid4()}",
            actor_id=f"patient:{patient_id}",
            event_type="PATIENT_EXTERNAL_RECORD_SOURCE_VIEWED",
            target_id=str(import_id),
            patient_id=patient_id,
            metadata={"mime_type": document.content_type},
        )
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "AUDIT_UNAVAILABLE", "retryable": True},
        ) from exc
    return data, document.content_type, document.original_filename or "medical-record"
