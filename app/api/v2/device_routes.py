"""Device enrollment routes for Nexa Care V2 (Workstream 2).

Manages patient enrolled hardware cryptographic public keys (ECDSA P-256).
Never stores private keys server-side.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import base64
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_scoped_session
from app.models.patient_device_keys import PatientDeviceKey
from app.observability.audit_ledger import append_audit_log_or_503
from app.services.patient_auth_service import (
    claim_device_enrollment_token,
    finalize_device_enrollment_token,
    release_device_enrollment_claim,
)

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/patient/devices", tags=["devices"])


class DeviceEnrollRequest(BaseModel):
    device_public_key: str = Field(..., description="Base64 DER-encoded ECDSA P-256 public key")
    device_label: str = Field(..., max_length=100, description="Friendly name e.g. iPhone 14")
    platform: str = Field(..., max_length=20, description="ios or android")
    expo_push_token: str | None = None
    device_enrollment_token: str = Field(..., min_length=32, max_length=256)


class DeviceEnrollResponse(BaseModel):
    device_id: str
    status: str
    patient_id: str
    enrolled_at: str


class EnrolledDeviceInfo(BaseModel):
    device_id: str
    device_label: str | None
    platform: str
    status: str
    enrolled_at: str
    public_key_fingerprint: str


class EnrolledDevicesListResponse(BaseModel):
    patient_id: str
    devices: list[EnrolledDeviceInfo]


@router.post("/enroll", status_code=status.HTTP_201_CREATED, response_model=DeviceEnrollResponse)
async def enroll_device(
    payload: DeviceEnrollRequest,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Enroll a mobile device public key (ECDSA P-256) for biometric consent signing."""
    try:
        raw_key = base64.b64decode(payload.device_public_key, validate=True)
        pub_key = serialization.load_der_public_key(raw_key)
    except (ValueError, UnsupportedAlgorithm, Exception) as exc:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.PLATFORM),
            actor_uid=patient_id,
            event_type="DEVICE_KEY_ENROLLED",
            target_id=patient_id,
            status="FAILED",
            metadata={"reason": "invalid_public_key_format"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid DER public key encoding or unsupported key format.",
        ) from exc

    if not (
        isinstance(pub_key, ec.EllipticCurvePublicKey)
        and isinstance(pub_key.curve, ec.SECP256R1)
    ):
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.PLATFORM),
            actor_uid=patient_id,
            event_type="DEVICE_KEY_ENROLLED",
            target_id=patient_id,
            status="FAILED",
            metadata={"reason": "non_p256_curve"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Public key must be an ECDSA P-256 (SECP256R1) key.",
        )

    try:
        pid_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}) from exc

    # Check active device limit (max 5 active devices per patient)
    stmt_count = select(func.count(PatientDeviceKey.id)).where(
        PatientDeviceKey.patient_id == pid_uuid,
        PatientDeviceKey.status == "active",
    )
    result_count = await db.execute(stmt_count)
    active_count = result_count.scalar() or 0
    if active_count >= 5:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Maximum of 5 active devices reached for this patient.",
        )

    # Check if this exact key is already enrolled
    stmt_existing = select(PatientDeviceKey).where(
        PatientDeviceKey.patient_id == pid_uuid,
        PatientDeviceKey.device_public_key == raw_key,
    )
    res_existing = await db.execute(stmt_existing)
    existing = res_existing.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    claim_id = await claim_device_enrollment_token(payload.device_enrollment_token, patient_id)
    if claim_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired device enrollment token.")

    try:
        if existing:
            existing.status = "active"
            existing.device_label = payload.device_label
            existing.platform = payload.platform
            existing.revoked_at = None
            device_id = str(existing.id)
        else:
            new_key = PatientDeviceKey(
                patient_id=pid_uuid,
                device_public_key=raw_key,
                device_label=payload.device_label,
                platform=payload.platform,
                key_algorithm="ECDSA-P256",
                status="active",
                enrolled_at=now,
            )
            db.add(new_key)
            await db.flush()
            device_id = str(new_key.id or uuid.uuid4())

        await db.commit()
    except Exception:
        await release_device_enrollment_claim(payload.device_enrollment_token, claim_id)
        raise

    if not await finalize_device_enrollment_token(payload.device_enrollment_token, claim_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device enrollment token was already consumed.")

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PLATFORM),
        actor_uid=patient_id,
        event_type="DEVICE_KEY_ENROLLED",
        target_id=device_id,
        status="SUCCESS",
        metadata={"platform": payload.platform, "device_label": payload.device_label},
    )

    return DeviceEnrollResponse(
        device_id=device_id,
        status="active",
        patient_id=patient_id,
        enrolled_at=now.isoformat(),
    )


@router.get("", status_code=status.HTTP_200_OK, response_model=EnrolledDevicesListResponse)
async def list_devices(
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    """List active enrolled devices for a patient. Never returns raw public keys."""
    try:
        pid_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}) from exc

    stmt = select(PatientDeviceKey).where(
        PatientDeviceKey.patient_id == pid_uuid,
    ).order_by(PatientDeviceKey.enrolled_at.desc())
    res = await db.execute(stmt)
    rows = res.scalars().all()

    devices = [
        EnrolledDeviceInfo(
            device_id=str(row.id),
            device_label=row.device_label,
            platform=row.platform,
            status=row.status,
            enrolled_at=row.enrolled_at.isoformat(),
            public_key_fingerprint=hashlib.sha256(row.device_public_key).hexdigest(),
        )
        for row in rows
    ]
    return EnrolledDevicesListResponse(patient_id=patient_id, devices=devices)


class DeviceRevokeResponse(BaseModel):
    device_id: str
    status: str
    revoked_at: str


@router.post("/{device_id}/revoke", status_code=status.HTTP_200_OK, response_model=DeviceRevokeResponse)
async def revoke_device(
    device_id: str,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Immediately revoke a patient hardware device key."""
    try:
        pid_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}) from exc

    try:
        dev_uuid = uuid.UUID(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid device_id UUID") from exc

    stmt = select(PatientDeviceKey).where(
        PatientDeviceKey.id == dev_uuid,
        PatientDeviceKey.patient_id == pid_uuid,
    )
    res = await db.execute(stmt)
    device = res.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device key not found")

    now = datetime.now(timezone.utc)
    device.status = "revoked"
    device.revoked_at = now
    await db.commit()

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PLATFORM),
        actor_uid=patient_id,
        event_type="DEVICE_KEY_REVOKED",
        target_id=device_id,
        status="SUCCESS",
    )

    return DeviceRevokeResponse(
        device_id=device_id,
        status="revoked",
        revoked_at=now.isoformat(),
    )
