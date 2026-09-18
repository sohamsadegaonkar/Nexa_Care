"""Strict patient-self external medical record import routes.

The parent router is mounted at ``/api/v2/patient``; this child contributes
``/me/external-records`` and therefore never accepts a patient identifier from
the client as an authority input.
"""

from __future__ import annotations

import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extractor import TEXTRACT_MAX_SYNC_BYTES
from app.core.config import ConfigError, get_document_extraction_config
from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedPatient, get_current_patient
from app.models.patient_external_record_import import PatientExternalRecordImport
from app.services.patient_external_record_extraction import process_patient_external_record
from app.services.patient_external_record_import import (
    get_patient_external_record,
    list_patient_external_records,
    patient_status,
    read_patient_external_record_source,
    stage_patient_external_record,
)

router = APIRouter(prefix="/me/external-records", tags=["patient-external-records"])

PatientCategory = Literal[
    "prescription",
    "lab_report",
    "imaging_report",
    "discharge_summary",
    "other_medical_record",
]
PatientVisibleStatus = Literal[
    "processing",
    "needs_review",
    "ready_to_save",
    "imported",
    "retry_available",
    "could_not_process",
    "cancelled",
]


class PatientExternalRecordResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    import_id: uuid.UUID
    category: PatientCategory
    status: PatientVisibleStatus
    duplicate: bool = False
    source_available: bool = True
    created_at: str


_INTERNAL_TO_PUBLIC_CATEGORY: dict[str, PatientCategory] = {
    "PRESCRIPTION": "prescription",
    "LAB_REPORT": "lab_report",
    "IMAGING_REPORT": "imaging_report",
    "DISCHARGE_SUMMARY": "discharge_summary",
    "OTHER_MEDICAL_RECORD": "other_medical_record",
}


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _response(
    row: PatientExternalRecordImport, *, duplicate: bool = False
) -> PatientExternalRecordResponse:
    public_status = patient_status(row.status)
    return PatientExternalRecordResponse(
        import_id=row.id,
        category=_INTERNAL_TO_PUBLIC_CATEGORY[row.category],
        status=public_status,  # type: ignore[arg-type]
        duplicate=duplicate,
        source_available=True,
        created_at=row.created_at.isoformat(),
    )


def _upload_limit() -> int:
    try:
        configured = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
        extraction_config = get_document_extraction_config()
    except (ValueError, ConfigError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "UPLOAD_CONFIGURATION_UNAVAILABLE", "retryable": True},
        ) from exc
    if configured <= 0:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "UPLOAD_CONFIGURATION_UNAVAILABLE", "retryable": True},
        )
    return (
        min(configured, TEXTRACT_MAX_SYNC_BYTES)
        if extraction_config.provider == "aws_textract"
        else configured
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PatientExternalRecordResponse,
)
async def upload_external_record(
    response: Response,
    category: PatientCategory = Form(...),
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> PatientExternalRecordResponse:
    """Retain a patient-owned external source without creating provider authority."""
    _set_no_store(response)
    max_bytes = _upload_limit()
    data = await file.read(max_bytes + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail={"error_code": "EMPTY_DOCUMENT"})
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail={"error_code": "DOCUMENT_TOO_LARGE"})

    request_id = (idempotency_key or str(uuid.uuid4())).strip()
    if not request_id or len(request_id) > 64:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "INVALID_IDEMPOTENCY_KEY"},
        )

    try:
        result = await stage_patient_external_record(
            db,
            patient_id=auth.patient_id,
            category_slug=category,
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=data,
            request_id=request_id,
        )
    finally:
        del data
    return _response(result.import_row, duplicate=result.duplicate)


@router.get("", response_model=list[PatientExternalRecordResponse])
async def list_external_records(
    response: Response,
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> list[PatientExternalRecordResponse]:
    _set_no_store(response)
    rows = await list_patient_external_records(db, patient_id=auth.patient_id)
    return [_response(row) for row in rows]


@router.get("/{import_id}", response_model=PatientExternalRecordResponse)
async def read_external_record(
    import_id: uuid.UUID,
    response: Response,
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> PatientExternalRecordResponse:
    _set_no_store(response)
    row = await get_patient_external_record(
        db, patient_id=auth.patient_id, import_id=import_id
    )
    return _response(row)


@router.post("/{import_id}/process", response_model=PatientExternalRecordResponse)
async def process_external_record(
    import_id: uuid.UUID,
    response: Response,
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> PatientExternalRecordResponse:
    """Extract owned evidence into encrypted review candidates only."""
    _set_no_store(response)
    row = await process_patient_external_record(
        db,
        patient_id=auth.patient_id,
        import_id=import_id,
    )
    return _response(row)


@router.get("/{import_id}/source")
async def read_external_record_source(
    import_id: uuid.UUID,
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    data, mime_type, filename = await read_patient_external_record_source(
        db, patient_id=auth.patient_id, import_id=import_id
    )
    safe_filename = (
        os.path.basename(filename.replace("\\", "/"))
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")[:255]
        or "medical-record"
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename}"',
            "Cache-Control": "private, no-store",
        },
    )
