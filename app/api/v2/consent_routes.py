"""Routine consent routes for Nexa Care V2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.models.provider_context import ProviderContext
from app.core.dependencies import get_provider_context
from app.observability.audit_ledger import append_audit_log_or_503
from app.services.consent_service import (
    ConsentServiceUnavailable,
    ROUTINE_CONSENT_TTL_SECONDS,
    grant_routine_consent,
    revoke_routine_consent,
)

router = APIRouter(prefix="/api/v2/consent", tags=["consent"])


class RoutineConsentGrantRequest(BaseModel):
    """MVP request for front-desk verified routine consent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patient_id: UUID = Field(..., description="Masked patient identifier authorizing access")


class RoutineConsentGrantResponse(BaseModel):
    """Time-bound routine consent token response."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    consent_token: str
    expires_at: datetime


@router.post("/grant", response_model=RoutineConsentGrantResponse)
async def grant_routine_consent_route(
    payload: RoutineConsentGrantRequest,
    provider: ProviderContext = Depends(get_provider_context),
) -> RoutineConsentGrantResponse:
    """Grant one-hour routine access for an authenticated provider.

    This MVP assumes front-desk patient verification happened outside the API.
    The grant is provider-bound and patient-bound; possession of provider auth
    alone is not enough to read clinical data later.
    """

    patient_id = str(payload.patient_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ROUTINE_CONSENT_TTL_SECONDS)

    try:
        token = await grant_routine_consent(patient_id=patient_id, provider_uid=provider.actor_uid)
    except ConsentServiceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Routine consent service is temporarily unavailable.",
        ) from exc

    try:
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="ROUTINE_CONSENT_GRANTED",
            target_id=patient_id,
            status="GRANTED",
            metadata={
                "patient_id": patient_id,
                "provider_uid": provider.actor_uid,
                "hospital_id": str(provider.hospital.hospital_id),
                "expires_at": expires_at.isoformat(),
            },
        )
    except HTTPException:
        await revoke_routine_consent(token)
        raise

    return RoutineConsentGrantResponse(consent_token=token, expires_at=expires_at)
