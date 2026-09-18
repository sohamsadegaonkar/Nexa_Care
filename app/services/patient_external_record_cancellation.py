"""Patient-self cancellation for external medical-record imports.

Cancellation is a terminal workflow decision, not erasure. It never deletes or
rebinds the retained source and never creates provider/treatment authority.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_external_record_import import PatientExternalRecordImport
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_external_record_lifecycle import (
    assert_patient_external_record_access_active,
)

_CANCEL_ALLOWED_STATES = frozenset(
    {"UPLOADED", "FAILED_RETRYABLE", "REVIEW_REQUIRED", "READY_TO_SAVE"}
)


async def cancel_patient_external_record(
    db: AsyncSession,
    *,
    patient_id: str,
    import_id: uuid.UUID,
) -> PatientExternalRecordImport:
    """Cancel one owned pre-completion import while retaining its source."""
    try:
        patient_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid patient identity") from exc

    await assert_patient_external_record_access_active(db, patient_id=patient_id)

    try:
        row = (
            await db.execute(
                select(PatientExternalRecordImport)
                .where(
                    PatientExternalRecordImport.id == import_id,
                    PatientExternalRecordImport.patient_id == patient_uuid,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            await db.rollback()
            raise HTTPException(
                status_code=404,
                detail={"error_code": "EXTERNAL_RECORD_NOT_FOUND"},
            )
        if row.status == "CANCELLED":
            await db.commit()
            return row
        if row.status not in _CANCEL_ALLOWED_STATES:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "EXTERNAL_RECORD_CANCEL_NOT_AVAILABLE",
                    "retryable": False,
                },
            )

        # Close merge/erasure races after the row lock but before mutation.
        try:
            await assert_patient_external_record_access_active(
                db, patient_id=patient_id
            )
        except HTTPException:
            await db.rollback()
            raise

        previous_status = row.status
        row.status = "CANCELLED"
        row.cancelled_at = datetime.now(timezone.utc)
        row.retryable = False

        await db.flush()
        try:
            await enqueue_audit_event(
                db,
                audit_context=current_audit_context(AuditDomain.PIPELINE),
                idempotency_key=f"patient-external-record-cancel:{row.id}",
                actor_id=f"patient:{patient_id}",
                event_type="PATIENT_EXTERNAL_RECORD_CANCELLED",
                target_id=str(row.id),
                patient_id=patient_id,
                metadata={
                    "authority": "patient_self",
                    "previous_status": previous_status,
                },
            )
        except Exception as exc:  # noqa: BLE001 - audit failure is fail-closed
            await db.rollback()
            raise HTTPException(
                status_code=503,
                detail={"error_code": "AUDIT_UNAVAILABLE", "retryable": True},
            ) from exc
        await db.commit()
        return row
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
