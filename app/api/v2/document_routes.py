"""Document ingestion routes for Nexa Care V2."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai.pipeline import process_medical_document_background
from app.core.database import get_session_factory
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503

router = APIRouter(prefix="/api/v2/documents", tags=["documents"])


class DocumentUploadAcceptedResponse(BaseModel):
    """Response returned immediately after a document is queued."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    job_id: UUID
    status: Literal["accepted"] = Field(default="accepted")
    queued_at: datetime


async def run_medical_document_pipeline_job(file_path: str, provider_uid: str) -> None:
    """Open a fresh DB session for background document processing.

    FastAPI request-scoped sessions are closed after the response is sent, so
    the background task owns its own session lifecycle and passes that session
    into the pipeline orchestrator required by the AI layer.
    """

    session_factory = get_session_factory()
    async with session_factory() as db:
        await process_medical_document_background(file_path, provider_uid, db)


@router.post(
    "/upload",
    response_model=DocumentUploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    provider: ProviderContext = Depends(get_provider_context),
) -> DocumentUploadAcceptedResponse:
    """Accept a medical document and queue AI extraction in the background.

    The route performs only ingestion: authenticate provider, persist the upload
    to a server-owned temporary file, hard-audit ``DOCUMENT_UPLOAD_RECEIVED``,
    and queue the ML pipeline. Inference runs outside the request path and the
    pipeline is responsible for deleting the temporary file in ``finally``.
    """

    job_id = uuid4()
    queued_at = datetime.now(timezone.utc)
    temp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_path = temp_file.name
            shutil.copyfileobj(file.file, temp_file)
    except Exception as exc:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to stage uploaded document for processing.",
        ) from exc
    finally:
        await file.close()

    try:
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="DOCUMENT_UPLOAD_RECEIVED",
            target_id=str(job_id),
            status="ACCEPTED",
            metadata={
                "provider_uid": provider.actor_uid,
                "hospital_id": str(provider.hospital.hospital_id),
                "job_id": str(job_id),
                "queued_at": queued_at.isoformat(),
            },
            event_timestamp=queued_at.isoformat(),
        )
    except HTTPException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

    background_tasks.add_task(
        run_medical_document_pipeline_job,
        temp_path,
        provider.actor_uid,
    )

    return DocumentUploadAcceptedResponse(job_id=job_id, queued_at=queued_at)
