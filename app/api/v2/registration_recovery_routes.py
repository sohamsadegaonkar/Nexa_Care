"""Patient-facing recovery for historical or corrupted registration graphs.

This surface is intentionally separate from first-time registration and from
lost-device recovery. Fresh Supabase OTP proves control of the external identity;
a server-side one-time repair capability then authorizes only the exact graph
repair classified by Nexa. Repair completion may establish account-session
authority, but never silently grants historical device authority.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.client_ip import resolve_client_ip
from app.core.database import get_db_session
from app.core.rate_limiter import (
    OtpRateLimitBackendUnavailable,
    OtpRateLimitExceeded,
    OtpRedisRateLimiter,
)
from app.core.supabase import get_supabase_client
from app.services.patient_auth_service import (
    DEVICE_ENROLLMENT_TTL_SECONDS,
    issue_device_enrollment_token,
    issue_patient_access_session,
    normalize_indian_phone,
)
from app.services.patient_device_recovery_transactions import patient_has_device_history
from app.services.patient_registration_recovery_authority import (
    REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS,
    REGISTRATION_RECOVERY_OPERATION,
    RegistrationRecoveryAttemptError,
    RegistrationRecoveryAuthorityUnavailable,
    RegistrationRecoveryCapabilityError,
    claim_registration_recovery_attempt,
    consume_registration_recovery_attempt,
    consume_registration_recovery_capability,
    issue_registration_recovery_attempt,
    record_registration_recovery_invalid_otp,
    release_registration_recovery_claim,
)
from app.services.patient_registration_recovery_review_service import (
    PatientRegistrationRecoveryReviewError,
    open_registration_recovery_review_case,
)
from app.services.patient_registration_recovery_service import (
    REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED,
    REGISTRATION_RECOVERY_NOT_AVAILABLE,
    REGISTRATION_RECOVERY_NOT_REQUIRED,
    REGISTRATION_RECOVERY_STATE_CHANGED,
    PatientRegistrationRecoveryError,
    audit_registration_recovery_required,
    inspect_patient_registration_recovery,
    repair_patient_registration_account,
)
from app.services.patient_registration_recovery_transition import (
    exchange_registration_recovery_attempt_for_capability,
)
from app.services.patient_session_authority import PatientSessionAuthorityUnavailable


router = APIRouter(prefix="/api/v2/auth/registration-recovery", tags=["auth"])
_otp_limiter = OtpRedisRateLimiter()


class RegistrationRecoveryOtpSendRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=32)


class RegistrationRecoveryOtpSendResponse(BaseModel):
    message: str
    registration_recovery_attempt_token: str


class RegistrationRecoveryOtpVerifyRequest(RegistrationRecoveryOtpSendRequest):
    otp: str = Field(..., pattern=r"^\d{6}$")
    registration_recovery_attempt_token: str = Field(..., min_length=32, max_length=512)


class RegistrationRecoveryCapabilityResponse(BaseModel):
    registration_recovery_token: str
    operation: str
    repair_kind: str
    expires_in_seconds: int
    expires_at: str


class RegistrationRecoveryCompleteRequest(BaseModel):
    registration_recovery_token: str = Field(..., min_length=32, max_length=512)


class RegistrationRecoveryCompleteResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    patient_id: str
    repair_kind: str
    device_authority_state: str
    device_enrollment_token: str | None = None
    device_enrollment_expires_in_seconds: int | None = None


def _normalize_phone_or_422(phone: str) -> str:
    try:
        return normalize_indian_phone(phone)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "INVALID_PHONE_FORMAT"},
        ) from exc


async def _enforce_limits(request: Request, *, phone: str, action: str) -> None:
    try:
        await _otp_limiter.check(
            action=f"patient_registration_recovery_{action}",
            ip=resolve_client_ip(request) or "unknown",
            normalized_phone=phone,
        )
    except OtpRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "REGISTRATION_RECOVERY_OTP_RATE_LIMITED"},
        ) from exc
    except OtpRateLimitBackendUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_SECURITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc


def _attempt_http_error(exc: RegistrationRecoveryAttemptError) -> HTTPException:
    if exc.code == "REGISTRATION_RECOVERY_ATTEMPT_IN_PROGRESS":
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_401_UNAUTHORIZED
    return HTTPException(status_code=code, detail={"error_code": exc.code})


def _capability_http_error(exc: RegistrationRecoveryCapabilityError) -> HTTPException:
    code = (
        status.HTTP_409_CONFLICT
        if exc.code == "REGISTRATION_RECOVERY_CAPABILITY_ALREADY_ISSUED"
        else status.HTTP_401_UNAUTHORIZED
    )
    return HTTPException(status_code=code, detail={"error_code": exc.code})


async def _consume_attempt_or_http_error(
    *, token: str, phone: str, claim
) -> None:
    try:
        await consume_registration_recovery_attempt(token, phone, claim)
    except RegistrationRecoveryAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_SECURITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except RegistrationRecoveryAttemptError as exc:
        raise _attempt_http_error(exc) from exc


@router.post(
    "/otp/send",
    response_model=RegistrationRecoveryOtpSendResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_otp_send(
    payload: RegistrationRecoveryOtpSendRequest,
    request: Request,
) -> RegistrationRecoveryOtpSendResponse:
    """Initiate neutral account-repair identity proof without creating users."""

    phone = _normalize_phone_or_422(payload.phone)
    await _enforce_limits(request, phone=phone, action="send")
    try:
        await run_in_threadpool(
            get_supabase_client().auth.sign_in_with_otp,
            {"phone": phone, "options": {"should_create_user": False}},
        )
    except Exception as exc:
        provider_code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if provider_code not in {400, 401, 403, 422}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "REGISTRATION_RECOVERY_SMS_UNAVAILABLE",
                    "retryable": True,
                },
            ) from None

    try:
        attempt_token = await issue_registration_recovery_attempt(phone)
    except RegistrationRecoveryAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_SECURITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    return RegistrationRecoveryOtpSendResponse(
        message="If this identity is eligible for account recovery, an OTP will be sent.",
        registration_recovery_attempt_token=attempt_token,
    )


@router.post(
    "/otp/verify",
    response_model=RegistrationRecoveryCapabilityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def registration_recovery_otp_verify(
    payload: RegistrationRecoveryOtpVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> RegistrationRecoveryCapabilityResponse:
    """Verify identity, classify the durable graph, and mint exact repair authority."""

    phone = _normalize_phone_or_422(payload.phone)
    await _enforce_limits(request, phone=phone, action="verify")
    try:
        claim = await claim_registration_recovery_attempt(
            payload.registration_recovery_attempt_token, phone
        )
    except RegistrationRecoveryAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_SECURITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except RegistrationRecoveryAttemptError as exc:
        raise _attempt_http_error(exc) from exc

    try:
        result = await run_in_threadpool(
            get_supabase_client().auth.verify_otp,
            {"phone": phone, "token": payload.otp, "type": "sms"},
        )
    except Exception as exc:
        provider_code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if provider_code in {400, 401, 403}:
            try:
                await record_registration_recovery_invalid_otp(
                    payload.registration_recovery_attempt_token, phone, claim
                )
            except RegistrationRecoveryAuthorityUnavailable as budget_exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error_code": "REGISTRATION_RECOVERY_SECURITY_UNAVAILABLE",
                        "retryable": True,
                    },
                ) from budget_exc
            except RegistrationRecoveryAttemptError as budget_exc:
                raise _attempt_http_error(budget_exc) from budget_exc
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "REGISTRATION_RECOVERY_OTP_INVALID"},
            ) from None
        try:
            await release_registration_recovery_claim(
                payload.registration_recovery_attempt_token, phone, claim
            )
        except RegistrationRecoveryAuthorityUnavailable:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_SMS_UNAVAILABLE",
                "retryable": True,
            },
        ) from None

    user = getattr(result, "user", None)
    verified_phone = getattr(user, "phone", None)
    provider_subject = getattr(user, "id", None)
    provider_session = getattr(result, "session", None)
    provider_access_token = getattr(provider_session, "access_token", None)
    try:
        authoritative_phone = normalize_indian_phone(str(verified_phone))
    except (TypeError, ValueError):
        authoritative_phone = ""
    if (
        not user
        or not provider_subject
        or not provider_access_token
        or authoritative_phone != phone
    ):
        try:
            await release_registration_recovery_claim(
                payload.registration_recovery_attempt_token, phone, claim
            )
        except RegistrationRecoveryAuthorityUnavailable:
            pass
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "REGISTRATION_RECOVERY_IDENTITY_MISMATCH"},
        )

    try:
        inspection = await inspect_patient_registration_recovery(
            db, provider_subject=str(provider_subject)
        )
    except PatientRegistrationRecoveryError as exc:
        await db.rollback()
        try:
            await consume_registration_recovery_attempt(
                payload.registration_recovery_attempt_token, phone, claim
            )
        except (RegistrationRecoveryAuthorityUnavailable, RegistrationRecoveryAttemptError):
            pass
        if exc.code == REGISTRATION_RECOVERY_NOT_AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error_code": exc.code},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "REGISTRATION_RECOVERY_UNAVAILABLE", "retryable": True},
        ) from None
    except Exception:
        await db.rollback()
        try:
            await release_registration_recovery_claim(
                payload.registration_recovery_attempt_token, phone, claim
            )
        except RegistrationRecoveryAuthorityUnavailable:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "REGISTRATION_RECOVERY_UNAVAILABLE", "retryable": True},
        ) from None

    if inspection.disposition == "not_required":
        await db.rollback()
        await _consume_attempt_or_http_error(
            token=payload.registration_recovery_attempt_token,
            phone=phone,
            claim=claim,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": REGISTRATION_RECOVERY_NOT_REQUIRED},
        )

    if inspection.disposition == "manual_review":
        try:
            await audit_registration_recovery_required(
                db, inspection=inspection, attempt_id=claim.attempt_id
            )
            review_case = await open_registration_recovery_review_case(
                db,
                inspection=inspection,
                attempt_id=claim.attempt_id,
            )
            await db.commit()
        except PatientRegistrationRecoveryReviewError:
            await db.rollback()
            await _consume_attempt_or_http_error(
                token=payload.registration_recovery_attempt_token,
                phone=phone,
                claim=claim,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": REGISTRATION_RECOVERY_STATE_CHANGED},
            ) from None
        except Exception:
            await db.rollback()
            try:
                await release_registration_recovery_claim(
                    payload.registration_recovery_attempt_token, phone, claim
                )
            except RegistrationRecoveryAuthorityUnavailable:
                pass
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE",
                    "retryable": True,
                },
            ) from None

        await _consume_attempt_or_http_error(
            token=payload.registration_recovery_attempt_token,
            phone=phone,
            claim=claim,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED,
                "case_reference": review_case.case_reference,
            },
        )

    try:
        await audit_registration_recovery_required(
            db, inspection=inspection, attempt_id=claim.attempt_id
        )
        await db.commit()
    except Exception:
        await db.rollback()
        try:
            await release_registration_recovery_claim(
                payload.registration_recovery_attempt_token, phone, claim
            )
        except RegistrationRecoveryAuthorityUnavailable:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "REGISTRATION_RECOVERY_AUDIT_UNAVAILABLE", "retryable": True},
        ) from None

    if not inspection.repairable or inspection.repair_kind is None:
        await _consume_attempt_or_http_error(
            token=payload.registration_recovery_attempt_token,
            phone=phone,
            claim=claim,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": REGISTRATION_RECOVERY_STATE_CHANGED},
        )

    try:
        capability = await exchange_registration_recovery_attempt_for_capability(
            attempt_token=payload.registration_recovery_attempt_token,
            phone=phone,
            claim=claim,
            patient_id=inspection.patient_id,
            provider_subject=inspection.provider_subject,
            repair_kind=inspection.repair_kind,
            graph_fingerprint=inspection.graph_fingerprint,
        )
    except RegistrationRecoveryAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_SECURITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except RegistrationRecoveryAttemptError as exc:
        raise _attempt_http_error(exc) from exc
    except RegistrationRecoveryCapabilityError as exc:
        raise _capability_http_error(exc) from exc

    return RegistrationRecoveryCapabilityResponse(
        registration_recovery_token=capability.token,
        operation=REGISTRATION_RECOVERY_OPERATION,
        repair_kind=capability.repair_kind,
        expires_in_seconds=REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS,
        expires_at=capability.expires_at,
    )


@router.post(
    "/complete",
    response_model=RegistrationRecoveryCompleteResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_complete(
    payload: RegistrationRecoveryCompleteRequest,
    db: AsyncSession = Depends(get_db_session),
) -> RegistrationRecoveryCompleteResponse:
    """Consume exact repair authority, mutate the graph, then establish login authority."""

    try:
        capability = await consume_registration_recovery_capability(
            payload.registration_recovery_token
        )
    except RegistrationRecoveryAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_SECURITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except RegistrationRecoveryCapabilityError as exc:
        raise _capability_http_error(exc) from exc

    try:
        repaired = await repair_patient_registration_account(db, capability=capability)
    except PatientRegistrationRecoveryError as exc:
        await db.rollback()
        if exc.code == REGISTRATION_RECOVERY_STATE_CHANGED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": exc.code},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_RESTART_REQUIRED",
                "retryable": False,
            },
        ) from None
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_RESTART_REQUIRED",
                "retryable": False,
            },
        ) from None

    try:
        access_token, expires_at, session_id = await issue_patient_access_session(
            repaired.patient_id, repaired.provider_subject
        )
    except PatientSessionAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
                "retryable": False,
                "account_repaired": True,
            },
        ) from exc

    try:
        patient_uuid = uuid.UUID(repaired.patient_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
                "retryable": False,
                "account_repaired": True,
            },
        ) from exc

    try:
        has_device_history = await patient_has_device_history(db, patient_id=patient_uuid)
        await db.rollback()
    except Exception:
        await db.rollback()
        has_device_history = True

    enrollment_token: str | None = None
    device_state = "existing_device_required"
    enrollment_ttl: int | None = None
    if not has_device_history:
        try:
            enrollment_token = await issue_device_enrollment_token(
                repaired.patient_id, session_id
            )
        except PatientSessionAuthorityUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
                    "retryable": False,
                    "account_repaired": True,
                },
            ) from exc
        device_state = "bootstrap_enrollment"
        enrollment_ttl = DEVICE_ENROLLMENT_TTL_SECONDS

    return RegistrationRecoveryCompleteResponse(
        access_token=access_token,
        expires_at=expires_at.isoformat(),
        patient_id=repaired.patient_id,
        repair_kind=repaired.repair_kind,
        device_authority_state=device_state,
        device_enrollment_token=enrollment_token,
        device_enrollment_expires_in_seconds=enrollment_ttl,
    )
