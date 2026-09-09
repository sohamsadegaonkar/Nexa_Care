"""Signed Consent V3 routes.

This router is intentionally versioned separately from the legacy V2 signed
approval surface.  New consent authority is created only through this V3
protocol: complete context binding, exact logical-device/key-version binding,
and live provider-trust revalidation are mandatory.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedPatientSession,
    capture_clinical_initiation_assurance,
    get_current_patient_session,
    require_clinical_capability,
)
from app.core.rate_limiter import ConcurrentPushLimiter
from app.core.redis import get_async_redis_client
from app.models.consent_grant import ConsentGrantLog
from app.models.patient_device_keys import PatientDeviceKey
from app.models.push_token import PatientPushToken
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, bind_trusted_audit_hospital, current_audit_context
from app.security.document_processing_policy import DOCUMENT_PROCESSING_PURPOSE, DOCUMENT_PROCESSING_SCOPE
from app.security.provider_capabilities import ClinicalCapability
from app.services.approved_access_capability import (
    ApprovedAccessClaimInProgress,
    ApprovedAccessStoreUnavailable,
    invalidate_request,
    issue_from_approved_request,
)
from app.services.consent_v3_authority import (
    ConsentV3AuthorityUnavailable,
    ConsentV3ProviderIneligible,
    assert_live_consent_v3_provider,
)
from app.services.patient_discovery_service import (
    DiscoveryHandleInvalid,
    DiscoveryUnavailable,
    PatientDiscoveryService,
)
from app.services.push_notification_service import PushNotificationService
from app.services.signed_consent_v3 import (
    SIGNED_CONSENT_V3_PROTOCOL_VERSION,
    SignedConsentV3Verifier,
    consent_context_hash_v3,
)

logger = logging.getLogger("nexa_logger")
router = APIRouter(prefix="/api/v2/consent/v3", tags=["consent-v3"])
push_notification_service = PushNotificationService()
push_request_limiter = ConcurrentPushLimiter()


def _token_hash(token: str) -> str:
    clean = token[len("nexa:consent:") :] if token.startswith("nexa:consent:") else token
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


async def _redis_call(method, *args, **kwargs):
    result = method(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


class ConsentV3RequestPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["nexa-consent-v3"]
    discovery_handle: str = Field(min_length=32, max_length=256)
    purpose: str = "routine_checkup"
    scope: str = "clinical"
    access_duration_seconds: int = 900

    @model_validator(mode="after")
    def validate_purpose_scope(self):
        if self.purpose == DOCUMENT_PROCESSING_PURPOSE:
            if self.scope != DOCUMENT_PROCESSING_SCOPE:
                raise ValueError("document_processing requires the documents scope")
        elif self.scope == DOCUMENT_PROCESSING_SCOPE:
            raise ValueError("documents scope requires the document_processing purpose")
        elif self.scope not in {"clinical", "full"}:
            raise ValueError("unsupported consent scope")
        return self


class ConsentV3RequestResponse(BaseModel):
    protocol_version: Literal["nexa-consent-v3"]
    request_id: str
    status: Literal["pending"]
    expires_in_seconds: int
    challenge_nonce: str
    notification_dispatch: Literal["queued", "unavailable"]
    notification_queued: bool
    delivery_status: Literal["queued", "unavailable"]


class ConsentV3ChallengePayload(BaseModel):
    protocol_version: Literal["nexa-consent-v3"]
    request_id: str
    patient_id: str
    provider_id: str
    hospital_id: str
    provider_name: str
    hospital_name: str
    purpose: str
    scope: str
    access_duration: int
    challenge_nonce: str
    issued_at: str
    expires_at: str
    consent_context_hash: str
    status: Literal["pending"]


class SignedConsentV3RequestPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["nexa-consent-v3"]
    request_id: str
    patient_id: str
    decision: Literal["approved", "denied"]
    challenge_nonce: str
    consent_context_hash: str = Field(min_length=64, max_length=64)
    signature: str
    device_id: str
    key_id: str
    key_version: int = Field(ge=1)
    public_key_fingerprint: str = Field(min_length=64, max_length=64)


class SignedConsentV3ResponsePayload(BaseModel):
    protocol_version: Literal["nexa-consent-v3"]
    request_id: str
    status: Literal["approved", "denied"]
    responded_at: str


class ConsentV3AccessClaimResponse(BaseModel):
    protocol_version: Literal["nexa-consent-v3"]
    patient_id: str
    consent_token: str
    purpose: str
    scope: str
    expires_at: str


_PROMOTE_V3_REQUEST_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local data = cjson.decode(raw)
if data.status ~= 'pending_audit' or data.protocol_version ~= 'nexa-consent-v3' then return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return 0 end
data.status = 'pending'
redis.call('SET', KEYS[1], cjson.encode(data), 'PX', ttl)
return 1
"""


_RESOLVE_V3_REQUEST_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local data = cjson.decode(raw)
if data.protocol_version ~= 'nexa-consent-v3' then return 0 end
if data.status ~= 'pending' or data.challenge_nonce ~= ARGV[1] then return 0 end
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('SET', KEYS[2], '1', 'EX', 300)
redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
return 1
"""


async def _promote_request(redis, request_id: str) -> bool:
    result = await _redis_call(
        redis.eval,
        _PROMOTE_V3_REQUEST_LUA,
        1,
        f"consent_request:{request_id}",
    )
    return int(result) == 1


async def _resolve_request(
    redis, *, request_id: str, nonce: str, data: dict, ttl_seconds: int
) -> bool:
    result = await _redis_call(
        redis.eval,
        _RESOLVE_V3_REQUEST_LUA,
        2,
        f"consent_request:{request_id}",
        f"signed_consent_v3_nonce:{nonce}:used",
        nonce,
        json.dumps(data, sort_keys=True),
        ttl_seconds,
    )
    return int(result) == 1


async def _deliver_notification(
    *,
    request_id: str,
    patient_id: str,
    provider_name: str,
    purpose: str,
    expo_push_token: str,
) -> None:
    redis = get_async_redis_client()
    key = f"consent_request:{request_id}"
    raw = await _redis_call(redis.get, key)
    if not raw:
        return
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if data.get("status") != "pending" or data.get("protocol_version") != SIGNED_CONSENT_V3_PROTOCOL_VERSION:
        return
    result = await push_notification_service.send_approval_request(
        patient_id=patient_id,
        request_id=request_id,
        provider_name=provider_name,
        purpose=purpose,
        expo_push_token=expo_push_token,
    )
    raw = await _redis_call(redis.get, key)
    if not raw:
        return
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if data.get("status") != "pending":
        return
    data["delivery_status"] = "sent" if result.success else "failed"
    data["delivery_error"] = None if result.success else "PUSH_DELIVERY_FAILED"
    data["delivery_completed_at"] = datetime.now(timezone.utc).isoformat()
    ttl = await _redis_call(redis.ttl, key)
    await _redis_call(redis.set, key, json.dumps(data), ex=ttl if isinstance(ttl, int) and ttl > 0 else 120)


def _authority_http(exc: Exception) -> HTTPException:
    if isinstance(exc, ConsentV3AuthorityUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CLINICAL_TRUST_UNAVAILABLE"},
        )
    if isinstance(exc, ConsentV3ProviderIneligible):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "CONSENT_PROVIDER_NO_LONGER_ELIGIBLE",
                "denial_code": exc.denial_code.value,
            },
        )
    raise TypeError("unsupported authority error")


def _context_from_data(data: dict) -> dict:
    return {
        "request_id": str(data.get("request_id", "")),
        "patient_id": str(data.get("patient_id", "")),
        "provider_id": str(data.get("provider_id", "")),
        "hospital_id": str(data.get("hospital_id", "")),
        "challenge_nonce": str(data.get("challenge_nonce", "")),
        "purpose": str(data.get("purpose", "")),
        "scope": str(data.get("scope", "")),
        "access_duration": int(data.get("access_duration", 0)),
        "issued_at": str(data.get("created_at", "")),
        "expires_at": str(data.get("expires_at", "")),
    }


def _validated_context_hash(data: dict) -> str:
    expected = consent_context_hash_v3(**_context_from_data(data))
    stored = str(data.get("consent_context_hash", ""))
    if not secrets.compare_digest(stored, expected):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "CONSENT_CONTEXT_INTEGRITY_FAILURE"},
        )
    return expected


@router.post(
    "/request",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsentV3RequestResponse,
)
async def create_consent_v3_request(
    request: Request,
    payload: ConsentV3RequestPayload,
    background_tasks: BackgroundTasks,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.CONSENT_REQUEST)
    ),
    db: AsyncSession = Depends(get_db_session),
):
    access_duration = max(300, min(3600, payload.access_duration_seconds))
    try:
        patient = await PatientDiscoveryService(db, get_async_redis_client()).consume_handle(
            raw_handle=payload.discovery_handle,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
        )
    except DiscoveryHandleInvalid as exc:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "DISCOVERY_HANDLE_INVALID_OR_EXPIRED"},
        ) from exc
    except DiscoveryUnavailable as exc:
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
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
    expires_at = now + timedelta(seconds=120)
    context = {
        "request_id": request_id,
        "patient_id": patient_id,
        "provider_id": provider.actor_uid,
        "hospital_id": str(provider.hospital_id),
        "challenge_nonce": challenge_nonce,
        "purpose": payload.purpose,
        "scope": payload.scope,
        "access_duration": access_duration,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    context_hash = consent_context_hash_v3(**context)

    token_result = await db.execute(
        select(PatientPushToken)
        .where(
            PatientPushToken.patient_id == patient_uuid,
            PatientPushToken.is_active.is_(True),
        )
        .order_by(PatientPushToken.updated_at.desc())
        .limit(1)
    )
    push_token = token_result.scalar_one_or_none()
    if push_token is not None and not isinstance(getattr(push_token, "expo_push_token", None), str):
        logger.error("invalid_active_push_token_record")
        push_token = None
    delivery_status = "queued" if push_token else "unavailable"

    request_data = {
        **context,
        "created_at": context.pop("issued_at"),
        "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION,
        "consent_context_hash": context_hash,
        "provider_name": provider.provider.display_name,
        "hospital_name": provider.hospital.display_name,
        "status": "pending_audit",
        "delivery_status": delivery_status,
        "delivery_error": None if push_token else "No active push token",
        "clinical_initiated_at": initiation.initiated_at.isoformat(),
        "clinical_authentication_method": initiation.authentication_method.value,
        "clinical_mfa_verified_at": initiation.mfa_verified_at.isoformat(),
        "clinical_assurance_policy_version": initiation.assurance_policy_version,
    }
    # context.pop above intentionally moved issued_at to the repository's
    # existing created_at field; restore all other canonical fields unchanged.
    request_data["issued_at"] = request_data["created_at"]

    redis = get_async_redis_client()
    try:
        await _redis_call(
            redis.set,
            f"consent_request:{request_id}",
            json.dumps(request_data),
            ex=120,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "CONSENT_SECURITY_STORE_UNAVAILABLE", "retryable": True},
        ) from exc

    try:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=provider.actor_uid,
            event_type="CONSENT_REQUEST_CREATED",
            target_id=request_id,
            status="SUCCESS",
            metadata={
                "purpose": payload.purpose,
                "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION,
            },
        )
    except Exception as exc:
        try:
            await _redis_call(redis.delete, f"consent_request:{request_id}")
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail={"error_code": "CONSENT_SECURITY_AUDIT_UNAVAILABLE", "retryable": True},
        ) from exc

    try:
        promoted = await _promote_request(redis, request_id)
    except Exception:
        promoted = False
    if not promoted:
        try:
            await _redis_call(redis.delete, f"consent_request:{request_id}")
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail={"error_code": "CONSENT_SECURITY_STORE_UNAVAILABLE", "retryable": True},
        )

    if push_token:
        background_tasks.add_task(
            _deliver_notification,
            request_id=request_id,
            patient_id=patient_id,
            provider_name=provider.provider.display_name,
            purpose=payload.purpose,
            expo_push_token=push_token.expo_push_token,
        )

    return ConsentV3RequestResponse(
        protocol_version=SIGNED_CONSENT_V3_PROTOCOL_VERSION,
        request_id=request_id,
        status="pending",
        expires_in_seconds=120,
        challenge_nonce=challenge_nonce,
        notification_dispatch=delivery_status,
        notification_queued=push_token is not None,
        delivery_status=delivery_status,
    )


@router.get(
    "/challenge/{request_id}",
    status_code=status.HTTP_200_OK,
    response_model=ConsentV3ChallengePayload,
)
async def get_consent_v3_challenge(
    request_id: str,
    session: AuthenticatedPatientSession = Depends(get_current_patient_session),
):
    redis = get_async_redis_client()
    try:
        raw = await _redis_call(redis.get, f"consent_request:{request_id}")
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail={"error_code": "CONSENT_SECURITY_STORE_UNAVAILABLE"}
        ) from exc
    if not raw:
        raise HTTPException(status_code=404, detail={"error_code": "CONSENT_CHALLENGE_NOT_FOUND"})
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if data.get("protocol_version") != SIGNED_CONSENT_V3_PROTOCOL_VERSION:
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_PROTOCOL_MISMATCH"})
    if data.get("status") != "pending":
        raise HTTPException(status_code=404, detail={"error_code": "CONSENT_CHALLENGE_NOT_FOUND"})
    if str(data.get("patient_id")) != session.patient_id:
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_PATIENT_MISMATCH"})
    context_hash = _validated_context_hash(data)
    return ConsentV3ChallengePayload(
        protocol_version=SIGNED_CONSENT_V3_PROTOCOL_VERSION,
        request_id=str(data["request_id"]),
        patient_id=session.patient_id,
        provider_id=str(data["provider_id"]),
        hospital_id=str(data["hospital_id"]),
        provider_name=str(data["provider_name"]),
        hospital_name=str(data["hospital_name"]),
        purpose=str(data["purpose"]),
        scope=str(data["scope"]),
        access_duration=int(data["access_duration"]),
        challenge_nonce=str(data["challenge_nonce"]),
        issued_at=str(data["created_at"]),
        expires_at=str(data["expires_at"]),
        consent_context_hash=context_hash,
        status="pending",
    )


@router.post(
    "/approve-signed",
    status_code=status.HTTP_200_OK,
    response_model=SignedConsentV3ResponsePayload,
)
async def approve_signed_consent_v3(
    payload: SignedConsentV3RequestPayload,
    session: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
):
    if payload.patient_id != session.patient_id:
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_PATIENT_MISMATCH"})

    redis = get_async_redis_client()
    try:
        raw = await _redis_call(redis.get, f"consent_request:{payload.request_id}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error_code": "CONSENT_SECURITY_STORE_UNAVAILABLE"}) from exc
    if not raw:
        raise HTTPException(status_code=404, detail={"error_code": "CONSENT_CHALLENGE_NOT_FOUND"})
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if data.get("protocol_version") != SIGNED_CONSENT_V3_PROTOCOL_VERSION:
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_PROTOCOL_MISMATCH"})
    if str(data.get("patient_id")) != session.patient_id:
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_PATIENT_MISMATCH"})

    trusted_hospital = str(data.get("hospital_id", ""))
    if trusted_hospital:
        bind_trusted_audit_hospital(trusted_hospital)

    context_hash = _validated_context_hash(data)
    if not secrets.compare_digest(payload.consent_context_hash, context_hash):
        raise HTTPException(status_code=401, detail={"error_code": "CONSENT_CONTEXT_MISMATCH"})
    if payload.challenge_nonce != data.get("challenge_nonce"):
        raise HTTPException(status_code=401, detail={"error_code": "CONSENT_CHALLENGE_NONCE_MISMATCH"})

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "protocol_version": payload.protocol_version,
                "request_id": payload.request_id,
                "patient_id": payload.patient_id,
                "decision": payload.decision,
                "challenge_nonce": payload.challenge_nonce,
                "consent_context_hash": payload.consent_context_hash,
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
        return SignedConsentV3ResponsePayload(
            protocol_version=SIGNED_CONSENT_V3_PROTOCOL_VERSION,
            request_id=payload.request_id,
            status=payload.decision,
            responded_at=str(data.get("responded_at")),
        )

    if data.get("status") != "pending" or await _redis_call(
        redis.get, f"signed_consent_v3_nonce:{payload.challenge_nonce}:used"
    ):
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_REPLAY_REJECTED"})

    try:
        await assert_live_consent_v3_provider(db=db, request_data=data)
    except (ConsentV3AuthorityUnavailable, ConsentV3ProviderIneligible) as exc:
        raise _authority_http(exc) from exc

    result = await SignedConsentV3Verifier().verify(
        db=db,
        patient_id=session.patient_id,
        request_id=payload.request_id,
        provider_id=str(data.get("provider_id", "")),
        hospital_id=str(data.get("hospital_id", "")),
        challenge_nonce=payload.challenge_nonce,
        decision=payload.decision,
        signature_b64=payload.signature,
        purpose=str(data.get("purpose", "")),
        scope=str(data.get("scope", "")),
        access_duration=int(data.get("access_duration", 0)),
        issued_at=str(data.get("created_at", "")),
        expires_at=str(data.get("expires_at", "")),
        consent_context_hash=context_hash,
        device_id=payload.device_id,
        key_id=payload.key_id,
        key_version=payload.key_version,
        public_key_fingerprint=payload.public_key_fingerprint,
    )
    if not result.verified:
        if result.error == "Challenge expired":
            raise HTTPException(status_code=403, detail={"error_code": "CONSENT_CHALLENGE_EXPIRED"})
        raise HTTPException(status_code=401, detail={"error_code": "CONSENT_SIGNATURE_INVALID"})

    now = datetime.now(timezone.utc)
    data["status"] = payload.decision
    data["responded_at"] = now.isoformat()
    data["approved_device_id"] = result.device_id
    data["approved_device_key_id"] = result.key_id
    data["approved_device_key_version"] = result.key_version
    data["approved_device_key_fingerprint"] = result.public_key_fingerprint
    data["approval_fingerprint"] = fingerprint
    if payload.decision == "approved":
        data["access_expires_at"] = (
            now + timedelta(seconds=int(data.get("access_duration", 0)))
        ).isoformat()
        evidence = {
            "status": "approved",
            "patient_id": session.patient_id,
            "approved_at": now.isoformat(),
            "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION,
            "device_id": result.device_id,
            "key_version": result.key_version,
        }
        await _redis_call(
            redis.set,
            f"assurance_evidence:{payload.request_id}",
            json.dumps(evidence),
            ex=120,
        )

    ttl_seconds = int(data.get("access_duration", 0)) if payload.decision == "approved" else 300
    if not await _resolve_request(
        redis,
        request_id=payload.request_id,
        nonce=payload.challenge_nonce,
        data=data,
        ttl_seconds=ttl_seconds,
    ):
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_REPLAY_REJECTED"})

    try:
        await push_request_limiter.release(patient_id=session.patient_id)
    except Exception as exc:
        logger.error("Resolved V3 consent push lock cleanup failed", extra={"error_type": type(exc).__name__})

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.CONSENT),
        actor_uid=session.patient_id,
        event_type="CONSENT_APPROVED_SIGNED" if payload.decision == "approved" else "CONSENT_DENIED_SIGNED",
        target_id=payload.request_id,
        status="SUCCESS",
        metadata={
            "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION,
            "device_id": result.device_id,
            "key_id": result.key_id,
            "key_version": result.key_version,
        },
    )

    return SignedConsentV3ResponsePayload(
        protocol_version=SIGNED_CONSENT_V3_PROTOCOL_VERSION,
        request_id=payload.request_id,
        status=payload.decision,
        responded_at=now.isoformat(),
    )


@router.post(
    "/{request_id}/claim-access",
    status_code=status.HTTP_200_OK,
    response_model=ConsentV3AccessClaimResponse,
)
async def claim_consent_v3_access(
    request_id: str,
    response: Response,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.CONSENT_REQUEST)
    ),
    db: AsyncSession = Depends(get_db_session),
):
    response.headers["Cache-Control"] = "no-store"
    redis = get_async_redis_client()
    try:
        raw = await _redis_call(redis.get, f"consent_request:{request_id}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error_code": "CONSENT_SECURITY_STORE_UNAVAILABLE"}) from exc
    if not raw:
        raise HTTPException(status_code=404, detail={"error_code": "CONSENT_REQUEST_NOT_FOUND"})
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if data.get("protocol_version") != SIGNED_CONSENT_V3_PROTOCOL_VERSION:
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_PROTOCOL_MISMATCH"})
    if data.get("status") != "approved":
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_NOT_APPROVED"})
    if str(data.get("provider_id")) != provider.actor_uid or str(data.get("hospital_id")) != str(provider.hospital_id):
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_PROVIDER_CONTEXT_MISMATCH"})
    _validated_context_hash(data)

    try:
        await assert_live_consent_v3_provider(db=db, request_data=data)
    except (ConsentV3AuthorityUnavailable, ConsentV3ProviderIneligible) as exc:
        raise _authority_http(exc) from exc

    try:
        access_expires_at = datetime.fromisoformat(str(data["access_expires_at"]).replace("Z", "+00:00"))
        if access_expires_at.tzinfo is None or access_expires_at.utcoffset() is None:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_ACCESS_WINDOW_INVALID"}) from exc
    if datetime.now(timezone.utc) >= access_expires_at:
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_ACCESS_EXPIRED"})

    try:
        patient_uuid = uuid.UUID(str(data["patient_id"]))
        device_uuid = uuid.UUID(str(data["approved_device_id"]))
        key_uuid = uuid.UUID(str(data["approved_device_key_id"]))
        key_version = int(data["approved_device_key_version"])
        key_fingerprint = str(data["approved_device_key_fingerprint"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_DEVICE_BINDING_INVALID"}) from exc

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
        raise HTTPException(status_code=403, detail={"error_code": "CONSENT_APPROVING_KEY_NO_LONGER_ACTIVE"})

    grant_row = None
    try:
        token, capability = await issue_from_approved_request(request_data=data)
        now = datetime.now(timezone.utc)
        prior_rows = (
            (
                await db.execute(
                    select(ConsentGrantLog)
                    .where(
                        ConsentGrantLog.request_id == request_id,
                        ConsentGrantLog.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for prior in prior_rows:
            prior.revoked_at = now
            prior.revoked_reason = "capability_rotated"
        grant_row = ConsentGrantLog(
            token_hash=_token_hash(token),
            patient_id=capability.patient_id,
            clinician_id=provider.actor_uid,
            hospital_id=provider.hospital_id,
            purpose=capability.purpose,
            scope=capability.scope,
            is_break_glass=False,
            reason_code=None,
            issued_at=now,
            expires_at=access_expires_at,
            assurance_level="signed_device_v3",
            assurance_verified_at=now,
            request_id=request_id,
            signed_approval_id=str(data.get("approval_fingerprint")),
        )
        db.add(grant_row)
        await db.commit()
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=provider.actor_uid,
            event_type="CONSENT_ACCESS_CLAIMED",
            target_id=request_id,
            status="SUCCESS",
            metadata={
                "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION,
                "patient_id": capability.patient_id,
                "provider_id": provider.actor_uid,
                "hospital_id": str(provider.hospital_id),
                "purpose": capability.purpose,
                "scope": capability.scope,
                "device_id": str(key_row.device_id),
                "key_version": key_row.key_version,
            },
        )
    except ApprovedAccessClaimInProgress as exc:
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_ACCESS_CLAIM_IN_PROGRESS"}) from exc
    except ApprovedAccessStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail={"error_code": "CONSENT_ACCESS_STORE_UNAVAILABLE"}) from exc
    except Exception:
        try:
            await invalidate_request(request_id)
        finally:
            if grant_row is not None:
                grant_row.revoked_at = datetime.now(timezone.utc)
                grant_row.revoked_reason = "claim_finalization_failed"
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
            raise

    return ConsentV3AccessClaimResponse(
        protocol_version=SIGNED_CONSENT_V3_PROTOCOL_VERSION,
        patient_id=capability.patient_id,
        consent_token=token,
        purpose=capability.purpose,
        scope=capability.scope[0],
        expires_at=capability.expires_at,
    )
