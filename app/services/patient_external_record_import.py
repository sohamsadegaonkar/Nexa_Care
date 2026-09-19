"""Patient-owned external medical record import service.

This service stages encrypted patient-self sources and workflow metadata only.
It deliberately does not invoke the provider-delegated extraction pipeline.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from collections.abc import Callable
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
from app.services.patient_external_record_lifecycle import (
    assert_patient_external_record_access_active,
)
from app.services.patient_external_record_source_safety import (
    PatientSourceSafetyError,
    validate_patient_source_decoder,
)

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
    "READY_TO_SAVE": "ready_to_save",
    "COMPLETED": "imported",
    "FAILED_RETRYABLE": "retry_available",
    "FAILED_TERMINAL": "could_not_process",
    "CANCELLED": "cancelled",
}


@dataclass(frozen=True, slots=True)
class PatientImportUploadResult:
    import_row: PatientExternalRecordImport
    duplicate: bool


@dataclass(frozen=True, slots=True)
class PatientUploadTypeRule:
    mime_type: str
    signature_check: Callable[[bytes], bool]
    completion_check: Callable[[bytes], bool]


PATIENT_UPLOAD_TYPE_RULES: dict[str, PatientUploadTypeRule] = {
    ".pdf": PatientUploadTypeRule(
        mime_type="application/pdf",
        signature_check=lambda data: data.startswith(b"%PDF-"),
        completion_check=lambda data: data.rstrip().endswith(b"%%EOF"),
    ),
    ".png": PatientUploadTypeRule(
        mime_type="image/png",
        signature_check=lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        completion_check=lambda data: data.endswith(
            b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
        ),
    ),
    ".jpg": PatientUploadTypeRule(
        mime_type="image/jpeg",
        signature_check=lambda data: data.startswith(b"\xff\xd8\xff"),
        completion_check=lambda data: data.endswith(b"\xff\xd9"),
    ),
    ".jpeg": PatientUploadTypeRule(
        mime_type="image/jpeg",
        signature_check=lambda data: data.startswith(b"\xff\xd8\xff"),
        completion_check=lambda data: data.endswith(b"\xff\xd9"),
    ),
}
PATIENT_UPLOAD_EXTENSIONS = tuple(PATIENT_UPLOAD_TYPE_RULES)
PATIENT_UPLOAD_MIME_TYPES = tuple(
    dict.fromkeys(rule.mime_type for rule in PATIENT_UPLOAD_TYPE_RULES.values())
)


def patient_action_capabilities(
    row: PatientExternalRecordImport,
) -> dict[str, bool]:
    """Return fail-closed patient-self client actions for the persisted state.

    can_view_source is advisory: it means the retained source endpoint may be
    requested for this import. Lifecycle, metadata, storage, and integrity
    failures can still make the source request fail.
    """
    state = row.status
    return {
        "can_process": state == "UPLOADED",
        "can_retry": state == "FAILED_RETRYABLE" and bool(row.retryable),
        "can_cancel": state
        in {"UPLOADED", "FAILED_RETRYABLE", "REVIEW_REQUIRED", "READY_TO_SAVE"},
        "can_review": state in {"REVIEW_REQUIRED", "READY_TO_SAVE"},
        "can_save": state == "READY_TO_SAVE",
        "can_view_source": bool(row.source_document_id),
    }


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
    rule = PATIENT_UPLOAD_TYPE_RULES.get(ext)
    if rule is None:
        raise HTTPException(
            status_code=415,
            detail={"error_code": "UNSUPPORTED_DOCUMENT_TYPE"},
        )
    if content_type != rule.mime_type or not rule.signature_check(data):
        raise HTTPException(
            status_code=415,
            detail={"error_code": "DOCUMENT_TYPE_MISMATCH"},
        )
    if not rule.completion_check(data):
        raise HTTPException(
            status_code=422,
            detail={"error_code": "DOCUMENT_MALFORMED"},
        )
    return safe_name, rule.mime_type


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
    except Exception as exc:  # noqa: BLE001 - normalize storage cleanup failures
        raise HTTPException(
            status_code=503,
            detail={"error_code": "SOURCE_CLEANUP_UNAVAILABLE", "retryable": True},
        ) from exc


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

    await assert_patient_external_record_access_active(db, patient_id=patient_id)

    category = PATIENT_CATEGORY_MAP.get(category_slug)
    if category is None:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "UNSUPPORTED_RECORD_CATEGORY"},
        )

    safe_name, mime_type = validate_patient_upload_type(filename, content_type, data)
    try:
        validate_patient_source_decoder(data, mime_type=mime_type)
    except PatientSourceSafetyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_code": exc.code, "retryable": False},
        ) from exc
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
        await assert_patient_external_record_access_active(db, patient_id=patient_id)
    except HTTPException:
        await _delete_staged_source(storage, stored.storage_ref, patient_id=patient_id)
        raise

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

    try:
        await assert_patient_external_record_access_active(db, patient_id=patient_id)
    except HTTPException:
        await _delete_staged_source(storage, stored.storage_ref, patient_id=patient_id)
        raise

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

    try:
        # The import has a composite ownership FK to document_storage. Flush the
        # retained source first so PostgreSQL never observes the child import
        # before its exact patient-owned source row inside this transaction.
        await db.flush()
        db.add(import_row)
        await db.flush()
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            idempotency_key=f"patient-external-record-upload:{patient_id}:{request_id}",
            actor_id=f"patient:{patient_id}",
            event_type="DOCUMENT_UPLOADED",
            target_id=str(import_id),
            patient_id=patient_id,
            metadata={
                "authority": "patient_self",
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
    await assert_patient_external_record_access_active(db, patient_id=patient_id)
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
    await assert_patient_external_record_access_active(db, patient_id=patient_id)
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
    if getattr(row, "error_code", None) == "SOURCE_MALWARE_DETECTED":
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "SOURCE_DOCUMENT_QUARANTINED",
                "retryable": False,
            },
        )
    await assert_patient_external_record_access_active(db, patient_id=patient_id)
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

    await assert_patient_external_record_access_active(db, patient_id=patient_id)
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
        await assert_patient_external_record_access_active(db, patient_id=patient_id)
    except HTTPException:
        raise

    try:
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            idempotency_key=f"patient-external-record-source-view:{import_id}:{uuid.uuid4()}",
            actor_id=f"patient:{patient_id}",
            event_type="DOCUMENT_SOURCE_VIEWED",
            target_id=str(import_id),
            patient_id=patient_id,
            metadata={
                "authority": "patient_self",
                "mime_type": document.content_type,
            },
        )
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "AUDIT_UNAVAILABLE", "retryable": True},
        ) from exc
    return data, document.content_type, document.original_filename or "medical-record"
