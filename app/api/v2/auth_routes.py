"""Provider authentication routes for Nexa Care V2."""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import inspect
import hashlib
import hmac
import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
import uuid
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.core.rate_limiter import (
    OtpRateLimitBackendUnavailable,
    OtpRateLimitExceeded,
    OtpRedisRateLimiter,
    RateLimiter,
    client_ip_key,
)
from app.core.security import encrypt_mfa_secret, hash_client_ip
from app.models.provider import ProviderCredential
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
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
    resolve_provider_session_context,
    decrypt_mfa_secret,
)

from app.core.redis import get_async_redis_client
from app.core.session_binding import provider_session_binding
from app.core.config import get_otp_rate_limit_config
from app.core.rate_limiter import atomic_fixed_window
from app.core.client_ip import resolve_client_ip
from app.core.supabase import get_supabase_client
from app.services.patient_auth_service import (
    issue_device_enrollment_token,
    issue_patient_access_token,
    normalize_indian_phone,
)

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


# Provider login limiters preserve their existing shared Redis behavior.
_login_rate_limiter = RateLimiter(
    max_requests=5, window_seconds=60, key_func=client_ip_key
)
_mfa_verify_rate_limiter = RateLimiter(
    max_requests=5, window_seconds=60, key_func=client_ip_key
)
_otp_rate_limiter = OtpRedisRateLimiter()


class PatientOtpSendRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    phone: str = Field(..., min_length=10, max_length=32)


class PatientOtpVerifyRequest(PatientOtpSendRequest):
    otp: str = Field(..., pattern=r"^\d{6}$")


class PatientOtpSendResponse(BaseModel):
    message: str


class PatientOtpVerifyResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    patient_id: str
    device_enrollment_token: str


def _normalized_phone_or_422(phone: str) -> str:
    try:
        return normalize_indian_phone(phone)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "INVALID_PHONE_FORMAT"},
        ) from exc


async def _enforce_otp_limits(request: Request, phone: str) -> None:
    ip = _client_ip_from_request(request) or "unknown"
    action = request.url.path.rsplit("/", 1)[-1]
    try:
        await _otp_rate_limiter.check(action=action, ip=ip, normalized_phone=phone)
    except OtpRateLimitExceeded:
        raise HTTPException(
            status_code=429, detail="Too many OTP requests. Please try again later."
        )
    except OtpRateLimitBackendUnavailable:
        raise HTTPException(
            status_code=503, detail="OTP service is temporarily unavailable."
        )


@router.post("/otp/send", response_model=PatientOtpSendResponse)
async def patient_otp_send(
    payload: PatientOtpSendRequest, request: Request
) -> PatientOtpSendResponse:
    phone = _normalized_phone_or_422(payload.phone)
    await _enforce_otp_limits(request, phone)
    try:
        await run_in_threadpool(
            get_supabase_client().auth.sign_in_with_otp,
            {"phone": phone, "options": {"should_create_user": False}},
        )
    except Exception as exc:
        # Preserve account non-enumeration. Supabase returns an error for an
        # unknown phone when user creation is disabled; callers receive the
        # same response as an existing patient.
        code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if code not in {400, 401, 403, 422}:
            raise HTTPException(
                status_code=503, detail="SMS service is unavailable."
            ) from None
    return PatientOtpSendResponse(
        message="If this phone is registered, an OTP will be sent."
    )


@router.post("/otp/verify", response_model=PatientOtpVerifyResponse)
async def patient_otp_verify(
    payload: PatientOtpVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> PatientOtpVerifyResponse:
    phone = _normalized_phone_or_422(payload.phone)
    await _enforce_otp_limits(request, phone)
    try:
        result = await run_in_threadpool(
            get_supabase_client().auth.verify_otp,
            {"phone": phone, "token": payload.otp, "type": "sms"},
        )
    except Exception as exc:
        code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if code in {400, 401, 403}:
            raise HTTPException(
                status_code=401, detail="Invalid or expired OTP."
            ) from None
        raise HTTPException(
            status_code=503, detail="SMS verification service is unavailable."
        ) from None

    user = getattr(result, "user", None)
    verified_phone = getattr(user, "phone", None)
    supabase_user_id = getattr(user, "id", None)
    session = getattr(result, "session", None)
    supabase_access_token = getattr(session, "access_token", None)
    if (
        not user
        or not verified_phone
        or not supabase_user_id
        or not supabase_access_token
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
    try:
        authoritative_phone = normalize_indian_phone(str(verified_phone))
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.") from None
    if authoritative_phone != phone:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")

    identity = await db.scalar(
        select(PatientAuthIdentity).where(
            PatientAuthIdentity.provider == "supabase",
            PatientAuthIdentity.provider_subject == str(supabase_user_id),
            PatientAuthIdentity.revoked_at.is_(None),
        )
    )
    if identity is None:
        raise HTTPException(
            status_code=403,
            detail="No patient account is linked to this verified identity.",
        )

    patient = await db.scalar(
        select(Patient).where(
            Patient.patient_uuid == identity.patient_id,
            Patient.is_deleted.is_(False),
        )
    )
    if patient is None:
        raise HTTPException(
            status_code=403,
            detail="No active patient account is linked to this verified identity.",
        )

    patient_id = str(patient.patient_uuid)
    access_token, expires_at = issue_patient_access_token(
        patient_id, str(supabase_user_id)
    )
    auth_session_id = hashlib.sha256(str(supabase_access_token).encode()).hexdigest()
    enrollment_token = await issue_device_enrollment_token(patient_id, auth_session_id)
    return PatientOtpVerifyResponse(
        access_token=access_token,
        expires_at=expires_at,
        patient_id=patient_id,
        device_enrollment_token=enrollment_token,
    )


def _client_ip_from_request(request: Request) -> str:
    return resolve_client_ip(request)


async def enforce_provider_login_controls(
    login_identifier: str, client_ip: str
) -> tuple[str, int]:
    """Apply privacy-preserving per-IP and per-target progressive throttles."""
    identifier_hash = hmac.new(
        get_otp_rate_limit_config().hmac_secret.encode(),
        login_identifier.strip().lower().encode(),
        hashlib.sha256,
    ).hexdigest()
    redis = get_async_redis_client()
    try:
        global_count, global_ttl = await atomic_fixed_window(
            redis, f"provider_login:ip:{hash_client_ip(client_ip)}", 60
        )
        target_count, target_ttl = await atomic_fixed_window(
            redis,
            f"provider_login:target:{hash_client_ip(client_ip)}:{identifier_hash}",
            300,
        )
        await atomic_fixed_window(
            redis, f"provider_login:anomaly:{identifier_hash}", 900
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "LOGIN_SECURITY_CONTROL_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    if global_count > 30 or target_count > 8:
        retry_after = max(
            global_ttl if global_count > 30 else 0,
            target_ttl if target_count > 8 else 0,
            1,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error_code": "LOGIN_THROTTLED",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
    if target_count > 3:
        await asyncio.sleep(min(1.0, 0.25 * (2 ** (target_count - 4))))
    return identifier_hash, target_count


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


class ProviderWebSessionResponse(BaseModel):
    authenticated: bool = True
    expires_at: datetime
    provider_uid: str
    hospital_id: UUID
    display_name: str = ""
    hospital_name: str = ""
    roles: list[str] = Field(default_factory=list)


class ProviderWebLoginState(BaseModel):
    status: str
    expires_at: datetime | None = None


class ProviderWebMfaRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=8)


def _set_web_auth_cookies(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        "nexa_provider_session",
        token,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="none",
        path="/api/v2",
    )
    response.set_cookie(
        "nexa_csrf",
        secrets.token_urlsafe(24),
        max_age=max_age,
        secure=True,
        httponly=False,
        samesite="none",
        path="/",
    )


def _clear_web_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        "nexa_provider_session",
        path="/api/v2",
        secure=True,
        httponly=True,
        samesite="none",
    )
    response.delete_cookie(
        "nexa_mfa_pending",
        path="/api/v2/auth/web",
        secure=True,
        httponly=True,
        samesite="none",
    )
    response.delete_cookie(
        "nexa_csrf",
        path="/",
        secure=True,
        httponly=False,
        samesite="none",
    )


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
    if failure is ProviderAuthFailure.MFA_SESSION_EXPIRED:
        return "MFA session expired. Sign in again."
    if failure is ProviderAuthFailure.MFA_INVALID_CODE:
        return "Invalid authenticator code."
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
    mfa_verified_at: datetime | None = None,
) -> ProviderLoginResponse:
    token = await issue_provider_session_token(
        context.provider.provider_id,
        user_agent=user_agent,
        client_ip=client_ip,
        mfa_verified_at=mfa_verified_at,
    )
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=_PROVIDER_SESSION_TTL_SECONDS
    )
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

    client_ip = _client_ip_from_request(request)
    identifier_hash, _target_count = await enforce_provider_login_controls(
        payload.login_identifier, client_ip
    )

    result = await authenticate_provider_password(
        db,
        payload.login_identifier,
        payload.password,
        payload.hospital_id,
    )

    if result.failure is ProviderAuthFailure.MFA_REQUIRED:
        assert result.mfa_pending_token is not None
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="PROVIDER_LOGIN",
            event_type="PROVIDER_MFA_REQUIRED",
            target_id=identifier_hash,
            status="MFA_REQUIRED",
        )
        return ProviderLoginMfaRequiredResponse(
            detail="Multi-factor authentication required.",
            mfa_token=result.mfa_pending_token,
        )

    if result.context is None:
        assert result.failure is not None
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="PROVIDER_LOGIN",
            event_type="PROVIDER_LOGIN_FAILED",
            target_id=identifier_hash,
            status=result.failure.value.upper(),
        )
        if result.failure is ProviderAuthFailure.MFA_NOT_CONFIGURED:
            logger.critical(
                json.dumps(
                    {
                        "event": "provider_login_mfa_not_configured",
                        "provider_login_hash": identifier_hash,
                    }
                )
            )
        raise HTTPException(
            status_code=_status_for_failure(result.failure),
            detail=_detail_for_failure(result.failure),
        )

    user_agent = request.headers.get("user-agent")
    response = await _issue_login_response(result.context, user_agent, client_ip)
    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
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
        is_binding_mismatch = (
            result.failure is ProviderAuthFailure.SESSION_BINDING_MISMATCH
        )
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
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

    response = await _issue_login_response(
        result.context,
        user_agent,
        client_ip,
        mfa_verified_at=datetime.now(timezone.utc),
    )
    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
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


@router.post("/web/login", response_model=ProviderWebLoginState)
async def provider_web_login(
    payload: ProviderLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> ProviderWebLoginState:
    """Browser login: bearer material is written only to HttpOnly cookies."""
    login_result = await provider_login(payload, request, db, None)
    if isinstance(login_result, ProviderLoginMfaRequiredResponse):
        response.set_cookie(
            "nexa_mfa_pending",
            login_result.mfa_token,
            max_age=300,
            secure=True,
            httponly=True,
            samesite="none",
            path="/api/v2/auth/web",
        )
        response.set_cookie(
            "nexa_csrf",
            secrets.token_urlsafe(24),
            max_age=300,
            secure=True,
            httponly=False,
            samesite="none",
            path="/",
        )
        return ProviderWebLoginState(status="mfa_required")
    _set_web_auth_cookies(response, login_result.access_token, login_result.expires_at)
    return ProviderWebLoginState(
        status="authenticated", expires_at=login_result.expires_at
    )


@router.post("/web/mfa/verify", response_model=ProviderWebLoginState)
async def provider_web_mfa_verify(
    payload: ProviderWebMfaRequest,
    request: Request,
    response: Response,
    pending_token: str | None = Cookie(default=None, alias="nexa_mfa_pending"),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderWebLoginState:
    if not pending_token:
        raise HTTPException(status_code=401, detail="MFA session expired")
    result = await provider_mfa_verify(
        ProviderMfaVerifyRequest(mfa_token=pending_token, totp_code=payload.totp_code),
        request,
        db,
        None,
    )
    response.delete_cookie(
        "nexa_mfa_pending",
        path="/api/v2/auth/web",
        secure=True,
        httponly=True,
        samesite="none",
    )
    _set_web_auth_cookies(response, result.access_token, result.expires_at)
    return ProviderWebLoginState(status="authenticated", expires_at=result.expires_at)


@router.get("/web/session", response_model=ProviderWebSessionResponse)
async def provider_web_session(
    provider: ProviderContext = Depends(get_current_provider),
    session_token: str | None = Cookie(default=None, alias="nexa_provider_session"),
) -> ProviderWebSessionResponse:
    if not session_token:
        raise HTTPException(status_code=401, detail="Browser session required")
    session_data = await resolve_provider_session_context(session_token)
    if not session_data or str(session_data.get("provider_id")) != str(
        provider.provider.provider_id
    ):
        raise HTTPException(status_code=401, detail="Browser session expired")
    try:
        expires_at = datetime.fromisoformat(str(session_data["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Browser session expired") from exc
    return ProviderWebSessionResponse(
        expires_at=expires_at,
        provider_uid=provider.actor_uid,
        hospital_id=provider.hospital.hospital_id,
        display_name=provider.provider.display_name,
        hospital_name=provider.hospital.display_name,
        roles=sorted(set(provider.affiliation.roles or [])),
    )


@router.post("/web/logout", status_code=204, response_model=None)
async def provider_web_logout(
    response: Response,
    provider: ProviderContext = Depends(get_current_provider),
    session_token: str | None = Cookie(default=None, alias="nexa_provider_session"),
) -> None:
    if session_token:
        await delete_provider_session_token(session_token)
    _clear_web_auth_cookies(response)
    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=provider.actor_uid,
        event_type="PROVIDER_LOGOUT",
        target_id=str(provider.provider.provider_id),
        status="SUCCESS",
    )


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

    stmt = select(ProviderCredential).where(
        ProviderCredential.provider_id == provider.provider.provider_id
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
            logger.warning(
                json.dumps(
                    {
                        "event": "provider_mfa_setup_throttled",
                        "provider_id": str(provider.provider.provider_id),
                        "elapsed_seconds": int(elapsed.total_seconds()),
                    }
                )
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="A setup is already in progress. Please wait 15 minutes or complete verification.",
            )

        logger.info(
            json.dumps(
                {
                    "event": "provider_mfa_setup_overwriting_stale_secret",
                    "provider_id": str(provider.provider.provider_id),
                    "stale_age_seconds": int(elapsed.total_seconds()),
                }
            )
        )

    secret = generate_totp_secret()
    row.mfa_secret_encrypted = encrypt_mfa_secret(secret)
    row.mfa_secret = None
    await db.commit()

    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
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

    stmt = select(ProviderCredential).where(
        ProviderCredential.provider_id == provider.provider.provider_id
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
    from app.services.provider_auth_service import verify_totp_code_once

    if not await verify_totp_code_once(
        provider.provider.provider_id,
        secret,
        payload.totp_code,
        redis_client=get_async_redis_client(),
    ):
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
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
        audit_context=current_audit_context(AuditDomain.AUTH),
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
        audit_context=current_audit_context(AuditDomain.AUTH),
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
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=provider.actor_uid,
        event_type="PROVIDER_SESSION_REFRESH",
        target_id=str(provider.provider.provider_id),
        status="SUCCESS",
    )

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=_PROVIDER_SESSION_TTL_SECONDS
    )
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
    request: Request,
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
        "hospital_id": str(provider.hospital.hospital_id),
        "session_binding": provider_session_binding(request),
        "operation": "patient_merge",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "verified": False,
    }

    redis = get_async_redis_client()
    key = f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}"
    await _maybe_await(
        redis.setex(key, _MERGE_CHALLENGE_TTL_SECONDS, json.dumps(payload))
    )

    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=provider.actor_uid,
        event_type="MERGE_CHALLENGE_CREATED",
        target_id=hashlib.sha256(challenge_token.encode("utf-8")).hexdigest(),
        status="SUCCESS",
    )

    return MergeChallengeResponse(challenge_token=challenge_token)


class MergeChallengeVerifyRequest(BaseModel):
    challenge_token: str
    totp_code: str = Field(..., min_length=6, max_length=6)


class MergeChallengeCancelRequest(BaseModel):
    challenge_token: str


@router.post("/challenge/merge/cancel", status_code=status.HTTP_200_OK)
async def cancel_merge_challenge(
    payload: MergeChallengeCancelRequest,
    request: Request,
    provider: ProviderContext = Depends(get_current_provider),
) -> None:
    redis = get_async_redis_client()
    key = f"{_MERGE_CHALLENGE_PREFIX}{payload.challenge_token}"
    cached = await _maybe_await(redis.get(key))
    if not cached:
        return
    if isinstance(cached, bytes):
        cached = cached.decode("utf-8")
    try:
        challenge_data = json.loads(cached)
    except (TypeError, json.JSONDecodeError):
        await _maybe_await(redis.delete(key))
        return
    if (
        challenge_data.get("provider_id") == str(provider.provider.provider_id)
        and challenge_data.get("hospital_id") == str(provider.hospital.hospital_id)
        and challenge_data.get("session_binding") == provider_session_binding(request)
    ):
        await _maybe_await(redis.delete(key))


@router.post("/challenge/merge/verify")
async def verify_merge_challenge(
    payload: MergeChallengeVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_current_provider),
):
    """Verify a merge challenge with a TOTP code."""
    redis = get_async_redis_client()
    key = f"{_MERGE_CHALLENGE_PREFIX}{payload.challenge_token}"
    cached = await _maybe_await(redis.get(key))
    if not cached:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge expired or invalid.",
        )

    challenge_data = json.loads(cached)
    expected_binding = provider_session_binding(request)
    if (
        challenge_data.get("provider_id") != str(provider.provider.provider_id)
        or challenge_data.get("hospital_id") != str(provider.hospital.hospital_id)
        or challenge_data.get("session_binding") != expected_binding
        or challenge_data.get("operation") != "patient_merge"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "MERGE_CHALLENGE_BINDING_MISMATCH"},
        )

    # Re-use existing MFA logic
    from app.services.provider_auth_service import (
        verify_totp_code_once,
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
    stmt = select(ProviderCredential).where(
        ProviderCredential.provider_id == provider.provider.provider_id
    )
    result = await db.execute(stmt)
    cred = result.scalar_one_or_none()
    if not cred or not cred.mfa_enabled or not cred.mfa_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA not enabled or configured for this provider.",
        )

    mfa_secret = decrypt_mfa_secret(cred.mfa_secret_encrypted)
    if not await verify_totp_code_once(
        provider.provider.provider_id, mfa_secret, payload.totp_code, redis_client=redis
    ):
        await _record_failed_mfa_attempt(provider.provider.provider_id, ip_hash)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MFA code.",
        )

    await _clear_mfa_fails(provider.provider.provider_id, ip_hash)

    challenge_data["verified"] = True
    remaining_ttl = await _maybe_await(redis.ttl(key))
    if not isinstance(remaining_ttl, int) or remaining_ttl <= 0:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error_code": "MERGE_CHALLENGE_EXPIRED"},
        )
    await _maybe_await(
        redis.setex(key, remaining_ttl, json.dumps(challenge_data, sort_keys=True))
    )

    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=provider.actor_uid,
        event_type="MERGE_CHALLENGE_VERIFIED",
        target_id=hashlib.sha256(payload.challenge_token.encode("utf-8")).hexdigest(),
        status="SUCCESS",
    )

    return {"challenge_token": payload.challenge_token, "verified": True}
