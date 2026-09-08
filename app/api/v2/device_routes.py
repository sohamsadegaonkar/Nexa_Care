"""Patient cryptographic-device trust routes.

Only canonical ECDSA P-256 public keys are accepted. Patient private keys are
never uploaded or stored by the backend. Normal key rotation requires proof
from the currently active private key; lost-key recovery is a separate 6E path.
"""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedPatientSession, get_current_patient_session
from app.models.patient_device_keys import PatientDeviceKey
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.patient_auth_service import (
    claim_device_enrollment_token,
    finalize_device_enrollment_token,
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
from app.services.patient_session_authority import PatientSessionAuthorityUnavailable

router = APIRouter(prefix="/api/v2/patient/devices", tags=["devices"])


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
    if exc.code == "DEVICE_ROTATION_SIGNATURE_INVALID":
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


@router.post(
    "/enroll", status_code=status.HTTP_201_CREATED, response_model=DeviceEnrollResponse
)
async def enroll_device(
    payload: DeviceEnrollRequest,
    patient: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Enroll version 1 of a logical device from this exact current session."""

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
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.PLATFORM),
            actor_uid=patient_id,
            event_type="DEVICE_KEY_ENROLLED",
            target_id=patient_id,
            status="DENIED",
            metadata={"reason_code": code},
        )
        raise _http_for_device_error(PatientDeviceTrustError(code)) from exc

    try:
        pid_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}
        ) from exc

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

    # Consume Redis authority before creating durable DB authority. This ordering
    # intentionally prefers a consumed grant + no device on a later DB failure
    # over a committed device + unfinalized Redis grant. Slice 6G qualifies the
    # remaining cross-store retry behavior under injected failures.
    try:
        finalized = await finalize_device_enrollment_token(
            payload.device_enrollment_token, claim_id
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
            await append_audit_log_or_503(
                audit_context=current_audit_context(AuditDomain.PLATFORM),
                actor_uid=patient_id,
                event_type="DEVICE_KEY_ENROLLED",
                target_id=patient_id,
                status="DENIED",
                metadata={"reason_code": exc.code},
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

    # Consume one-time Redis authority before durable DB mutation. If the DB
    # transaction later fails, the challenge remains consumed and the client
    # must obtain a new one. This deliberately fails closed; 6G qualifies
    # cross-store retry/partial-failure behavior under injected races.
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
