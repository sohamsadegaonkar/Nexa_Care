"""Consent routes for Nexa Care V2.

Exposes both the generic /grant endpoint and the frontend-facing
/routine/issue and /break-glass/issue endpoints. All paths delegate to
ConsentEngine so the v2 consent surface has a single authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db_session, get_provider_context
from app.models.provider_context import ProviderContext
from app.core.dependencies import get_provider_context, get_db_session
from app.observability.audit_ledger import append_audit_log_or_503

# EXPLICITLY ALIAS THE IMPORT SO MOCK PATCHING MATCHES THE ATTRIBUTE NAME
import app.services.consent_engine as consent_engine
from app.services.consent_engine import ConsentEngineUnavailable

router = APIRouter(prefix="/api/v2/consent", tags=["consent"])
ROUTINE_CONSENT_TTL_SECONDS = 60 * 60
BREAK_GLASS_TTL_SECONDS = 15 * 60


class RoutineConsentIssueRequest(BaseModel):
    patient_id: str
    purpose: str = "routine_access"
    scope: list[str] = Field(
        default_factory=lambda: ["clinical.*", "pii.demographics"],
        min_length=1,
        description="List of required namespaced data scopes",
    )

    model_config = ConfigDict(frozen=True)


class BreakGlassConsentIssueRequest(BaseModel):
    patient_id: str
    reason_code: str
    free_text: str = ""
    purpose: Literal["EMERGENCY"] = "EMERGENCY"

    model_config = ConfigDict(frozen=True)


class ConsentIssueResponse(BaseModel):
    """Time-bound consent token response."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    consent_token: str
    expires_at: datetime


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


def _expires_at(ttl_seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


@router.post("/grant", response_model=RoutineConsentGrantResponse)
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
            scope=request.scope,
        )
        return RoutineConsentGrantResponse(
            consent_token=token,
            expires_at=_expires_at(ROUTINE_CONSENT_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))


@router.post("/routine/issue", response_model=ConsentIssueResponse)
async def issue_routine_consent_route(
    request: RoutineConsentIssueRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    """Issue a routine consent token for a verified patient identity.

    Mirrors the contract expected by the Nexa Care mobile/web scanner
    flow: a simple patient_id + purpose request returns a time-bound
    consent token. The scope defaults to the union used by the patient
    profile screen (demographics + clinical data).
    """
    try:
        token = await consent_engine.issue(
            db=db,
            patient_id=request.patient_id,
            clinician_id=provider.actor_uid,
            purpose=request.purpose,
            scope=request.scope,
            ttl_seconds=ROUTINE_CONSENT_TTL_SECONDS,
        )
        return ConsentIssueResponse(
            consent_token=token,
            expires_at=_expires_at(ROUTINE_CONSENT_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))


@router.post("/break-glass/issue", response_model=ConsentIssueResponse)
async def issue_break_glass_consent_route(
    request: BreakGlassConsentIssueRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    """Issue an emergency break-glass consent token.

    Requires a reason_code and captures the free-text justification in
    the audit trail. The grant is short-lived and routes a notification
    to the compliance queue.
    """
    if not request.reason_code.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reason_code is required for break-glass access.",
        )

    try:
        token = await consent_engine.issue(
            db=db,
            patient_id=request.patient_id,
            clinician_id=provider.actor_uid,
            purpose=request.purpose,
            scope=["clinical.*", "pii.*"],
            ttl_seconds=BREAK_GLASS_TTL_SECONDS,
            is_break_glass=True,
            reason_code=f"{request.reason_code}: {request.free_text}".strip(": "),
        )
        return ConsentIssueResponse(
            consent_token=token,
            expires_at=_expires_at(BREAK_GLASS_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))