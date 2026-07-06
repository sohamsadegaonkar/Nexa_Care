"""Consent routes for Nexa Care V2.

Exposes both the generic /grant endpoint and the frontend-facing
/routine/issue and /break-glass/issue endpoints. All paths delegate to
ConsentEngine so the v2 consent surface has a single authority.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db_session, get_provider_context, require_role
from app.core.rate_limiter import RateLimiter
from app.models.provider_context import ProviderContext
from app.models.consent_grant import ConsentGrantLog
from app.models.assurance import AssuranceLevel
# EXPLICITLY ALIAS THE IMPORT SO MOCK PATCHING MATCHES THE ATTRIBUTE NAME
import app.services.consent_engine as consent_engine
from app.services.consent_engine import (
    ConsentEngineUnavailable,
    ConsentPurpose,
    issue_routine,
    issue_break_glass,
)
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")
router = APIRouter(prefix="/api/v2/consent", tags=["consent"])
ROUTINE_CONSENT_TTL_SECONDS = 60 * 60
BREAK_GLASS_TTL_SECONDS = 15 * 60

# Rate limit: 3 break-glass requests per provider per hour
_break_glass_limiter = RateLimiter(max_requests=3, window_seconds=3600, key_func=lambda r: "bg")


class RoutineConsentIssueRequest(BaseModel):
    patient_id: str
    purpose: ConsentPurpose = ConsentPurpose.TREATMENT
    scope: list[str] = Field(
        default_factory=lambda: ["clinical.*", "pii.demographics"],
        min_length=1,
        description="List of required namespaced data scopes",
    )
    assurance_level: AssuranceLevel = AssuranceLevel.STANDARD
    assurance_evidence: dict = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class BreakGlassConsentIssueRequest(BaseModel):
    patient_id: str
    reason_code: str
    free_text: str = ""
    purpose: Literal["EMERGENCY"] = "EMERGENCY"

    model_config = ConfigDict(frozen=True)


class BreakGlassRevokeRequest(BaseModel):
    consent_token: str
    revocation_reason: str

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
    assurance_level: AssuranceLevel = AssuranceLevel.STANDARD
    assurance_evidence: dict = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class RoutineConsentGrantResponse(BaseModel):
    """Time-bound routine consent token response."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    consent_token: str
    expires_at: datetime


def _expires_at(ttl_seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


def _token_hash(token: str) -> str:
    """Non-secret correlation id for the durable Postgres row."""
    # Prefix handling to match ConsentEngine._token_hash
    clean = token[len("nexa:consent:"):] if token.startswith("nexa:consent:") else token
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


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
            assurance_level=request.assurance_level,
            assurance_evidence=request.assurance_evidence,
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
    """Issue a routine consent token for a verified patient identity."""
    try:
        token = await issue_routine(
            db=db,
            patient_id=request.patient_id,
            clinician_id=provider.actor_uid,
            purpose=request.purpose,
            scope=request.scope,
            assurance_level=request.assurance_level,
            assurance_evidence=request.assurance_evidence,
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
    request: Request,
    payload: BreakGlassConsentIssueRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    """Issue an emergency break-glass consent token."""
    # Enforce rate limit per provider
    await _break_glass_limiter(request=request, provider_id=provider.actor_uid)

    try:
        full_reason = f"{payload.reason_code}: {payload.free_text}".strip(": ")
        token = await issue_break_glass(
            db=db,
            patient_id=payload.patient_id,
            clinician_id=provider.actor_uid,
            reason_code=full_reason,
        )
        return ConsentIssueResponse(
            consent_token=token,
            expires_at=_expires_at(BREAK_GLASS_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))


@router.post("/break-glass/revoke")
async def revoke_break_glass_consent_route(
    request: BreakGlassRevokeRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(require_role("clinician")),
):
    """Revoke an emergency break-glass consent token."""
    token_hash = _token_hash(request.consent_token)

    # Hard-audit revocation attempt
    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="BREAK_GLASS_REVOKE_ATTEMPT",
        target_id=token_hash,
        status="STARTED",
        metadata={"revocation_reason": request.revocation_reason},
    )

    # Verify token is a break-glass token
    stmt = select(ConsentGrantLog).where(ConsentGrantLog.token_hash == token_hash)
    result = await db.execute(stmt)
    grant = result.scalar_one_or_none()

    if grant is None or not grant.is_break_glass:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token is not a break-glass grant",
        )

    # Perform revocation
    await consent_engine.revoke(
        db=db,
        token=request.consent_token,
        reason=request.revocation_reason,
    )

    # Hard-audit success
    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="BREAK_GLASS_REVOKE_SUCCESS",
        target_id=token_hash,
        status="SUCCESS",
    )

    return {"status": "revoked", "token_hash": token_hash}


@router.get("/validate")
async def validate_consent(
    consent_token: str,
    patient_id: str | None = None,
    provider: ProviderContext = Depends(get_provider_context),
):
    """Validate a consent token (retained for terminal revalidation)."""
    try:
        capability = await consent_engine.validate(
            token=consent_token,
            patient_id=patient_id,
            clinician_id=provider.actor_uid,
        )
        if not capability:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired consent",
            )
        return capability
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))
