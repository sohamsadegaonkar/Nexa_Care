"""Provider authentication routes for Nexa Care V2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.observability.audit_ledger import append_audit_log
from app.services.provider_auth_service import (
    ProviderAuthFailure,
    authenticate_provider_password,
    issue_provider_session_token,
)

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])

_PROVIDER_SESSION_TTL_SECONDS = 60 * 60 * 8


class ProviderLoginRequest(BaseModel):
    """Provider login request with no patient data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    login_identifier: str = Field(..., min_length=1, max_length=320)
    password: str = Field(..., min_length=1, max_length=256)
    hospital_id: UUID | None = None


class ProviderLoginResponse(BaseModel):
    """Opaque provider session token response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    provider_uid: str
    hospital_id: UUID


def _status_for_failure(failure: ProviderAuthFailure) -> int:
    if failure is ProviderAuthFailure.MFA_REQUIRED:
        return status.HTTP_403_FORBIDDEN
    if failure in {
        ProviderAuthFailure.AFFILIATION_REQUIRED,
        ProviderAuthFailure.AFFILIATION_NOT_FOUND,
    }:
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_401_UNAUTHORIZED


@router.post("/login", response_model=ProviderLoginResponse)
async def provider_login(
    payload: ProviderLoginRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ProviderLoginResponse:
    """Authenticate a provider and issue a short-lived bearer session token.

    Brute-force accounting is handled inside ``authenticate_provider_password``:
    failed password attempts are committed before failure is returned, and a
    successful login resets the counters before token issuance.
    """

    result = await authenticate_provider_password(
        db,
        payload.login_identifier,
        payload.password,
        payload.hospital_id,
    )
    if result.context is None:
        assert result.failure is not None
        await append_audit_log(
            actor_uid="PROVIDER_LOGIN",
            event_type="PROVIDER_LOGIN_FAILED",
            target_id=payload.login_identifier,
            status=result.failure.value.upper(),
        )
        raise HTTPException(
            status_code=_status_for_failure(result.failure),
            detail="Invalid provider credentials",
        )

    token = await issue_provider_session_token(result.context.provider.provider_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PROVIDER_SESSION_TTL_SECONDS)

    await append_audit_log(
        actor_uid=result.context.actor_uid,
        event_type="PROVIDER_LOGIN_SUCCEEDED",
        target_id=str(result.context.hospital.hospital_id),
        status="SUCCESS",
        metadata={
            "provider_uid": result.context.actor_uid,
            "hospital_id": str(result.context.hospital.hospital_id),
            "expires_at": expires_at.isoformat(),
        },
    )

    return ProviderLoginResponse(
        access_token=token,
        expires_at=expires_at,
        provider_uid=result.context.actor_uid,
        hospital_id=result.context.hospital.hospital_id,
    )
