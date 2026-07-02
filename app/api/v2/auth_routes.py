"""Provider authentication routes for Nexa Care V2."""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])

# MFA-DISABLED-EXPLICITLY (2026-07-03): mfa_enabled=True is a real,
# reachable provider_credential state, but no /mfa/verify route (or any
# other MFA-completion path) exists anywhere in this codebase. Previously
# this returned the same generic 401/403 "Invalid provider credentials"
# used for a wrong password, which silently and permanently locked out any
# provider whose account had MFA turned on -- there was nothing distinct
# in the response telling the caller (or the person debugging the ticket)
# that this account could never complete login. Decision: until a real
# /mfa/verify flow ships, fail loudly and specifically instead of
# pretending this is a normal auth failure. See provider_auth_service.py
# for the underlying check.
_MFA_NOT_IMPLEMENTED_DETAIL = (
    "This provider account has multi-factor authentication enabled, but "
    "MFA verification is not yet implemented. Login cannot proceed. "
    "Contact an administrator to disable MFA on this account."
)

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
    # 501, not 401/403: this isn't "your credentials were wrong" or "you're
    # not allowed in" -- it's "the server doesn't support finishing this
    # login for you." A 403 here previously looked like a normal
    # security denial with no way to tell it apart from a routine failed
    # login; 501 makes it unambiguous that this is a server-side gap, not
    # something retrying or double-checking a password will ever fix.
    if failure is ProviderAuthFailure.MFA_REQUIRED:
        return status.HTTP_501_NOT_IMPLEMENTED
    if failure in {
        ProviderAuthFailure.AFFILIATION_REQUIRED,
        ProviderAuthFailure.AFFILIATION_NOT_FOUND,
    }:
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_401_UNAUTHORIZED


def _detail_for_failure(failure: ProviderAuthFailure) -> str:
    if failure is ProviderAuthFailure.MFA_REQUIRED:
        return _MFA_NOT_IMPLEMENTED_DETAIL
    return "Invalid provider credentials"


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
        if result.failure is ProviderAuthFailure.MFA_REQUIRED:
            # Not routine auth-failure noise -- every occurrence is a
            # provider who cannot log in at all until an admin disables
            # MFA on their account. Worth a CRITICAL, not a routine log.
            logger.critical(json.dumps({
                "event": "provider_login_blocked_mfa_not_implemented",
                "login_identifier": payload.login_identifier,
            }))
        raise HTTPException(
            status_code=_status_for_failure(result.failure),
            detail=_detail_for_failure(result.failure),
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