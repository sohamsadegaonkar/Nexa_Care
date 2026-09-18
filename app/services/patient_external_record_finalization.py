"""Patient-self canonical finalization for reviewed external records.

Phase D1 deliberately canonicalizes the retained external source only as a
DocumentReference. The current patient-import candidate persistence does not
retain the structured unit/reference/regimen fields required to safely create
LabResult or Medication rows, so this service must never infer those values
from free text.

The explicit patient save transaction creates the canonical document,
completion timeline marker, audit outbox event, and import completion refs
atomically. It grants no provider, hospital, tenant, consent, or clinical
session authority.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_external_record_import import (
    PatientExternalRecordCandidate,
    PatientExternalRecordImport,
)
from app.models.patient_records import DocumentReference, TimelineEvent
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.audit_outbox import enqueue_audit_event

_RESOLVED_REVIEW_STATUSES = frozenset({"ACCEPTED", "CORRECTED", "REJECTED"})


async def _load_owned_import_for_finalization(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    import_id: uuid.UUID,
) -> PatientExternalRecordImport | None:
    return (
        await db.execute(
            select(PatientExternalRecordImport)
            .where(
                PatientExternalRecordImport.id == import_id,
                PatientExternalRecordImport.patient_id == patient_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _load_owned_source_for_finalization(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    row: PatientExternalRecordImport,
) -> DocumentStorageRecord | None:
    return (
        await db.execute(
            select(DocumentStorageRecord).where(
                DocumentStorageRecord.id == row.source_document_id,
                DocumentStorageRecord.patient_id == patient_id,
                DocumentStorageRecord.tenant_id.is_(None),
                DocumentStorageRecord.source_system == "patient_self",
                DocumentStorageRecord.upload_purpose
                == "patient_external_record_import",
            )
        )
    ).scalar_one_or_none()


async def _load_owned_candidates_for_finalization(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    row: PatientExternalRecordImport,
) -> list[PatientExternalRecordCandidate]:
    return list(
        (
            await db.execute(
                select(PatientExternalRecordCandidate)
                .where(
                    PatientExternalRecordCandidate.import_id == row.id,
                    PatientExternalRecordCandidate.patient_id == patient_id,
                    PatientExternalRecordCandidate.source_document_id
                    == row.source_document_id,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def _assert_patient_active(
    db: AsyncSession,
    *,
    patient_uuid: uuid.UUID,
    patient_id: str,
) -> None:
    patient = await db.get(Patient, patient_uuid)
    if patient is None or patient.is_deleted:
        raise HTTPException(
            status_code=410,
            detail={"error_code": "PATIENT_RECORD_RETIRED"},
        )
    try:
        await check_erasure_registry(patient_id, db)
    except _PatientErasedSignal as exc:
        raise HTTPException(
            status_code=410,
            detail={"error_code": "PATIENT_DATA_ERASED"},
        ) from exc
    except ErasureRegistryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "PATIENT_DATA_UNAVAILABLE", "retryable": True},
        ) from exc


def _assert_source_integrity_metadata(
    row: PatientExternalRecordImport,
    source: DocumentStorageRecord,
) -> None:
    if (
        not row.content_hash
        or not source.content_hash
        or not secrets.compare_digest(str(row.content_hash), str(source.content_hash))
        or not source.storage_ref
    ):
        raise HTTPException(
            status_code=409,
            detail={"error_code": "SOURCE_INTEGRITY_UNAVAILABLE"},
        )


def _assert_review_complete(candidates: list[PatientExternalRecordCandidate]) -> None:
    if not candidates:
        raise HTTPException(
            status_code=409,
            detail={"error_code": "EXTERNAL_RECORD_REVIEW_EMPTY"},
        )
    if any(
        not bool(candidate.patient_reviewed)
        or candidate.review_status not in _RESOLVED_REVIEW_STATUSES
        for candidate in candidates
    ):
        raise HTTPException(
            status_code=409,
            detail={"error_code": "EXTERNAL_RECORD_REVIEW_INCOMPLETE"},
        )


async def finalize_patient_external_record(
    db: AsyncSession,
    *,
    patient_id: str,
    import_id: uuid.UUID,
) -> PatientExternalRecordImport:
    """Save a fully reviewed patient import as a canonical external document."""
    try:
        patient_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid patient identity") from exc

    try:
        row = await _load_owned_import_for_finalization(
            db,
            patient_id=patient_uuid,
            import_id=import_id,
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "EXTERNAL_RECORD_NOT_FOUND"},
            )
        if row.status == "COMPLETED":
            return row
        if row.status != "READY_TO_SAVE":
            raise HTTPException(
                status_code=409,
                detail={"error_code": "EXTERNAL_RECORD_NOT_READY_TO_SAVE"},
            )

        await _assert_patient_active(
            db,
            patient_uuid=patient_uuid,
            patient_id=patient_id,
        )
        source = await _load_owned_source_for_finalization(
            db,
            patient_id=patient_uuid,
            row=row,
        )
        if source is None:
            raise HTTPException(
                status_code=409,
                detail={"error_code": "SOURCE_DOCUMENT_NOT_FOUND"},
            )
        _assert_source_integrity_metadata(row, source)

        candidates = await _load_owned_candidates_for_finalization(
            db,
            patient_id=patient_uuid,
            row=row,
        )
        _assert_review_complete(candidates)

        now = datetime.now(timezone.utc)
        document_id = uuid.uuid4()
        timeline_id = uuid.uuid4()
        document = DocumentReference(
            id=document_id,
            patient_id=patient_uuid,
            document_type=row.category,
            uploaded_at=source.uploaded_at or row.created_at or now,
            storage_ref=source.storage_ref,
            extraction_job_id=None,
        )
        timeline = TimelineEvent(
            id=timeline_id,
            patient_id=patient_uuid,
            event_type="DOCUMENT",
            event_ref_id=document_id,
            occurred_at=now,
            source="patient_uploaded",
            summary="Imported by you from an external report",
        )
        db.add(document)
        db.add(timeline)

        row.status = "COMPLETED"
        row.final_record_type = "DOCUMENT_REFERENCE"
        row.final_record_id = document_id
        row.timeline_event_id = timeline_id
        row.completed_at = now
        row.error_code = None
        row.retryable = False

        decision_counts = {
            status: sum(1 for candidate in candidates if candidate.review_status == status)
            for status in _RESOLVED_REVIEW_STATUSES
        }
        await db.flush()
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
            idempotency_key=f"patient-external-record-save:{row.id}",
            actor_id=f"patient:{patient_id}",
            event_type="PATIENT_EXTERNAL_RECORD_SAVED",
            target_id=str(row.id),
            patient_id=patient_id,
            metadata={
                "authority": "patient_self",
                "category": row.category,
                "final_record_type": "DOCUMENT_REFERENCE",
                "accepted_count": decision_counts["ACCEPTED"],
                "corrected_count": decision_counts["CORRECTED"],
                "rejected_count": decision_counts["REJECTED"],
            },
        )
        await db.commit()
        return row
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "EXTERNAL_RECORD_SAVE_UNAVAILABLE", "retryable": True},
        ) from exc
