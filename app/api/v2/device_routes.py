"""Patient cryptographic-device trust routes.

Only canonical ECDSA P-256 public keys are accepted. Patient private keys are
never uploaded or stored by the backend. Normal key rotation requires proof
from the currently active private key. Slice 6E adds trusted-device enrollment
and explicit lost-device recovery without treating account authentication as
cryptographic device authority.
"""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.client_ip import resolve_client_ip
from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedPatientSession, get_current_patient_session
from app.core.rate_limiter import (
    OtpRateLimitBackendUnavailable,
    OtpRateLimitExceeded,
    OtpRedisRateLimiter,
)
from app.core.supabase import get_supabase_client
from app.models.patient_device_keys import PatientDeviceKey
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.patient_auth_service import (
    claim_device_enrollment_token,
    finalize_device_enrollment_token,
    issue_patient_access_session,
    normalize_indian_phone,
)
from app.services.patient_device_recovery import (
    PATIENT_RECOVERY_OPERATION,
    PATIENT_RECOVERY_TTL_SECONDS,
    TRUSTED_ENROLLMENT_OPERATION,
    TRUSTED_ENROLLMENT_PROTOCOL_VERSION,
    PatientRecoveryAuthorityUnavailable,
    PatientRecoveryCapabilityError,
    TrustedEnrollmentAuthorityUnavailable,
    TrustedEnrollmentChallengeError,
    consume_patient_recovery_capability,
    consume_trusted_enrollment_challenge,
    issue_patient_recovery_capability,
    issue_trusted_enrollment_challenge,
)
from app.services.patient_device_recovery_transactions import (
    enroll_patient_device_from_trusted_authorizer,
    patient_has_device_history,
    recover_patient_device_authority,
)
from app.services.patient_device_rotation import (
    DEVICE_ROTATION_OPERATION,
    DEVICE_ROTATION_PROTOCOL_VERSION,
    DeviceRotationAuthorityUnavailable,
    DeviceRotationChallengeError,
    consume_device_rotation_challenge,
    issue_device_rotation_challenge,
)
from app.services.patient_device_trust import (
    PatientDeviceTrustError,
    assert_rotation_new_key_available,
    canonicalize_p256_public_key,
    enroll_patient_device_key,
    get_active_patient_device_key,
    revoke_patient_device,
    rotate_patient_device_key,
)
from app.services.patient_session_authority import (
    PatientSessionAuthorityUnavailable,
    revoke_all_patient_sessions,
)

router = APIRouter(prefix="/api/v2/patient/devices", tags=["devices"])
_recovery_otp_rate_limiter = OtpRedisRateLimiter()


class DeviceEnrollRequest(BaseModel):
    device_public_key: str = Field(
        ..., description="Base64 DER-encoded ECDSA P-256 public key"
    )
    device_label: str = Field(
        ..., max_length=100, description="Friendly name e.g. iPhone 14"
    )
    platform: str = Field(..., max_length=20, description="ios or android")
    expo_push_token: str | None = None
    device_enrollment_token: str = Field(..., min_length=32, max_length=256)


class DeviceEnrollResponse(BaseModel):
    device_id: str
    key_id: str
    key_version: int
    status: str
    patient_id: str
    enrolled_at: str


class EnrolledDeviceInfo(BaseModel):
    device_id: str
    key_id: str
    key_version: int
    device_label: str | None
    platform: str
    status: str
    enrolled_at: str
    revoked_at: str | None = None
    revocation_reason_code: str | None = None
    public_key_fingerprint: str


class EnrolledDevicesListResponse(BaseModel):
    patient_id: str
    devices: list[EnrolledDeviceInfo]


class DeviceRevokeResponse(BaseModel):
    device_id: str
    key_id: str
    key_version: int
    status: str
    revoked_at: str


class DeviceRotationChallengeRequest(BaseModel):
    new_device_public_key: str = Field(
        ..., description="Base64 DER-encoded replacement ECDSA P-256 public key"
    )


class DeviceRotationChallengeResponse(BaseModel):
    challenge_nonce: str
    protocol_version: str
    operation: str
    device_id: str
    current_key_version: int
    new_public_key_fingerprint: str
    issued_at: str
    expires_at: str
    signing_payload_b64: str


class DeviceRotateRequest(BaseModel):
    challenge_nonce: str = Field(..., min_length=32, max_length=256)
    current_key_version: int = Field(..., ge=1)
    new_device_public_key: str
    signature: str = Field(..., min_length=16, max_length=1024)


class DeviceRotateResponse(BaseModel):
    device_id: str
    old_key_id: str
    new_key_id: str
    old_key_version: int
    new_key_version: int
    new_public_key_fingerprint: str
    status: str
    rotated_at: str


class TrustedEnrollmentChallengeRequest(BaseModel):
    new_device_public_key: str = Field(
        ..., description="Base64 DER-encoded public key for the new logical device"
    )


class TrustedEnrollmentChallengeResponse(BaseModel):
    challenge_nonce: str
    protocol_version: str
    operation: str
    authorizer_device_id: str
    authorizer_key_version: int
    new_public_key_fingerprint: str
    issued_at: str
    expires_at: str
    signing_payload_b64: str


class TrustedEnrollmentAuthorizeRequest(BaseModel):
    challenge_nonce: str = Field(..., min_length=32, max_length=256)
    authorizer_key_version: int = Field(..., ge=1)
    new_device_public_key: str
    signature: str = Field(..., min_length=16, max_length=1024)
    device_label: str = Field(..., max_length=100)
    platform: str = Field(..., max_length=20)


class RecoveryOtpSendRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=32)


class RecoveryOtpVerifyRequest(RecoveryOtpSendRequest):
    otp: str = Field(..., pattern=r"^\d{6}$")


class RecoveryOtpSendResponse(BaseModel):
    message: str


class RecoveryCapabilityResponse(BaseModel):
    recovery_token: str
    operation: str
    expires_in_seconds: int
    expires_at: str


class RecoveryCompleteRequest(BaseModel):
    recovery_token: str = Field(..., min_length=32, max_length=256)
    new_device_public_key: str
    device_label: str = Field(..., max_length=100)
    platform: str = Field(..., max_length=20)


class RecoveryCompleteResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    patient_id: str
    device_id: str
    key_id: str
    key_version: int
    status: str
    public_key_fingerprint: str
    revoked_device_count: int


def _http_for_device_error(exc: PatientDeviceTrustError) -> HTTPException:
    if exc.code in {"DEVICE_PUBLIC_KEY_INVALID", "DEVICE_PUBLIC_KEY_NOT_P256"}:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": exc.code},
        )
    if exc.code == "DEVICE_NOT_FOUND":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": exc.code},
        )
    if exc.code in {
        "DEVICE_ROTATION_SIGNATURE_INVALID",
        "TRUSTED_ENROLLMENT_SIGNATURE_INVALID",
    }:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": exc.code},
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": exc.code},
    )


def _http_for_rotation_challenge_error(
    exc: DeviceRotationChallengeError,
) -> HTTPException:
    if exc.code == "DEVICE_ROTATION_SESSION_INACTIVE":
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": exc.code},
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": exc.code},
    )


def _http_for_trusted_challenge_error(
    exc: TrustedEnrollmentChallengeError,
) -> HTTPException:
    if exc.code == "TRUSTED_ENROLLMENT_SESSION_INACTIVE":
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": exc.code},
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": exc.code},
    )


def _http_for_recovery_capability_error(
    exc: PatientRecoveryCapabilityError,
) -> HTTPException:
    if exc.code in {
        "PATIENT_RECOVERY_SESSION_INACTIVE",
        "PATIENT_RECOVERY_IDENTITY_MISMATCH",
        "PATIENT_RECOVERY_CAPABILITY_INVALID",
        "PATIENT_RECOVERY_CAPABILITY_EXPIRED",
    }:
        code = status.HTTP_401_UNAUTHORIZED
    else:
        code = status.HTTP_409_CONFLICT
    return HTTPException(status_code=code, detail={"error_code": exc.code})


async def _audit_rotation_denied(
    *, patient_id: str, device_id: str, reason_code: str
) -> None:
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PLATFORM),
        actor_uid=patient_id,
        event_type="DEVICE_KEY_ENROLLED",
        target_id=device_id,
        status="DENIED",
        metadata={
            "operation": "device_key_rotation",
            "reason_code": reason_code,
        },
    )


async def _audit_device_enrollment_denied(
    *, patient_id: str, reason_code: str, operation: str
) -> None:
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PLATFORM),
        actor_uid=patient_id,
        event_type="DEVICE_KEY_ENROLLED",
        target_id=patient_id,
        status="DENIED",
        metadata={"operation": operation, "reason_code": reason_code},
    )


def _normalize_phone_or_422(phone: str) -> str:
    try:
        return normalize_indian_phone(phone)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "INVALID_PHONE_FORMAT"},
        ) from exc


async def _enforce_recovery_otp_limits(
    request: Request, *, phone: str, action: str
) -> None:
    try:
        await _recovery_otp_rate_limiter.check(
            action=f"patient_recovery_{action}",
            ip=resolve_client_ip(request) or "unknown",
            normalized_phone=phone,
        )
    except OtpRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "PATIENT_RECOVERY_OTP_RATE_LIMITED"},
        ) from exc
    except OtpRateLimitBackendUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_RECOVERY_OTP_SECURITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc


@router.post(
    "/enroll", status_code=status.HTTP_201_CREATED, response_model=DeviceEnrollResponse
)
async def enroll_device(
    payload: DeviceEnrollRequest,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Bootstrap the first device only; account login never replaces device trust."""

    patient_id = patient.patient_id
    try:
        raw_key = base64.b64decode(payload.device_public_key, validate=True)
        canonicalize_p256_public_key(raw_key)
    except (ValueError, PatientDeviceTrustError) as exc:
        code = (
            exc.code
            if isinstance(exc, PatientDeviceTrustError)
            else "DEVICE_PUBLIC_KEY_INVALID"
        )
        await _audit_device_enrollment_denied(
            patient_id=patient_id, reason_code=code, operation="bootstrap_enrollment"
        )
        raise _http_for_device_error(PatientDeviceTrustError(code)) from exc

    try:
        pid_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}
        ) from exc

    if await patient_has_device_history(db, patient_id=pid_uuid):
        await _audit_device_enrollment_denied(
            patient_id=patient_id,
            reason_code="DEVICE_RECOVERY_REQUIRED",
            operation="bootstrap_enrollment",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "DEVICE_RECOVERY_REQUIRED"},
        )
    await db.rollback()

    try:
        claim_id = await claim_device_enrollment_token(
            payload.device_enrollment_token, patient_id, patient.session_id
        )
    except PatientSessionAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    if claim_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "DEVICE_ENROLLMENT_GRANT_INVALID"},
        )

    try:
        finalized = await finalize_device_enrollment_token(
            payload.device_enrollment_token,
            claim_id,
            patient_id=patient_id,
            auth_session_id=patient.session_id,
        )
    except PatientSessionAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    if not finalized:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "DEVICE_ENROLLMENT_GRANT_ALREADY_CONSUMED"},
        )

    try:
        row = await enroll_patient_device_key(
            db,
            patient_id=pid_uuid,
            raw_public_key=raw_key,
            device_label=payload.device_label,
            platform=payload.platform,
            actor_id=patient_id,
        )
    except PatientDeviceTrustError as exc:
        if exc.code in {
            "DEVICE_KEY_RESURRECTION_FORBIDDEN",
            "DEVICE_KEY_ALREADY_ENROLLED",
            "DEVICE_ACTIVE_LIMIT_REACHED",
        }:
            await _audit_device_enrollment_denied(
                patient_id=patient_id,
                reason_code=exc.code,
                operation="bootstrap_enrollment",
            )
        raise _http_for_device_error(exc) from exc

    return DeviceEnrollResponse(
        device_id=str(row.device_id),
        key_id=str(row.id),
        key_version=row.key_version,
        status=row.status,
        patient_id=patient_id,
        enrolled_at=row.enrolled_at.isoformat(),
    )


@router.get(
    "", status_code=status.HTTP_200_OK, response_model=EnrolledDevicesListResponse
)
async def list_devices(
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """List this patient's device/key lifecycle without exposing raw keys."""

    patient_id = patient.patient_id
    try:
        pid_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}
        ) from exc

    rows = (
        (
            await db.execute(
                select(PatientDeviceKey)
                .where(PatientDeviceKey.patient_id == pid_uuid)
                .order_by(
                    PatientDeviceKey.enrolled_at.desc(),
                    PatientDeviceKey.key_version.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    devices = [
        EnrolledDeviceInfo(
            device_id=str(row.device_id),
            key_id=str(row.id),
            key_version=row.key_version,
            device_label=row.device_label,
            platform=row.platform,
            status=row.status,
            enrolled_at=row.enrolled_at.isoformat(),
            revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
            revocation_reason_code=row.revocation_reason_code,
            public_key_fingerprint=row.public_key_fingerprint,
        )
        for row in rows
    ]
    return EnrolledDevicesListResponse(patient_id=patient_id, devices=devices)


@router.post(
    "/{device_id}/trusted-enrollment/challenge",
    status_code=status.HTTP_201_CREATED,
    response_model=TrustedEnrollmentChallengeResponse,
)
async def issue_trusted_device_enrollment_challenge(
    device_id: str,
    payload: TrustedEnrollmentChallengeRequest,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Bind a prospective new device to proof from one current trusted device."""

    patient_id = patient.patient_id
    try:
        pid_uuid = uuid.UUID(patient_id)
        dev_uuid = uuid.UUID(device_id)
        raw_new_key = base64.b64decode(payload.new_device_public_key, validate=True)
        canonical = canonicalize_p256_public_key(raw_new_key)
        authorizer = await get_active_patient_device_key(
            db, patient_id=pid_uuid, device_id=dev_uuid
        )
        await assert_rotation_new_key_available(
            db, public_key_fingerprint=canonical.fingerprint
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "DEVICE_PUBLIC_KEY_INVALID"},
        ) from exc
    except PatientDeviceTrustError as exc:
        await _audit_device_enrollment_denied(
            patient_id=patient_id,
            reason_code=exc.code,
            operation="trusted_device_enrollment",
        )
        raise _http_for_device_error(exc) from exc

    try:
        challenge = await issue_trusted_enrollment_challenge(
            patient_id=patient_id,
            session_id=patient.session_id,
            authorizer_device_id=device_id,
            authorizer_key_version=authorizer.key_version,
            new_public_key_fingerprint=canonical.fingerprint,
        )
    except TrustedEnrollmentAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "TRUSTED_ENROLLMENT_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except TrustedEnrollmentChallengeError as exc:
        raise _http_for_trusted_challenge_error(exc) from exc

    return TrustedEnrollmentChallengeResponse(
        challenge_nonce=challenge.nonce,
        protocol_version=TRUSTED_ENROLLMENT_PROTOCOL_VERSION,
        operation=TRUSTED_ENROLLMENT_OPERATION,
        authorizer_device_id=device_id,
        authorizer_key_version=challenge.authorizer_key_version,
        new_public_key_fingerprint=challenge.new_public_key_fingerprint,
        issued_at=challenge.issued_at,
        expires_at=challenge.expires_at,
        signing_payload_b64=base64.b64encode(challenge.signing_payload).decode("ascii"),
    )


@router.post(
    "/{device_id}/trusted-enrollment/authorize",
    status_code=status.HTTP_201_CREATED,
    response_model=DeviceEnrollResponse,
)
async def authorize_trusted_device_enrollment(
    device_id: str,
    payload: TrustedEnrollmentAuthorizeRequest,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Enroll a new logical device only after current-device proof succeeds."""

    patient_id = patient.patient_id
    try:
        pid_uuid = uuid.UUID(patient_id)
        dev_uuid = uuid.UUID(device_id)
        raw_new_key = base64.b64decode(payload.new_device_public_key, validate=True)
        canonical = canonicalize_p256_public_key(raw_new_key)
    except (ValueError, PatientDeviceTrustError) as exc:
        code = (
            exc.code
            if isinstance(exc, PatientDeviceTrustError)
            else "DEVICE_PUBLIC_KEY_INVALID"
        )
        raise _http_for_device_error(PatientDeviceTrustError(code)) from exc

    try:
        challenge = await consume_trusted_enrollment_challenge(
            challenge_nonce=payload.challenge_nonce,
            patient_id=patient_id,
            session_id=patient.session_id,
            authorizer_device_id=device_id,
            authorizer_key_version=payload.authorizer_key_version,
            new_public_key_fingerprint=canonical.fingerprint,
        )
    except TrustedEnrollmentAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "TRUSTED_ENROLLMENT_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except TrustedEnrollmentChallengeError as exc:
        await _audit_device_enrollment_denied(
            patient_id=patient_id,
            reason_code=exc.code,
            operation="trusted_device_enrollment",
        )
        raise _http_for_trusted_challenge_error(exc) from exc

    await db.rollback()
    try:
        row = await enroll_patient_device_from_trusted_authorizer(
            db,
            patient_id=pid_uuid,
            authorizer_device_id=dev_uuid,
            expected_authorizer_key_version=payload.authorizer_key_version,
            raw_new_public_key=raw_new_key,
            signing_payload=challenge.signing_payload,
            signature_b64=payload.signature,
            device_label=payload.device_label,
            platform=payload.platform,
            actor_id=patient_id,
        )
    except PatientDeviceTrustError as exc:
        await _audit_device_enrollment_denied(
            patient_id=patient_id,
            reason_code=exc.code,
            operation="trusted_device_enrollment",
        )
        raise _http_for_device_error(exc) from exc

    return DeviceEnrollResponse(
        device_id=str(row.device_id),
        key_id=str(row.id),
        key_version=row.key_version,
        status=row.status,
        patient_id=patient_id,
        enrolled_at=row.enrolled_at.isoformat(),
    )


@router.post(
    "/recovery/otp/send",
    status_code=status.HTTP_200_OK,
    response_model=RecoveryOtpSendResponse,
)
async def recovery_otp_send(
    payload: RecoveryOtpSendRequest,
    request: Request,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
) -> RecoveryOtpSendResponse:
    """Send the second fresh OTP required for all-devices-lost recovery."""

    phone = _normalize_phone_or_422(payload.phone)
    await _enforce_recovery_otp_limits(request, phone=phone, action="send")
    try:
        await run_in_threadpool(
            get_supabase_client().auth.sign_in_with_otp,
            {"phone": phone, "options": {"should_create_user": False}},
        )
    except Exception as exc:
        code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if code not in {400, 401, 403, 422}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "PATIENT_RECOVERY_SMS_UNAVAILABLE",
                    "retryable": True,
                },
            ) from None
    return RecoveryOtpSendResponse(
        message="If this identity is eligible for recovery, an OTP will be sent."
    )


@router.post(
    "/recovery/otp/verify",
    status_code=status.HTTP_201_CREATED,
    response_model=RecoveryCapabilityResponse,
)
async def recovery_otp_verify(
    payload: RecoveryOtpVerifyRequest,
    request: Request,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
) -> RecoveryCapabilityResponse:
    """Verify fresh Supabase identity and mint one exact-session recovery capability."""

    phone = _normalize_phone_or_422(payload.phone)
    await _enforce_recovery_otp_limits(request, phone=phone, action="verify")
    try:
        result = await run_in_threadpool(
            get_supabase_client().auth.verify_otp,
            {"phone": phone, "token": payload.otp, "type": "sms"},
        )
    except Exception as exc:
        code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if code in {400, 401, 403}:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "PATIENT_RECOVERY_OTP_INVALID"},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_RECOVERY_SMS_UNAVAILABLE",
                "retryable": True,
            },
        ) from None

    user = getattr(result, "user", None)
    verified_phone = getattr(user, "phone", None)
    subject = getattr(user, "id", None)
    upstream_session = getattr(result, "session", None)
    upstream_token = getattr(upstream_session, "access_token", None)
    try:
        authoritative_phone = normalize_indian_phone(str(verified_phone))
    except (TypeError, ValueError):
        authoritative_phone = ""
    if (
        not user
        or not subject
        or not upstream_token
        or authoritative_phone != phone
        or str(subject) != patient.supabase_user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "PATIENT_RECOVERY_IDENTITY_MISMATCH"},
        )

    try:
        pid_uuid = uuid.UUID(patient.patient_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "INVALID_PATIENT_ID"},
        ) from exc
    if not await patient_has_device_history(db, patient_id=pid_uuid):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "DEVICE_RECOVERY_NOT_REQUIRED"},
        )

    try:
        capability = await issue_patient_recovery_capability(
            patient_id=patient.patient_id,
            session_id=patient.session_id,
            supabase_user_id=patient.supabase_user_id,
        )
    except PatientRecoveryAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_RECOVERY_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except PatientRecoveryCapabilityError as exc:
        raise _http_for_recovery_capability_error(exc) from exc

    return RecoveryCapabilityResponse(
        recovery_token=capability.token,
        operation=PATIENT_RECOVERY_OPERATION,
        expires_in_seconds=PATIENT_RECOVERY_TTL_SECONDS,
        expires_at=capability.expires_at,
    )


@router.post(
    "/recovery/complete",
    status_code=status.HTTP_200_OK,
    response_model=RecoveryCompleteResponse,
)
async def recovery_complete(
    payload: RecoveryCompleteRequest,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
) -> RecoveryCompleteResponse:
    """Replace all old device authority with one fresh key after recovery proof."""

    try:
        pid_uuid = uuid.UUID(patient.patient_id)
        raw_new_key = base64.b64decode(payload.new_device_public_key, validate=True)
        canonicalize_p256_public_key(raw_new_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "DEVICE_PUBLIC_KEY_INVALID"},
        ) from exc
    except PatientDeviceTrustError as exc:
        raise _http_for_device_error(exc) from exc

    try:
        await consume_patient_recovery_capability(
            token=payload.recovery_token,
            patient_id=patient.patient_id,
            session_id=patient.session_id,
            supabase_user_id=patient.supabase_user_id,
        )
    except PatientRecoveryAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_RECOVERY_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except PatientRecoveryCapabilityError as exc:
        raise _http_for_recovery_capability_error(exc) from exc

    try:
        new_epoch = await revoke_all_patient_sessions(patient.patient_id)
    except PatientSessionAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=patient.patient_id,
        event_type="PATIENT_SESSIONS_REVOKED",
        target_id=patient.patient_id,
        status="SUCCESS",
        metadata={
            "scope": "all_sessions",
            "operation": "account_recovery",
            "session_epoch": new_epoch,
        },
    )

    await db.rollback()
    try:
        recovery = await recover_patient_device_authority(
            db,
            patient_id=pid_uuid,
            raw_new_public_key=raw_new_key,
            device_label=payload.device_label,
            platform=payload.platform,
            actor_id=patient.patient_id,
        )
    except PatientDeviceTrustError as exc:
        await _audit_device_enrollment_denied(
            patient_id=patient.patient_id,
            reason_code=exc.code,
            operation="account_recovery",
        )
        raise _http_for_device_error(exc) from exc

    try:
        access_token, expires_at, _session_id = await issue_patient_access_session(
            patient.patient_id, patient.supabase_user_id
        )
    except PatientSessionAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "PATIENT_RECOVERY_SESSION_REISSUE_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc

    return RecoveryCompleteResponse(
        access_token=access_token,
        expires_at=expires_at.isoformat(),
        patient_id=patient.patient_id,
        device_id=str(recovery.device_id),
        key_id=str(recovery.key_id),
        key_version=recovery.key_version,
        status=recovery.status,
        public_key_fingerprint=recovery.public_key_fingerprint,
        revoked_device_count=recovery.revoked_device_count,
    )


@router.post(
    "/{device_id}/rotation/challenge",
    status_code=status.HTTP_201_CREATED,
    response_model=DeviceRotationChallengeResponse,
)
async def issue_rotation_challenge(
    device_id: str,
    payload: DeviceRotationChallengeRequest,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Issue a one-time proof-of-possession challenge for the current key."""

    patient_id = patient.patient_id
    try:
        pid_uuid = uuid.UUID(patient_id)
        dev_uuid = uuid.UUID(device_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "INVALID_DEVICE_ID"},
        ) from exc

    try:
        raw_new_key = base64.b64decode(payload.new_device_public_key, validate=True)
        canonical = canonicalize_p256_public_key(raw_new_key)
        current = await get_active_patient_device_key(
            db, patient_id=pid_uuid, device_id=dev_uuid
        )
        await assert_rotation_new_key_available(
            db, public_key_fingerprint=canonical.fingerprint
        )
    except PatientDeviceTrustError as exc:
        await _audit_rotation_denied(
            patient_id=patient_id, device_id=device_id, reason_code=exc.code
        )
        raise _http_for_device_error(exc) from exc
    except ValueError as exc:
        await _audit_rotation_denied(
            patient_id=patient_id,
            device_id=device_id,
            reason_code="DEVICE_PUBLIC_KEY_INVALID",
        )
        raise _http_for_device_error(
            PatientDeviceTrustError("DEVICE_PUBLIC_KEY_INVALID")
        ) from exc

    try:
        challenge = await issue_device_rotation_challenge(
            patient_id=patient_id,
            session_id=patient.session_id,
            device_id=device_id,
            current_key_version=current.key_version,
            new_public_key_fingerprint=canonical.fingerprint,
        )
    except DeviceRotationAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "DEVICE_ROTATION_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except DeviceRotationChallengeError as exc:
        await _audit_rotation_denied(
            patient_id=patient_id, device_id=device_id, reason_code=exc.code
        )
        raise _http_for_rotation_challenge_error(exc) from exc

    return DeviceRotationChallengeResponse(
        challenge_nonce=challenge.nonce,
        protocol_version=DEVICE_ROTATION_PROTOCOL_VERSION,
        operation=DEVICE_ROTATION_OPERATION,
        device_id=device_id,
        current_key_version=challenge.current_key_version,
        new_public_key_fingerprint=challenge.new_public_key_fingerprint,
        issued_at=challenge.issued_at,
        expires_at=challenge.expires_at,
        signing_payload_b64=base64.b64encode(challenge.signing_payload).decode("ascii"),
    )


@router.post(
    "/{device_id}/rotate",
    status_code=status.HTTP_200_OK,
    response_model=DeviceRotateResponse,
)
async def rotate_device(
    device_id: str,
    payload: DeviceRotateRequest,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Rotate only when the exact old active private key proves possession."""

    patient_id = patient.patient_id
    try:
        pid_uuid = uuid.UUID(patient_id)
        dev_uuid = uuid.UUID(device_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "INVALID_DEVICE_ID"},
        ) from exc

    try:
        raw_new_key = base64.b64decode(payload.new_device_public_key, validate=True)
        canonical = canonicalize_p256_public_key(raw_new_key)
    except (ValueError, PatientDeviceTrustError) as exc:
        code = (
            exc.code
            if isinstance(exc, PatientDeviceTrustError)
            else "DEVICE_PUBLIC_KEY_INVALID"
        )
        await _audit_rotation_denied(
            patient_id=patient_id, device_id=device_id, reason_code=code
        )
        raise _http_for_device_error(PatientDeviceTrustError(code)) from exc

    try:
        challenge = await consume_device_rotation_challenge(
            challenge_nonce=payload.challenge_nonce,
            patient_id=patient_id,
            session_id=patient.session_id,
            device_id=device_id,
            current_key_version=payload.current_key_version,
            new_public_key_fingerprint=canonical.fingerprint,
        )
    except DeviceRotationAuthorityUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "DEVICE_ROTATION_AUTHORITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except DeviceRotationChallengeError as exc:
        await _audit_rotation_denied(
            patient_id=patient_id, device_id=device_id, reason_code=exc.code
        )
        raise _http_for_rotation_challenge_error(exc) from exc

    try:
        result = await rotate_patient_device_key(
            db,
            patient_id=pid_uuid,
            device_id=dev_uuid,
            expected_key_version=payload.current_key_version,
            raw_new_public_key=raw_new_key,
            signing_payload=challenge.signing_payload,
            signature_b64=payload.signature,
            actor_id=patient_id,
        )
    except PatientDeviceTrustError as exc:
        await _audit_rotation_denied(
            patient_id=patient_id, device_id=device_id, reason_code=exc.code
        )
        raise _http_for_device_error(exc) from exc

    return DeviceRotateResponse(
        device_id=str(result.device_id),
        old_key_id=str(result.old_key_id),
        new_key_id=str(result.new_key_id),
        old_key_version=result.old_key_version,
        new_key_version=result.new_key_version,
        new_public_key_fingerprint=result.new_public_key_fingerprint,
        status=result.status,
        rotated_at=result.rotated_at.isoformat(),
    )


@router.post(
    "/{device_id}/revoke",
    status_code=status.HTTP_200_OK,
    response_model=DeviceRevokeResponse,
)
async def revoke_device(
    device_id: str,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Terminally revoke the current key of one logical patient device."""

    patient_id = patient.patient_id
    try:
        pid_uuid = uuid.UUID(patient_id)
        dev_uuid = uuid.UUID(device_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "INVALID_DEVICE_ID"},
        ) from exc

    try:
        row = await revoke_patient_device(
            db,
            patient_id=pid_uuid,
            device_id=dev_uuid,
            actor_id=patient_id,
        )
    except PatientDeviceTrustError as exc:
        raise _http_for_device_error(exc) from exc

    assert row.revoked_at is not None
    return DeviceRevokeResponse(
        device_id=str(row.device_id),
        key_id=str(row.id),
        key_version=row.key_version,
        status=row.status,
        revoked_at=row.revoked_at.isoformat(),
    )
