"""Routine consent routes for Nexa Care V2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_context import ProviderContext
from app.core.dependencies import get_provider_context, get_db_session

# EXPLICITLY ALIAS THE IMPORT SO MOCK PATCHING MATCHES THE ATTRIBUTE NAME
import app.services.consent_engine as consent_engine
from app.services.consent_engine import ConsentEngineUnavailable

router = APIRouter(prefix="/api/v2/consent", tags=["consent"])
ROUTINE_CONSENT_TTL_SECONDS = 60 * 60


class RoutineConsentGrantRequest(BaseModel):
    patient_id: str
    purpose: str = "routine_access"
    scope: list[str] = Field(..., min_length=1, description="List of required namespaced data scopes")

    model_config = ConfigDict(frozen=True)


class RoutineConsentGrantResponse(BaseModel):
    """Time-bound routine consent token response."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    consent_token: str
    expires_at: datetime

@router.post("/grant")
async def grant_consent_route(
    request: RoutineConsentGrantRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    try:
        token = await consent_engine.issue(
            db=db,
            patient_id=request.patient_id,
            clinician_id=provider.actor_uid,
            purpose=request.purpose,
            scope=request.scope
        )
        # Calculate a locally generated expiration timestamp matching the default duration block
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=3600)
        
        return {
            "consent_token": token,
            "expires_at": expires_at.isoformat()
        }
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))