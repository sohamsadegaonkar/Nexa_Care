"""Compatibility response for the retired unbound document upload route."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/v2/documents", tags=["documents"])


class DocumentUploadAcceptedResponse(BaseModel):
    """Historical response schema retained for generated-client imports only."""
    job_id: UUID
    status: Literal["accepted"] = "accepted"
    queued_at: datetime


@router.post("/upload", status_code=410)
async def upload_document() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "error_code": "UNBOUND_DOCUMENT_UPLOAD_RETIRED",
            "message": "Use /api/v2/pipeline/documents/upload with an authorized patient binding.",
            "retryable": False,
        },
    )
