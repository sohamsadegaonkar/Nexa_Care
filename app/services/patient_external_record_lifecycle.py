"""Lifecycle guards for patient-self external medical-record sources.

Patient-self document objects use the document-storage encryption key with
patient-bound AAD. They are therefore not rendered unreadable by destroying the
patient clinical-data DEK alone. This module integrates that storage lifecycle
with the canonical erasure registry without creating a parallel erasure model.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConfigError
from app.models.patient_external_record_import import PatientExternalRecordImport
from app.models.patient_records import DocumentReference
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.document_storage import get_document_storage

_ERASED_SOURCE_REF = "erased://patient-external-record-source"
_ERASED_DOCUMENT_REF = "erased://patient-external-record"


class PatientExternalSourceErasureUnavailable(RuntimeError):
    """A retained patient-self source could not be enumerated or deleted."""


async def assert_patient_external_record_access_active(
    db: AsyncSession,
    *,
    patient_id: str,
) -> None:
    """Fail closed when canonical erasure state denies patient-source access."""
    try:
        uuid.UUID(patient_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid patient identity") from exc

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


async def delete_patient_external_record_sources(
    db: AsyncSession,
    *,
    patient_id: str,
) -> int:
    """Delete and neutralize every retained Task-1 patient-self source.

    The canonical erasure route must establish the erasure tombstone/access
    block before calling this helper. Raw objects are removed because their
    encryption key is independent of the patient DEK. Afterwards potentially
    identifying filename/hash/storage metadata is neutralized while preserving
    non-secret workflow/provenance rows needed by the existing erasure model.
    """
    try:
        patient_uuid = uuid.UUID(patient_id)
    except (TypeError, ValueError) as exc:
        raise PatientExternalSourceErasureUnavailable(
            "invalid patient identity for source cleanup"
        ) from exc

    try:
        source_rows = list(
            (
                await db.execute(
                    select(DocumentStorageRecord).where(
                        DocumentStorageRecord.patient_id == patient_uuid,
                        DocumentStorageRecord.tenant_id.is_(None),
                        DocumentStorageRecord.source_system == "patient_self",
                        DocumentStorageRecord.upload_purpose
                        == "patient_external_record_import",
                    )
                )
            )
            .scalars()
            .all()
        )
        import_rows = list(
            (
                await db.execute(
                    select(PatientExternalRecordImport).where(
                        PatientExternalRecordImport.patient_id == patient_uuid
                    )
                )
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        raise PatientExternalSourceErasureUnavailable(
            "patient external source metadata unavailable"
        ) from exc

    if not source_rows and not import_rows:
        return 0

    pending_source_rows = [
        row for row in source_rows if row.storage_ref != _ERASED_SOURCE_REF
    ]
    deleted = 0
    if pending_source_rows:
        try:
            storage = get_document_storage()
        except ConfigError as exc:
            raise PatientExternalSourceErasureUnavailable(
                "patient external source storage unavailable"
            ) from exc

        for row in pending_source_rows:
            try:
                await storage.delete_patient_document(
                    row.storage_ref,
                    patient_id=patient_id,
                )
            except Exception as exc:  # noqa: BLE001 - normalize storage failures
                raise PatientExternalSourceErasureUnavailable(
                    "patient external source deletion failed"
                ) from exc
            deleted += 1

    document_ids = [
        row.final_record_id
        for row in import_rows
        if row.final_record_type == "DOCUMENT_REFERENCE"
        and row.final_record_id is not None
    ]

    try:
        for row in source_rows:
            row.storage_ref = _ERASED_SOURCE_REF
            row.original_filename = None
            row.content_hash = None
            row.uploader_id = None
        for row in import_rows:
            row.content_hash = None

        if document_ids:
            await db.execute(
                update(DocumentReference)
                .where(
                    DocumentReference.patient_id == patient_uuid,
                    DocumentReference.id.in_(document_ids),
                )
                .values(storage_ref=_ERASED_DOCUMENT_REF)
            )
        await db.flush()
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise PatientExternalSourceErasureUnavailable(
            "patient external source metadata neutralization failed"
        ) from exc

    return deleted
