"""Emergency snapshot retrieval routes for Nexa Care V2."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v2/emergency", tags=["emergency"])


@router.post("/read-card", status_code=status.HTTP_410_GONE)
async def read_emergency_card() -> JSONResponse:
    """Retired: emergency clinical access requires controlled break-glass."""
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={"error_code": "EMERGENCY_DIRECT_CARD_READ_RETIRED"},
    )
