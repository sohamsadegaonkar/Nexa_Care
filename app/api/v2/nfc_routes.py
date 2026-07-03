"""NFC card resolution routes for Nexa Care V2.

Resolves a physical card UID to a masked patient identifier for the
provider-facing scanner flow. No clinical data is returned here; the
caller still needs a separate consent grant to read the patient record.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext
from app.services.card_resolution_service import CardResolutionService

router = APIRouter(prefix="/api/v2/nfc", tags=["nfc"])


class NFCResolveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    card_uid: str = Field(..., min_length=1, max_length=128)


class NFCResolveResponse(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    patient_id: UUID


@router.post("/resolve", response_model=NFCResolveResponse)
async def resolve_nfc_card(
    payload: NFCResolveRequest,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> NFCResolveResponse:
    """Resolve a card UID to the masked patient ID it is bound to.

    Fail-closed: unknown, lost, or revoked cards raise 403, and any
    unexpected resolution error raises 503.
    """

    resolver = CardResolutionService(db)
    try:
        patient_id = await resolver.resolve_card(payload.card_uid)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NFC resolution service is temporarily unavailable.",
        ) from exc

    return NFCResolveResponse(patient_id=patient_id)
