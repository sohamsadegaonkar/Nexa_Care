"""Patient-signed Treatment Session V1 request/challenge/approval lifecycle.

This surface is intentionally separate from Signed Consent V3. It creates and
resolves operation-bound patient signatures, but it does not mint a clinical
session or authorize any write by itself.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedPatientSession,
    capture_clinical_initiation_assurance,
    get_current_patient_session,
    require_clinical_capability,
)
from app.core.redis import get_async_redis_client
from app.models.patient_device_keys import PatientDeviceKey
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, bind_trusted_audit_hospital, current_audit_context
from app.security.provider_capabilities import ClinicalCapability
from app.services.patient_discovery_service import (
    DiscoveryHandleInvalid,
    DiscoveryUnavailable,
    PatientDiscoveryService,
)
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
    SignedTreatmentSessionV1Verifier,
    TreatmentSessionV1ProtocolError,
    normalize_treatment_operations,
    treatment_context_hash_v1,
)
from app.services.treatment_session_v1_authority import (
    TreatmentSessionV1AuthorityUnavailable,
    TreatmentSessionV1ProviderIneligible,
    assert_live_treatment_session_v1_provider,
)

router = APIRouter(
    prefix="/api/v2/treatment-session/v1", tags=["treatment-session-v1"]
)

_REQUEST_TTL_SECONDS = 120


async def _redis_call(method, *args, **kwargs):
    result = method(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


class TreatmentSessionV1RequestPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["nexa-treatment-session-v1"]
    discovery_handle: str = Field(min_length=32, max_length=256)
    purpose: str = Field(min_length=1, max_length=64)
    allowed_operations: list[str] = Field(min_length=1, max_length=8)
    access_duration_seconds: int = Field(default=900, ge=300, le=3600)


class TreatmentSessionV1RequestResponse(BaseModel):
    protocol_version: Literal["nexa-treatment-session-v1"]
    request_id: str
    status: Literal["pending"]
    expires_in_seconds: int
    challenge_nonce: str
    treatment_context_hash: str


class TreatmentSessionV1ChallengePayload(BaseModel):
    protocol_version: Literal["nexa-treatment-session-v1"]
    request_id: str
    patient_id: str
    provider_id: str
    hospital_id: str
    provider_name: str
    hospital_name: str
    purpose: str
    allowed_operations: list[str]
    access_duration: int
    challenge_nonce: str
    issued_at: str
    expires_at: str
    treatment_context_hash: str
    status: Literal["pending"]


class SignedTreatmentSessionV1RequestPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["nexa-treatment-session-v1"]
    request_id: str
    patient_id: str
    decision: Literal["approved", "denied"]
    challenge_nonce: str
    treatment_context_hash: str = Field(min_length=64, max_length=64)
    signature: str
    device_id: str
    key_id: str
    key_version: int = Field(ge=1)
    public_key_fingerprint: str = Field(min_length=64, max_length=64)


class SignedTreatmentSessionV1ResponsePayload(BaseModel):
    protocol_version: Literal["nexa-treatment-session-v1"]
    request_id: str
    status: Literal["approved", "denied"]
    responded_at: str


_PROMOTE_REQUEST_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local data = cjson.decode(raw)
if data.status ~= 'pending_audit' or data.protocol_version ~= 'nexa-treatment-session-v1' then return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return 0 end
data.status = 'pending'
redis.call('SET', KEYS[1], cjson.encode(data), 'PX', ttl)
return 1
"""


_RESOLVE_REQUEST_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local data = cjson.decode(raw)
if data.protocol_version ~= 'nexa-treatment-session-v1' then return 0 end
if data.status ~= 'pending' or data.challenge_nonce ~= ARGV[1] then return 0 end
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('SET', KEYS[2], '1', 'EX', 300)
redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
return 1
"""


def _request_key(request_id: str) -> str:
    return f"treatment_session_request:{request_id}"


def _nonce_key(nonce: str) -> str:
    return f"signed_treatment_session_v1_nonce:{nonce}:used"


def _provider_session_binding_hash(provider: ProviderContext) -> str:
    raw = provider.session_binding
    if not isinstance(raw, str) or not raw:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_PROVIDER_SESSION_BINDING_REQUIRED"},
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _context_from_data(data: dict) -> dict:
    return {
        "request_id": str(data.get("request_id", "")),
        "patient_id": str(data.get("patient_id", "")),
        "provider_id": str(data.get("provider_id", "")),
        "hospital_id": str(data.get("hospital_id", "")),
        "provider_session_binding_hash": str(
            data.get("provider_session_binding_hash", "")
        ),
        "challenge_nonce": str(data.get("challenge_nonce", "")),
        "purpose": str(data.get("purpose", "")),
        "allowed_operations": data.get("allowed_operations", []),
        "access_duration": int(data.get("access_duration", 0)),
        "issued_at": str(data.get("created_at", "")),
        "expires_at": str(data.get("expires_at", "")),
    }


def _validated_context_hash(data: dict) -> str:
    try:
        expected = treatment_context_hash_v1(**_context_from_data(data))
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


async def _promote_request(redis, request_id: str) -> bool:
    result = await _redis_call(
        redis.eval,
        _PROMOTE_REQUEST_LUA,
        1,
        _request_key(request_id),
    )
    return int(result) == 1


async def _resolve_request(
    redis, *, request_id: str, nonce: str, data: dict, ttl_seconds: int
) -> bool:
    result = await _redis_call(
        redis.eval,
        _RESOLVE_REQUEST_LUA,
        2,
        _request_key(request_id),
        _nonce_key(nonce),
        nonce,
        json.dumps(data, sort_keys=True),
        ttl_seconds,
    )
    return int(result) == 1


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


@router.post(
    "/request",
    status_code=status.HTTP_201_CREATED,
    response_model=TreatmentSessionV1RequestResponse,
)
async def create_treatment_session_v1_request(
    request: Request,
    payload: TreatmentSessionV1RequestPayload,
    response: Response,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.CONSENT_REQUEST)
    ),
    db: AsyncSession = Depends(get_db_session),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        operations = normalize_treatment_operations(payload.allowed_operations)
    except TreatmentSessionV1ProtocolError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "TREATMENT_OPERATION_SET_INVALID"},
        ) from exc

    try:
        patient = await PatientDiscoveryService(
            db, get_async_redis_client()
        ).consume_handle(
            raw_handle=payload.discovery_handle,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
        )
    except DiscoveryHandleInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "DISCOVERY_HANDLE_INVALID_OR_EXPIRED"},
        ) from exc
    except DiscoveryUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "DISCOVERY_UNAVAILABLE"},
        ) from exc

    patient_uuid = patient.patient_uuid
    patient_id = str(patient_uuid)
    active_key = (
        await db.execute(
            select(PatientDeviceKey)
            .where(
                PatientDeviceKey.patient_id == patient_uuid,
                PatientDeviceKey.status == "active",
                PatientDeviceKey.revoked_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "PATIENT_ACTIVE_DEVICE_REQUIRED"},
        )

    initiation = await capture_clinical_initiation_assurance(request, provider, db)
    request_id = str(uuid.uuid4())
    challenge_nonce = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_REQUEST_TTL_SECONDS)
    provider_binding_hash = _provider_session_binding_hash(provider)
    context = {
        "request_id": request_id,
        "patient_id": patient_id,
        "provider_id": provider.actor_uid,
        "hospital_id": str(provider.hospital_id),
        "provider_session_binding_hash": provider_binding_hash,
        "challenge_nonce": challenge_nonce,
        "purpose": payload.purpose,
        "allowed_operations": operations,
        "access_duration": payload.access_duration_seconds,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    context_hash = treatment_context_hash_v1(**context)
    request_data = {
        **context,
        "allowed_operations": list(operations),
        "created_at": now.isoformat(),
        "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        "treatment_context_hash": context_hash,
        "provider_name": provider.provider.display_name,
        "hospital_name": provider.hospital.display_name,
        "status": "pending_audit",
        "clinical_initiated_at": initiation.initiated_at.isoformat(),
        "clinical_authentication_method": initiation.authentication_method.value,
        "clinical_mfa_verified_at": initiation.mfa_verified_at.isoformat(),
        "clinical_assurance_policy_version": initiation.assurance_policy_version,
    }
    redis = get_async_redis_client()
    try:
        await _redis_call(
            redis.set,
            _request_key(request_id),
            json.dumps(request_data, sort_keys=True),
            ex=_REQUEST_TTL_SECONDS,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_SECURITY_STORE_UNAVAILABLE"},
        ) from exc

    try:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=provider.actor_uid,
            event_type="CONSENT_REQUEST_CREATED",
            target_id=request_id,
            status="SUCCESS",
            metadata={
                "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
                "operation_count": len(operations),
            },
        )
    except Exception as exc:
        try:
            await _redis_call(redis.delete, _request_key(request_id))
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_SECURITY_AUDIT_UNAVAILABLE"},
        ) from exc

    try:
        promoted = await _promote_request(redis, request_id)
    except Exception:
        promoted = False
    if not promoted:
        try:
            await _redis_call(redis.delete, _request_key(request_id))
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_SECURITY_STORE_UNAVAILABLE"},
        )

    return TreatmentSessionV1RequestResponse(
        protocol_version=SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        request_id=request_id,
        status="pending",
        expires_in_seconds=_REQUEST_TTL_SECONDS,
        challenge_nonce=challenge_nonce,
        treatment_context_hash=context_hash,
    )


@router.get(
    "/challenge/{request_id}",
    status_code=status.HTTP_200_OK,
    response_model=TreatmentSessionV1ChallengePayload,
)
async def get_treatment_session_v1_challenge(
    request_id: str,
    response: Response,
    session: AuthenticatedPatientSession = Depends(get_current_patient_session),
):
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
            detail={"error_code": "TREATMENT_CHALLENGE_NOT_FOUND"},
        )
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if data.get("protocol_version") != SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_PROTOCOL_MISMATCH"},
        )
    if data.get("status") != "pending":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "TREATMENT_CHALLENGE_NOT_FOUND"},
        )
    if str(data.get("patient_id")) != session.patient_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_PATIENT_MISMATCH"},
        )
    context_hash = _validated_context_hash(data)
    return TreatmentSessionV1ChallengePayload(
        protocol_version=SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        request_id=str(data["request_id"]),
        patient_id=session.patient_id,
        provider_id=str(data["provider_id"]),
        hospital_id=str(data["hospital_id"]),
        provider_name=str(data["provider_name"]),
        hospital_name=str(data["hospital_name"]),
        purpose=str(data["purpose"]),
        allowed_operations=list(data["allowed_operations"]),
        access_duration=int(data["access_duration"]),
        challenge_nonce=str(data["challenge_nonce"]),
        issued_at=str(data["created_at"]),
        expires_at=str(data["expires_at"]),
        treatment_context_hash=context_hash,
        status="pending",
    )


@router.post(
    "/approve-signed",
    status_code=status.HTTP_200_OK,
    response_model=SignedTreatmentSessionV1ResponsePayload,
)
async def approve_signed_treatment_session_v1(
    payload: SignedTreatmentSessionV1RequestPayload,
    response: Response,
    session: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    response.headers["Cache-Control"] = "no-store"
    if payload.patient_id != session.patient_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_PATIENT_MISMATCH"},
        )

    redis = get_async_redis_client()
    try:
        raw = await _redis_call(redis.get, _request_key(payload.request_id))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_SECURITY_STORE_UNAVAILABLE"},
        ) from exc
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "TREATMENT_CHALLENGE_NOT_FOUND"},
        )
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if data.get("protocol_version") != SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_PROTOCOL_MISMATCH"},
        )
    if str(data.get("patient_id")) != session.patient_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "TREATMENT_PATIENT_MISMATCH"},
        )

    trusted_hospital = str(data.get("hospital_id", ""))
    if trusted_hospital:
        bind_trusted_audit_hospital(trusted_hospital)

    context_hash = _validated_context_hash(data)
    if not secrets.compare_digest(payload.treatment_context_hash, context_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "TREATMENT_CONTEXT_MISMATCH"},
        )
    if payload.challenge_nonce != data.get("challenge_nonce"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "TREATMENT_CHALLENGE_NONCE_MISMATCH"},
        )

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "protocol_version": payload.protocol_version,
                "request_id": payload.request_id,
                "patient_id": payload.patient_id,
                "decision": payload.decision,
                "challenge_nonce": payload.challenge_nonce,
                "treatment_context_hash": payload.treatment_context_hash,
                "device_id": payload.device_id,
                "key_id": payload.key_id,
                "key_version": payload.key_version,
                "public_key_fingerprint": payload.public_key_fingerprint,
                "signature": payload.signature,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    if data.get("status") == payload.decision and secrets.compare_digest(
        str(data.get("approval_fingerprint", "")), fingerprint
    ):
        return SignedTreatmentSessionV1ResponsePayload(
            protocol_version=SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
            request_id=payload.request_id,
            status=payload.decision,
            responded_at=str(data.get("responded_at")),
        )

    if data.get("status") != "pending" or await _redis_call(
        redis.get, _nonce_key(payload.challenge_nonce)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_REPLAY_REJECTED"},
        )

    try:
        await assert_live_treatment_session_v1_provider(db=db, request_data=data)
    except (
        TreatmentSessionV1AuthorityUnavailable,
        TreatmentSessionV1ProviderIneligible,
    ) as exc:
        raise _authority_http(exc) from exc

    result = await SignedTreatmentSessionV1Verifier().verify(
        db=db,
        patient_id=session.patient_id,
        request_id=payload.request_id,
        provider_id=str(data.get("provider_id", "")),
        hospital_id=str(data.get("hospital_id", "")),
        provider_session_binding_hash=str(
            data.get("provider_session_binding_hash", "")
        ),
        challenge_nonce=payload.challenge_nonce,
        decision=payload.decision,
        signature_b64=payload.signature,
        purpose=str(data.get("purpose", "")),
        allowed_operations=data.get("allowed_operations", []),
        access_duration=int(data.get("access_duration", 0)),
        issued_at=str(data.get("created_at", "")),
        expires_at=str(data.get("expires_at", "")),
        treatment_context_hash=context_hash,
        device_id=payload.device_id,
        key_id=payload.key_id,
        key_version=payload.key_version,
        public_key_fingerprint=payload.public_key_fingerprint,
    )
    if not result.verified:
        if result.error == "Challenge expired":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error_code": "TREATMENT_CHALLENGE_EXPIRED"},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "TREATMENT_SIGNATURE_INVALID"},
        )

    now = datetime.now(timezone.utc)
    data["status"] = payload.decision
    data["responded_at"] = now.isoformat()
    data["approved_device_id"] = result.device_id
    data["approved_device_key_id"] = result.key_id
    data["approved_device_key_version"] = result.key_version
    data["approved_device_key_fingerprint"] = result.public_key_fingerprint
    data["approval_fingerprint"] = fingerprint
    if payload.decision == "approved":
        data["approval_expires_at"] = (
            now + timedelta(seconds=int(data.get("access_duration", 0)))
        ).isoformat()

    ttl_seconds = (
        int(data.get("access_duration", 0))
        if payload.decision == "approved"
        else 300
    )
    if not await _resolve_request(
        redis,
        request_id=payload.request_id,
        nonce=payload.challenge_nonce,
        data=data,
        ttl_seconds=ttl_seconds,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TREATMENT_REPLAY_REJECTED"},
        )

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.CONSENT),
        actor_uid=session.patient_id,
        event_type=(
            "CONSENT_APPROVED_SIGNED"
            if payload.decision == "approved"
            else "CONSENT_DENIED_SIGNED"
        ),
        target_id=payload.request_id,
        status="SUCCESS",
        metadata={
            "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
            "device_id": result.device_id,
            "key_id": result.key_id,
            "key_version": result.key_version,
            "operation_count": len(result.allowed_operations),
        },
    )

    return SignedTreatmentSessionV1ResponsePayload(
        protocol_version=SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        request_id=payload.request_id,
        status=payload.decision,
        responded_at=now.isoformat(),
    )
