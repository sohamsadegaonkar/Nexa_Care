"""Internal source-graph eligibility for future document-specific consent.

This module does not grant authority, register routes, decrypt, read, or
disclose source bytes. A caller must independently establish exact patient,
provider, hospital, session, and source-read authorization before using any
resolved graph in a future read workflow. Eligibility alone is not consent.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_external_record_import import PatientExternalRecordImport
from app.models.patient_records import DocumentReference
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.services.patient_external_record_lifecycle import (
    assert_patient_external_record_access_active,
)

_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_ALLOWED_MIME_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})


@dataclass(frozen=True, slots=True, repr=False)
class EligibleOriginalDocument:
    """Internal identity-only graph, deliberately excluding source content."""

    patient_id: uuid.UUID
    document_id: uuid.UUID
    import_id: uuid.UUID
    source_document_id: uuid.UUID


def _unavailable() -> HTTPException:
    # One value-free response for absent, mismatched, incomplete and unsafe rows.
    return HTTPException(
        status_code=404,
        detail={"error_code": "ORIGINAL_DOCUMENT_NOT_ELIGIBLE"},
    )


def validate_original_document_graph(
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    reference: DocumentReference,
    imported: PatientExternalRecordImport,
    source: DocumentStorageRecord,
) -> EligibleOriginalDocument:
    """Validate the supported patient-self archival graph without opening files.

    The completed import is necessary but not sufficient. Metadata integrity
    does not establish bytes integrity, current malware safety, clinical
    verification, or valid source-read authority.
    """
    if (
        reference.id != document_id
        or reference.patient_id != patient_id
        or reference.extraction_job_id is not None
        or imported.patient_id != patient_id
        or imported.final_record_id != document_id
        or imported.final_record_type != "DOCUMENT_REFERENCE"
        or imported.status != "COMPLETED"
        or imported.completed_at is None
        or imported.review_completed_at is None
        or imported.timeline_event_id is None
        or imported.error_code is not None
        or imported.category != reference.document_type
        or source.id != imported.source_document_id
        or source.patient_id != patient_id
        or source.tenant_id is not None
        or source.source_system != "patient_self"
        or source.upload_purpose != "patient_external_record_import"
        or source.content_type not in _ALLOWED_MIME_TYPES
        or not source.size
        or source.size < 1
        or not reference.storage_ref
        or reference.storage_ref.startswith("erased:")
        or not source.storage_ref
        or source.storage_ref.startswith("erased:")
        or reference.storage_ref != source.storage_ref
        or not isinstance(imported.content_hash, str)
        or not isinstance(source.content_hash, str)
        or _HASH_PATTERN.fullmatch(imported.content_hash) is None
        or _HASH_PATTERN.fullmatch(source.content_hash) is None
        or not secrets.compare_digest(imported.content_hash, source.content_hash)
    ):
        raise _unavailable()

    return EligibleOriginalDocument(
        patient_id=patient_id,
        document_id=document_id,
        import_id=imported.id,
        source_document_id=source.id,
    )


async def resolve_eligible_original_document(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
) -> EligibleOriginalDocument:
    """Resolve one exact, currently active finalized patient-self source graph.

    This is a preparatory internal service, not a route or permission gate.
    The returned identities must be re-resolved after independent authorization
    at any future disclosure boundary, including after external storage I/O.
    """
    await assert_patient_external_record_access_active(
        db, patient_id=str(patient_id)
    )

    try:
        result = await db.execute(
            select(DocumentReference, PatientExternalRecordImport, DocumentStorageRecord)
            .join(
                PatientExternalRecordImport,
                (PatientExternalRecordImport.final_record_id == DocumentReference.id)
                & (PatientExternalRecordImport.patient_id == DocumentReference.patient_id),
            )
            .join(
                DocumentStorageRecord,
                (DocumentStorageRecord.id == PatientExternalRecordImport.source_document_id)
                & (DocumentStorageRecord.patient_id == DocumentReference.patient_id),
            )
            .where(
                DocumentReference.id == document_id,
                DocumentReference.patient_id == patient_id,
                PatientExternalRecordImport.patient_id == patient_id,
                DocumentStorageRecord.patient_id == patient_id,
                DocumentStorageRecord.tenant_id.is_(None),
            )
            .limit(2)
        )
        rows = result.all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "ORIGINAL_DOCUMENT_METADATA_UNAVAILABLE"},
        ) from exc

    # A duplicate completion mapping is an ambiguous graph, not a reason to
    # take the first matching row.
    if len(rows) != 1:
        raise _unavailable()
    reference, imported, source = rows[0]
    eligible = validate_original_document_graph(
        patient_id=patient_id,
        document_id=document_id,
        reference=reference,
        imported=imported,
        source=source,
    )
    await assert_patient_external_record_access_active(
        db, patient_id=str(patient_id)
    )
    return eligible
