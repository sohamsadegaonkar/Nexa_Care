"""Provider authentication routes for Nexa Care V2."""

from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.core.rate_limiter import RateLimiter, client_ip_key
from app.core.security import encrypt_mfa_secret
from app.models.provider import ProviderCredential
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.services.provider_auth_service import (
    ProviderAuthFailure,
    authenticate_provider_password,
    complete_mfa_login,
    delete_provider_session_token,
    generate_totp_secret,
    get_totp_provisioning_uri,
    issue_provider_session_token,
    refresh_provider_session_token,
    decrypt_mfa_secret,
)

from app.core.redis import get_redis_client

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])

_PROVIDER_SESSION_TTL_SECONDS = 60 * 60 * 8
_MERGE_CHALLENGE_PREFIX = "merge_challenge:"
_MERGE_CHALLENGE_TTL_SECONDS = 120


async def _maybe_await(value):
    """Support sync redis-py and async fakes without changing route semantics."""
    if inspect.isawaitable(value):
        return await value
    return value

# Per-IP rate limiters. Single-worker MVP; replace with a Redis-backed
# limiter for multi-worker production.
_login_rate_limiter = RateLimiter(max_requests=5, window_seconds=60, key_func=client_ip_key)
_mfa_verify_rate_limiter = RateLimiter(max_requests=5, window_seconds=60, key_func=client_ip_key)


def _client_ip_from_request(request: Request) -> str:
    """Return the request's client IP, preferring X-Forwarded-For."""

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


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


class ProviderLoginMfaRequiredResponse(BaseModel):
    """Password was correct; provider must complete TOTP verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detail: str
    mfa_token: str


class ProviderMfaVerifyRequest(BaseModel):
    """Complete login with a TOTP code.

    ``provider_id``, if supplied, is NOT a credential and is never used to
    resolve identity. Identity is resolved exclusively from the server-side
    Redis-backed ``mfa_token`` pending-token, which is proof the caller
    already passed the password step. ``provider_id`` here is only a
    client-echo integrity check: if a caller supplies one and it does not
    match the identity bound to their ``mfa_token``, that is treated as a
    session-confusion / IDOR probe and rejected (see
    ``ProviderAuthFailure.SESSION_BINDING_MISMATCH``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mfa_token: str = Field(..., min_length=1, max_length=256)
    totp_code: str = Field(..., min_length=6, max_length=8)
    provider_id: UUID | None = Field(
        default=None,
        description=(
            "Optional client-echo of the provider's ID for defense-in-depth "
            "session-binding verification. Never authoritative on its own."
        ),
    )
    hospital_id: UUID | None = None


class ProviderMfaSetupResponse(BaseModel):
    """MFA enrollment response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret: str
    provisioning_uri: str
    message: str


class TokenRefreshResponse(BaseModel):
    """New Bearer session token after rotation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


def _status_for_failure(failure: ProviderAuthFailure) -> int:
    if failure in {
        ProviderAuthFailure.AFFILIATION_REQUIRED,
        ProviderAuthFailure.AFFILIATION_NOT_FOUND,
    }:
        return status.HTTP_400_BAD_REQUEST
    if failure is ProviderAuthFailure.MFA_NOT_CONFIGURED:
        # Inconsistent state: MFA flag is on but no secret is enrolled.
        return status.HTTP_500_INTERNAL_SERVER_ERROR
    if failure is ProviderAuthFailure.MFA_RATE_LIMITED:
        return status.HTTP_429_TOO_MANY_REQUESTS
    return status.HTTP_401_UNAUTHORIZED


def _detail_for_failure(failure: ProviderAuthFailure) -> str:
    if failure is ProviderAuthFailure.MFA_INVALID_CODE:
        return "Invalid or expired MFA code."
    if failure is ProviderAuthFailure.MFA_NOT_CONFIGURED:
        return (
            "This provider account has multi-factor authentication enabled, but "
            "no MFA secret is enrolled. Contact an administrator."
        )
    if failure is ProviderAuthFailure.MFA_RATE_LIMITED:
        return "Too many failed MFA attempts. Please try again later."
    return "Invalid provider credentials"


async def _issue_login_response(
    context: ProviderContext,
    user_agent: str | None = None,
    client_ip: str | None = None,
) -> ProviderLoginResponse:
    token = await issue_provider_session_token(
        context.provider.provider_id,
        user_agent=user_agent,
        client_ip=client_ip,
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PROVIDER_SESSION_TTL_SECONDS)
    return ProviderLoginResponse(
        access_token=token,
        expires_at=expires_at,
        provider_uid=context.actor_uid,
        hospital_id=context.hospital.hospital_id,
    )


@router.post("/login")
async def provider_login(
    payload: ProviderLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    _rate_limit: None = Depends(_login_rate_limiter),
):
    """Authenticate a provider and issue a short-lived bearer session token.

    If the provider has MFA enabled, the password step returns a short-lived
    ``mfa_token`` instead of the final bearer token. The client must then call
    ``POST /api/v2/auth/mfa/verify`` with the current TOTP code.

    The final session token is bound to the request's User-Agent and client IP.
    """

    result = await authenticate_provider_password(
        db,
        payload.login_identifier,
        payload.password,
        payload.hospital_id,
    )

    if result.failure is ProviderAuthFailure.MFA_REQUIRED:
        assert result.mfa_pending_token is not None
        await append_audit_log(
            actor_uid="PROVIDER_LOGIN",
            event_type="PROVIDER_MFA_REQUIRED",
            target_id=payload.login_identifier,
            status="MFA_REQUIRED",
        )
        return ProviderLoginMfaRequiredResponse(
            detail="Multi-factor authentication required.",
            mfa_token=result.mfa_pending_token,
        )

    if result.context is None:
        assert result.failure is not None
        await append_audit_log(
            actor_uid="PROVIDER_LOGIN",
            event_type="PROVIDER_LOGIN_FAILED",
            target_id=payload.login_identifier,
            status=result.failure.value.upper(),
        )
        if result.failure is ProviderAuthFailure.MFA_NOT_CONFIGURED:
            logger.critical(json.dumps({
                "event": "provider_login_mfa_not_configured",
                "login_identifier": payload.login_identifier,
            }))
        raise HTTPException(
            status_code=_status_for_failure(result.failure),
            detail=_detail_for_failure(result.failure),
        )

    user_agent = request.headers.get("user-agent")
    client_ip = _client_ip_from_request(request)
    response = await _issue_login_response(result.context, user_agent, client_ip)
    await append_audit_log(
        actor_uid=result.context.actor_uid,
        event_type="PROVIDER_LOGIN_SUCCEEDED",
        target_id=str(result.context.hospital.hospital_id),
        status="SUCCESS",
        metadata={
            "provider_uid": result.context.actor_uid,
            "hospital_id": str(result.context.hospital.hospital_id),
            "expires_at": response.expires_at.isoformat(),
        },
    )
    return response


@router.post("/mfa/verify", response_model=ProviderLoginResponse)
async def provider_mfa_verify(
    payload: ProviderMfaVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    _rate_limit: None = Depends(_mfa_verify_rate_limiter),
) -> ProviderLoginResponse:
    """Complete a provider login by verifying a TOTP code.

    The final session token is bound to the request's User-Agent and client IP.
    """

    user_agent = request.headers.get("user-agent")
    client_ip = _client_ip_from_request(request)

    result = await complete_mfa_login(
        db,
        payload.mfa_token,
        payload.totp_code,
        payload.hospital_id,
        client_ip=client_ip,
        claimed_provider_id=payload.provider_id,
    )

    if result.context is None:
        assert result.failure is not None

        # SESSION_BINDING_MISMATCH means the caller's mfa_token resolved to
        # a *different* provider than the provider_id they claimed in the
        # body — a session-confusion / IDOR probe, not a routine bad code.
        # It gets its own audit target_id (the claimed identity) so the
        # immutable ledger can distinguish "wrong code" from "possible
        # cross-account access attempt" without leaking that distinction
        # back to the client in the HTTP response.
        is_binding_mismatch = result.failure is ProviderAuthFailure.SESSION_BINDING_MISMATCH
        await append_audit_log(
            actor_uid="PROVIDER_MFA",
            event_type=(
                "PROVIDER_MFA_SESSION_BINDING_MISMATCH"
                if is_binding_mismatch
                else "PROVIDER_MFA_VERIFY_FAILED"
            ),
            target_id=str(payload.provider_id) if is_binding_mismatch else "UNKNOWN",
            status=result.failure.value.upper(),
        )
        raise HTTPException(
            status_code=_status_for_failure(result.failure),
            detail=_detail_for_failure(result.failure),
        )

    response = await _issue_login_response(result.context, user_agent, client_ip)
    await append_audit_log(
        actor_uid=result.context.actor_uid,
        event_type="PROVIDER_LOGIN_SUCCEEDED",
        target_id=str(result.context.hospital.hospital_id),
        status="SUCCESS",
        metadata={
            "provider_uid": result.context.actor_uid,
            "hospital_id": str(result.context.hospital.hospital_id),
            "expires_at": response.expires_at.isoformat(),
            "mfa_verified": True,
        },
    )
    return response


class ProviderMfaSetupVerifyRequest(BaseModel):
    """Verify and enable MFA enrollment."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    totp_code: str = Field(..., min_length=6, max_length=8)


@router.post("/mfa/setup", response_model=ProviderMfaSetupResponse)
async def provider_mfa_setup(
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_current_provider),
) -> ProviderMfaSetupResponse:
    """Initialize TOTP MFA enrollment for the authenticated provider.

    Generates a new secret and stores it on the provider's credential row, but
    does NOT enable the MFA flag yet. Enrollment must be verified via
    ``POST /api/v2/auth/mfa/setup/verify``.

    Security controls:
    - If MFA is already enabled, the request is rejected (409).
    - If a setup was initiated recently (15-min TTL), the request is rejected
      to prevent unverified secret overwrite/interception.
    """

    stmt = (
        select(ProviderCredential)
        .where(ProviderCredential.provider_id == provider.provider.provider_id)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider credential not found.",
        )

    if row.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled.",
        )

    # Overwrite protection / Pending TTL (15 mins)
    if row.mfa_secret_encrypted:
        now = datetime.now(timezone.utc)
        elapsed = now - row.updated_at
        if elapsed < timedelta(minutes=15):
            logger.warning(json.dumps({
                "event": "provider_mfa_setup_throttled",
                "provider_id": str(provider.provider.provider_id),
                "elapsed_seconds": int(elapsed.total_seconds()),
            }))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="A setup is already in progress. Please wait 15 minutes or complete verification.",
            )

        logger.info(json.dumps({
            "event": "provider_mfa_setup_overwriting_stale_secret",
            "provider_id": str(provider.provider.provider_id),
            "stale_age_seconds": int(elapsed.total_seconds()),
        }))

    secret = generate_totp_secret()
    row.mfa_secret_encrypted = encrypt_mfa_secret(secret)
    row.mfa_secret = None
    await db.commit()

    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="PROVIDER_MFA_SETUP_INIT",
        target_id=str(provider.provider.provider_id),
        status="SUCCESS",
    )

    return ProviderMfaSetupResponse(
        secret=secret,
        provisioning_uri=get_totp_provisioning_uri(secret, provider.actor_uid),
        message="Scan the provisioning URI and call /mfa/setup/verify to enable MFA.",
    )


@router.post("/mfa/setup/verify")
async def provider_mfa_setup_verify(
    payload: ProviderMfaSetupVerifyRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_current_provider),
):
    """Complete MFA enrollment by verifying a TOTP code.

    If the code is correct, the MFA flag is enabled for the account.
    """

    stmt = (
        select(ProviderCredential)
        .where(ProviderCredential.provider_id == provider.provider.provider_id)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None or row.mfa_secret_encrypted is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA setup has not been initialized.",
        )

    if row.mfa_enabled:
        return {"message": "MFA is already enabled."}

    secret = decrypt_mfa_secret(row.mfa_secret_encrypted)
    from app.services.provider_auth_service import verify_totp_code
    if not verify_totp_code(secret, payload.totp_code):
        await append_audit_log(
            actor_uid=provider.actor_uid,
            event_type="PROVIDER_MFA_SETUP_VERIFY_FAILED",
            target_id=str(provider.provider.provider_id),
            status="INVALID_CODE",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid MFA code. Verification failed.",
        )

    row.mfa_enabled = True
    await db.commit()

    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="PROVIDER_MFA_SETUP_SUCCESS",
        target_id=str(provider.provider.provider_id),
        status="SUCCESS",
    )

    return {"message": "MFA has been successfully enabled."}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def provider_logout(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=True)),
    provider: ProviderContext = Depends(get_current_provider),
) -> None:
    """Invalidate the current Bearer session token."""

    await delete_provider_session_token(credentials.credentials)
    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="PROVIDER_LOGOUT",
        target_id=str(provider.provider.provider_id),
        status="SUCCESS",
    )


@router.post("/refresh", response_model=TokenRefreshResponse)
async def provider_refresh(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=True)),
    provider: ProviderContext = Depends(get_current_provider),
) -> TokenRefreshResponse:
    """Rotate the current Bearer session token to a new one.

    The new token is rebound to the current request's User-Agent and client IP.
    """

    user_agent = request.headers.get("user-agent")
    client_ip = _client_ip_from_request(request)
    new_token = await refresh_provider_session_token(
        credentials.credentials,
        user_agent=user_agent,
        client_ip=client_ip,
    )
    if new_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token is invalid or expired.",
        )

    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="PROVIDER_SESSION_REFRESH",
        target_id=str(provider.provider.provider_id),
        status="SUCCESS",
    )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PROVIDER_SESSION_TTL_SECONDS)
    return TokenRefreshResponse(
        access_token=new_token,
        expires_at=expires_at,
    )


class MergeChallengeResponse(BaseModel):
    challenge_token: str
    requires_mfa: bool = True
    expires_in_seconds: int = _MERGE_CHALLENGE_TTL_SECONDS


@router.post("/challenge/merge", response_model=MergeChallengeResponse)
async def create_merge_challenge(
    provider: ProviderContext = Depends(get_current_provider),
):
    """Generate a short-lived challenge token for the merge operation.
    Requires admin role.
    """
    if "admin" not in provider.affiliation.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for merge challenge.",
        )

    challenge_token = str(uuid.uuid4())
    payload = {
        "provider_id": str(provider.provider.provider_id),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "verified": False,
    }

    redis = get_redis_client()
    key = f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}"
    await _maybe_await(redis.setex(key, _MERGE_CHALLENGE_TTL_SECONDS, json.dumps(payload)))

    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="MERGE_CHALLENGE_CREATED",
        target_id=challenge_token,
        status="SUCCESS"
    )

    return MergeChallengeResponse(challenge_token=challenge_token)


class MergeChallengeVerifyRequest(BaseModel):
    challenge_token: str
    totp_code: str = Field(..., min_length=6, max_length=6)


@router.post("/challenge/merge/verify")
async def verify_merge_challenge(
    payload: MergeChallengeVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_current_provider),
):
    """Verify a merge challenge with a TOTP code."""
    redis = get_redis_client()
    key = f"{_MERGE_CHALLENGE_PREFIX}{payload.challenge_token}"
    cached = await _maybe_await(redis.get(key))
    if not cached:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge expired or invalid.",
        )

    challenge_data = json.loads(cached)
    if challenge_data["provider_id"] != str(provider.provider.provider_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Challenge bound to different provider.",
        )

    # Re-use existing MFA logic
    from app.services.provider_auth_service import (
        verify_totp_code,
        _record_failed_mfa_attempt,
        _clear_mfa_fails,
        _is_mfa_rate_limited,
        hash_client_ip,
    )

    client_ip = _client_ip_from_request(request)
    ip_hash = hash_client_ip(client_ip)

    if await _is_mfa_rate_limited(provider.provider.provider_id, ip_hash):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed MFA attempts. Please try again later.",
        )

    # Fetch credential to get secret
    stmt = select(ProviderCredential).where(ProviderCredential.provider_id == provider.provider.provider_id)
    result = await db.execute(stmt)
    cred = result.scalar_one_or_none()
    if not cred or not cred.mfa_enabled or not cred.mfa_secret_encrypted:
         raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA not enabled or configured for this provider.",
        )

    mfa_secret = decrypt_mfa_secret(cred.mfa_secret_encrypted)
    if not verify_totp_code(mfa_secret, payload.totp_code):
        await _record_failed_mfa_attempt(provider.provider.provider_id, ip_hash)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MFA code.",
        )

    await _clear_mfa_fails(provider.provider.provider_id, ip_hash)

    challenge_data["verified"] = True
    await _maybe_await(redis.setex(key, _MERGE_CHALLENGE_TTL_SECONDS, json.dumps(challenge_data)))

    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="MERGE_CHALLENGE_VERIFIED",
        target_id=payload.challenge_token,
        status="SUCCESS"
    )

    return {"challenge_token": payload.challenge_token, "verified": True}
