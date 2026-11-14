"""Consent-gated provider inventory of canonical patient document references.

This endpoint provides *metadata* already within the approved read-only
clinical history. It deliberately does NOT disclose original document bytes,
storage refs, extraction candidates, or any identity-review evidence. Reading
source bytes requires a separately reviewed READ_DOCUMENTS authority contract.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.consent_gate import require_consent
from app.core.database import get_db_session
from app.core.dependencies import require_clinical_capability
from app.models.patient_external_record_import import PatientExternalRecordImport
from app.models.patient_records import DocumentReference
from app.models.provider_context import ProviderContext
from app.security.provider_capabilities import ClinicalCapability
from app.services.patient_external_record_lifecycle import (
    assert_patient_external_record_access_active,
)

router = APIRouter(prefix="/api/v2/provider/records", tags=["provider-record-documents"])


class ProviderDocumentItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: uuid.UUID
    document_type: str
    uploaded_at: str
    source_origin: Literal["patient_imported_external", "not_established"]
    verification_status: Literal["patient_reviewed_not_clinician_verified", "not_established"]
    original_access: Literal["separate_authorization_required"] = (
        "separate_authorization_required"
    )


class ProviderDocumentPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[ProviderDocumentItem]
    next_cursor: str | None


def encode_document_cursor(at: datetime, document_id: uuid.UUID) -> str:
    payload = json.dumps(
        {"t": at.isoformat(), "id": str(document_id)}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_document_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 512:
        raise HTTPException(422, detail={"error_code": "INVALID_DOCUMENT_CURSOR"})
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(
            base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
        )
        if not isinstance(data, dict) or set(data) != {"t", "id"}:
            raise ValueError("invalid cursor shape")
        at = datetime.fromisoformat(data["t"])
        document_id = uuid.UUID(data["id"])
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("naive timestamp")
        return at, document_id
    except (
        binascii.Error, UnicodeDecodeError, json.JSONDecodeError,
        KeyError, TypeError, ValueError, OverflowError,
    ) as exc:
        raise HTTPException(
            422, detail={"error_code": "INVALID_DOCUMENT_CURSOR"}
        ) from exc


def project_document(
    document: DocumentReference,
    imported: PatientExternalRecordImport | None,
) -> ProviderDocumentItem:
    # Patient review of an imported document is NOT clinician verification.
    is_patient_import = imported is not None and imported.status == "COMPLETED"
    return ProviderDocumentItem(
        document_id=document.id,
        document_type=document.document_type,
        uploaded_at=document.uploaded_at.isoformat(),
        source_origin=(
            "patient_imported_external" if is_patient_import else "not_established"
        ),
        verification_status=(
            "patient_reviewed_not_clinician_verified"
            if is_patient_import else "not_established"
        ),
    )


@router.get("/documents", response_model=ProviderDocumentPage)
async def list_provider_patient_documents(
    response: Response,
    patient_id: str = Header(alias="X-Patient-Id"),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=512),
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.RECORD_READ)
    ),
    capability=Depends(require_consent("clinical_summary")),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderDocumentPage:
    """Return bounded canonical document metadata, never raw source contents.

    Provider/facility authority and live Signed Consent V3 read authority are
    re-evaluated by the existing dependencies. The patient ID is carried in a
    request header (not a URL) and is never itself an authorization source.
    """
    response.headers["Cache-Control"] = "private, no-store"
    try:
        patient_uuid = uuid.UUID(patient_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            422, detail={"error_code": "INVALID_PATIENT_REFERENCE"}
        ) from exc

    # Do not show surviving archival metadata after patient erasure.
    await assert_patient_external_record_access_active(db, patient_id=str(patient_uuid))
    before = decode_document_cursor(cursor)

    stmt = (
        select(DocumentReference, PatientExternalRecordImport)
        .outerjoin(
            PatientExternalRecordImport,
            and_(
                PatientExternalRecordImport.patient_id == DocumentReference.patient_id,
                PatientExternalRecordImport.final_record_id == DocumentReference.id,
                PatientExternalRecordImport.final_record_type == "DOCUMENT_REFERENCE",
                PatientExternalRecordImport.status == "COMPLETED",
            ),
        )
        .where(DocumentReference.patient_id == patient_uuid)
    )
    if before is not None:
        at, doc_id = before
        stmt = stmt.where(
            or_(
                DocumentReference.uploaded_at < at,
                and_(
                    DocumentReference.uploaded_at == at,
                    DocumentReference.id < doc_id,
                ),
            )
        )
    stmt = stmt.order_by(
        DocumentReference.uploaded_at.desc(), DocumentReference.id.desc()
    ).limit(limit + 1)

    rows = (await db.execute(stmt)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last_document = page[-1][0]
        next_cursor = encode_document_cursor(
            last_document.uploaded_at, last_document.id
        )
    return ProviderDocumentPage(
        items=[project_document(doc, imported) for doc, imported in page],
        next_cursor=next_cursor,
    )
