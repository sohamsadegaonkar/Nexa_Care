"""One-time provider claim for approved Treatment Session V1 authority."""

from __future__ import annotations

import inspect
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_clinical_capability
from app.core.redis import get_async_redis_client
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.models.consent_grant import ConsentGrantLog
from app.models.patient_device_keys import PatientDeviceKey
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_access_session_store import revoke_by_request
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
    TreatmentSessionV1ProtocolError,
    normalize_treatment_operations,
    treatment_context_hash_v1,
)
from app.services.treatment_session_v1_authority import (
    TreatmentSessionV1AuthorityUnavailable,
    TreatmentSessionV1ProviderIneligible,
    assert_live_treatment_session_v1_provider,
)
from app.services.treatment_session_v1_mint import (
    TREATMENT_SESSION_V1_SCOPE,
    TreatmentSessionV1AlreadyClaimed,
    TreatmentSessionV1MintError,
    TreatmentSessionV1MintStoreUnavailable,
    invalidate_treatment_session_v1_request,
    mint_treatment_session_v1_capability,
    provider_session_binding_matches,
    stage_treatment_session_v1,
    treatment_token_hash,
)

router = APIRouter(
    prefix="/api/v2/treatment-session/v1", tags=["treatment-session-v1"]
)


def _request_key(request_id: str) -> str:
    return f"treatment_session_request:{request_id}"


async def _redis_call(method, *args, **kwargs):
    result = method(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


class TreatmentSessionV1ClaimResponse(BaseModel):
    protocol_version: Literal["nexa-treatment-session-v1"]
    patient_id: str
    clinical_session_id: str
    treatment_token: str
    purpose: str
    allowed_operations: list[str]
    expires_at: str


def _authority_http(exc: Exception) -> HTTPException:
    if isinstance(exc, TreatmentSessionV1AuthorityUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CLINICAL_TRUST_UNAVAILABLE"},
        )
    if isinstance(exc, TreatmentSessionV1ProviderIneligible):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "TREATMENT_PROVIDER_NO_LONGER_ELIGIBLE",
                "denial_code": exc.denial_code.value,
            },
        )
    raise TypeError("unsupported authority error")


def _validated_context_hash(data: dict) -> str:
    try:
        expected = treatment_context_hash_v1(
            request_id=str(data.get("request_id", "")),
            patient_id=str(data.get("patient_id", "")),
            provider_id=str(data.get("provider_id", "")),
            hospital_id=str(data.get("hospital_id", "")),
            provider_session_binding_hash=str(
                data.get("provider_session_binding_hash", "")
            ),
            challenge_nonce=str(data.get("challenge_nonce", "")),
            purpose=str(data.get("purpose", "")),
            allowed_operations=data.get("allowed_operations", []),
            access_duration=int(data.get("access_duration", 0)),
            issued_at=str(data.get("created_at", "")),
            expires_at=str(data.get("expires_at", "")),
        )
    except (TreatmentSessionV1ProtocolError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_CONTEXT_INTEGRITY_FAILURE"},
        ) from exc
    stored = str(data.get("treatment_context_hash", ""))
    if not secrets.compare_digest(stored, expected):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_CONTEXT_INTEGRITY_FAILURE"},
        )
    return expected


def _parse_aware(value: object, *, error_code: str) -> datetime:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": error_code},
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": error_code},
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": error_code},
        )
    return parsed


@router.post(
    "/{request_id}/claim",
    status_code=status.HTTP_200_OK,
    response_model=TreatmentSessionV1ClaimResponse,
)
async def claim_treatment_session_v1(
    request_id: str,
    response: Response,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.CONSENT_REQUEST)
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """Mint one operation-bound session from one verified signed approval."""

    response.headers["Cache-Control"] = "no-store"
    redis = get_async_redis_client()
    try:
        raw = await _redis_call(redis.get, _request_key(request_id))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_SECURITY_STORE_UNAVAILABLE"},
        ) from exc
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "TREATMENT_REQUEST_NOT_FOUND"},
        )
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_REQUEST_CORRUPT"},
        ) from exc
    if data.get("protocol_version") != SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_PROTOCOL_MISMATCH"},
        )
    if data.get("status") != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_NOT_APPROVED"},
        )
    if (
        str(data.get("provider_id")) != provider.actor_uid
        or str(data.get("hospital_id")) != str(provider.hospital_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_PROVIDER_CONTEXT_MISMATCH"},
        )
    if not provider_session_binding_matches(
        stored_hash=data.get("provider_session_binding_hash"),
        live_binding=provider.session_binding,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_PROVIDER_SESSION_MISMATCH"},
        )

    _validated_context_hash(data)
    try:
        operations = normalize_treatment_operations(data.get("allowed_operations", []))
    except TreatmentSessionV1ProtocolError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_OPERATION_SET_INVALID"},
        ) from exc

    try:
        await assert_live_treatment_session_v1_provider(db=db, request_data=data)
    except (
        TreatmentSessionV1AuthorityUnavailable,
        TreatmentSessionV1ProviderIneligible,
    ) as exc:
        raise _authority_http(exc) from exc

    approval_expires_at = _parse_aware(
        data.get("approval_expires_at"), error_code="TREATMENT_ACCESS_WINDOW_INVALID"
    )
    if datetime.now(timezone.utc) >= approval_expires_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_ACCESS_EXPIRED"},
        )

    try:
        patient_uuid = uuid.UUID(str(data["patient_id"]))
        device_uuid = uuid.UUID(str(data["approved_device_id"]))
        key_uuid = uuid.UUID(str(data["approved_device_key_id"]))
        key_version = int(data["approved_device_key_version"])
        key_fingerprint = str(data["approved_device_key_fingerprint"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_DEVICE_BINDING_INVALID"},
        ) from exc

    key_row = (
        await db.execute(
            select(PatientDeviceKey).where(
                PatientDeviceKey.id == key_uuid,
                PatientDeviceKey.patient_id == patient_uuid,
                PatientDeviceKey.device_id == device_uuid,
                PatientDeviceKey.key_version == key_version,
                PatientDeviceKey.public_key_fingerprint == key_fingerprint,
                PatientDeviceKey.status == "active",
                PatientDeviceKey.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if key_row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_APPROVING_KEY_NO_LONGER_ACTIVE"},
        )

    existing_session = (
        await db.execute(
            select(ClinicalAccessSessionRecord).where(
                ClinicalAccessSessionRecord.consent_request_id == request_id
            )
        )
    ).scalar_one_or_none()
    if existing_session is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_SESSION_ALREADY_CLAIMED"},
        )

    token: str | None = None
    token_digest: str | None = None
    grant_row: ConsentGrantLog | None = None
    durable_session_staged = False
    try:
        token, capability = await mint_treatment_session_v1_capability(
            request_data=data,
            provider_session_binding=provider.session_binding,
        )
        if capability.allowed_operations != operations:
            raise TreatmentSessionV1MintError("TREATMENT_OPERATION_SET_INVALID")
        token_digest = treatment_token_hash(token)
        await stage_treatment_session_v1(
            db,
            capability=capability,
            token_hash=token_digest,
        )
        durable_session_staged = True

        now = datetime.now(timezone.utc)
        grant_row = ConsentGrantLog(
            token_hash=token_digest,
            patient_id=capability.patient_id,
            clinician_id=capability.provider_id,
            hospital_id=uuid.UUID(capability.hospital_id),
            purpose=capability.purpose,
            scope=[TREATMENT_SESSION_V1_SCOPE],
            is_break_glass=False,
            reason_code=None,
            issued_at=now,
            expires_at=_parse_aware(
                capability.expires_at,
                error_code="TREATMENT_ACCESS_WINDOW_INVALID",
            ),
            assurance_level="signed_device_treatment_v1",
            assurance_verified_at=now,
            request_id=request_id,
            signed_approval_id=str(data.get("approval_fingerprint", "")),
        )
        db.add(grant_row)
        await db.commit()
    except TreatmentSessionV1AlreadyClaimed as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code},
        ) from exc
    except TreatmentSessionV1MintStoreUnavailable as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": exc.code},
        ) from exc
    except TreatmentSessionV1MintError as exc:
        await db.rollback()
        try:
            await invalidate_treatment_session_v1_request(request_id)
        except TreatmentSessionV1MintStoreUnavailable:
            pass
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": exc.code},
        ) from exc
    except Exception as exc:
        await db.rollback()
        try:
            await invalidate_treatment_session_v1_request(request_id)
        except TreatmentSessionV1MintStoreUnavailable:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_SESSION_MINT_UNAVAILABLE"},
        ) from exc

    try:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=provider.actor_uid,
            event_type="CONSENT_ACCESS_CLAIMED",
            target_id=request_id,
            status="SUCCESS",
            metadata={
                "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
                "clinical_session_id": capability.session_id,
                "operation_count": len(capability.allowed_operations),
                "allowed_operations": list(capability.allowed_operations),
                "scope": TREATMENT_SESSION_V1_SCOPE,
                "device_id": str(key_row.device_id),
                "key_version": key_row.key_version,
            },
        )
    except Exception as exc:
        try:
            await invalidate_treatment_session_v1_request(request_id)
        except TreatmentSessionV1MintStoreUnavailable:
            pass
        cleanup_now = datetime.now(timezone.utc)
        try:
            if grant_row is not None:
                grant_row.revoked_at = cleanup_now
                grant_row.revoked_reason = "claim_finalization_failed"
            if durable_session_staged:
                await revoke_by_request(
                    db,
                    consent_request_id=request_id,
                    reason="CLAIM_FINALIZATION_FAILED",
                    revoked_at=cleanup_now,
                )
            await db.commit()
        except Exception:
            await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_SECURITY_AUDIT_UNAVAILABLE"},
        ) from exc

    assert token is not None
    return TreatmentSessionV1ClaimResponse(
        protocol_version=SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        patient_id=capability.patient_id,
        clinical_session_id=capability.session_id,
        treatment_token=token,
        purpose=capability.purpose,
        allowed_operations=list(capability.allowed_operations),
        expires_at=capability.expires_at,
    )
