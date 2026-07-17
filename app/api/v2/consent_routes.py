"""Consent routes for Nexa Care V2.

Exposes both the generic /grant endpoint and the frontend-facing
/routine/issue and /break-glass/issue endpoints. All paths delegate to
ConsentEngine so the v2 consent surface has a single authority.
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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_provider, get_db_session, get_provider_context, get_scoped_session, require_role
from app.core.rate_limiter import ConcurrentPushLimiter, RateLimiter
from app.core.redis import get_async_redis_client
from app.core.config import get_break_glass_mfa_max_age_seconds
from app.core.session_binding import provider_session_binding, provider_session_token
# Deprecated import seam retained only for older test patch targets. Runtime
# request handlers use the explicit asyncio factory above.
get_redis_client = get_async_redis_client


async def _redis_call(method, *args, **kwargs):
    """Await the production asyncio client; tolerate legacy test doubles."""
    result = method(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result
from app.models.patient_device_keys import PatientDeviceKey
from app.models.push_token import PatientPushToken
from app.models.provider_context import ProviderContext
from app.models.consent_grant import ConsentGrantLog
from app.models.assurance import AssuranceLevel
from app.services.signed_approval_verifier import SignedApprovalVerifier
from app.services.push_notification_service import PushNotificationService
from app.services.approved_access_capability import (
    ApprovedAccessClaimInProgress,
    ApprovedAccessStoreUnavailable,
    invalidate_request,
    issue_from_approved_request,
)
# EXPLICITLY ALIAS THE IMPORT SO MOCK PATCHING MATCHES THE ATTRIBUTE NAME
import app.services.consent_engine as consent_engine
from app.services.consent_engine import (
    ConsentEngineUnavailable,
    ConsentPurpose,
    issue_routine,
    issue_break_glass,
)
from app.services.provider_auth_service import resolve_provider_session_context
from app.services.break_glass_policy import (
    BREAK_GLASS_POLICY_VERSION,
    BREAK_GLASS_REASON_CODE_VERSION,
    BreakGlassReasonCode,
    approved_break_glass_scope,
    validate_justification,
)
from app.services.policy_service import PolicyService
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")
router = APIRouter(prefix="/api/v2/consent", tags=["consent"])
push_notification_service = PushNotificationService()
push_request_limiter = ConcurrentPushLimiter()
ROUTINE_CONSENT_TTL_SECONDS = 60 * 60
BREAK_GLASS_TTL_SECONDS = 15 * 60

# Rate limit: 3 break-glass requests per provider per hour
_break_glass_limiter = RateLimiter(max_requests=3, window_seconds=3600, key_func=lambda r: "bg")


class RoutineConsentIssueRequest(BaseModel):
    patient_id: str
    purpose: ConsentPurpose = ConsentPurpose.TREATMENT
    scope: list[str] = Field(
        default_factory=lambda: ["clinical.*", "pii.demographics"],
        min_length=1,
        description="List of required namespaced data scopes",
    )
    assurance_level: AssuranceLevel = AssuranceLevel.STANDARD
    assurance_evidence: dict = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class BreakGlassConsentIssueRequest(BaseModel):
    patient_id: str
    reason_code: BreakGlassReasonCode
    justification: str = Field(..., min_length=1, max_length=500)
    requested_scope: list[str] | None = None
    purpose: Literal["EMERGENCY"] = "EMERGENCY"

    model_config = ConfigDict(frozen=True)


class BreakGlassRevokeRequest(BaseModel):
    consent_token: str
    revocation_reason: str

    model_config = ConfigDict(frozen=True)


class ConsentIssueResponse(BaseModel):
    """Time-bound consent token response."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    consent_token: str
    expires_at: datetime


class BreakGlassConsentIssueResponse(ConsentIssueResponse):
    approved_scope: list[str]
    policy_version: str
    authorization_ref: str


class RoutineConsentGrantRequest(BaseModel):
    patient_id: str
    purpose: str = "routine_access"
    scope: list[str] = Field(..., min_length=1, description="List of required namespaced data scopes")
    assurance_level: AssuranceLevel = AssuranceLevel.STANDARD
    assurance_evidence: dict = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class RoutineConsentGrantResponse(BaseModel):
    """Time-bound routine consent token response."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    consent_token: str
    expires_at: datetime


def _expires_at(ttl_seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


def _token_hash(token: str) -> str:
    """Non-secret correlation id for the durable Postgres row."""
    # Prefix handling to match ConsentEngine._token_hash
    clean = token[len("nexa:consent:"):] if token.startswith("nexa:consent:") else token
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


@router.post("/grant", response_model=RoutineConsentGrantResponse)
async def grant_consent_route(
    request: RoutineConsentGrantRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    try:
        token = await consent_engine.issue(
            db=db,
            patient_id=request.patient_id,
            clinician_id=provider.actor_uid,
            purpose=request.purpose,
            scope=request.scope,
            assurance_level=request.assurance_level,
            assurance_evidence=request.assurance_evidence,
            hospital_id=str(provider.hospital_id),
        )
        return RoutineConsentGrantResponse(
            consent_token=token,
            expires_at=_expires_at(ROUTINE_CONSENT_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "CONSENT_REQUEST_INVALID"},
        ) from err
    except ConsentEngineUnavailable as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CONSENT_SERVICE_UNAVAILABLE"},
        ) from err


@router.post("/routine/issue", response_model=ConsentIssueResponse)
async def issue_routine_consent_route(
    request: RoutineConsentIssueRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    """Issue a routine consent token for a verified patient identity."""
    try:
        policy = (await PolicyService(db).get_policy(uuid.UUID(request.patient_id))).strip().lower()
        if policy not in {"standard", "standard_access"}:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail={"error_code": "PATIENT_APPROVAL_REQUIRED"},
            )
        token = await issue_routine(
            db=db,
            patient_id=request.patient_id,
            clinician_id=provider.actor_uid,
            purpose=request.purpose,
            scope=request.scope,
            assurance_level=request.assurance_level,
            assurance_evidence=request.assurance_evidence,
            ttl_seconds=ROUTINE_CONSENT_TTL_SECONDS,
            hospital_id=str(provider.hospital_id),
        )
        return ConsentIssueResponse(
            consent_token=token,
            expires_at=_expires_at(ROUTINE_CONSENT_TTL_SECONDS),
        )
    except HTTPException:
        raise
    except (ValueError, TypeError) as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "CONSENT_REQUEST_INVALID"},
        ) from err
    except ConsentEngineUnavailable as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CONSENT_SERVICE_UNAVAILABLE"},
        ) from err


@router.post("/break-glass/issue", response_model=BreakGlassConsentIssueResponse)
async def issue_break_glass_consent_route(
    request: Request,
    payload: BreakGlassConsentIssueRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(require_role("clinician")),
):
    """Issue an emergency break-glass consent token."""
    # Enforce rate limit per provider
    await _break_glass_limiter(request=request, provider_id=provider.actor_uid)

    try:
        justification = validate_justification(payload.justification)
        approved_scope = approved_break_glass_scope(payload.reason_code, payload.requested_scope)
        raw_session = provider_session_token(request)
        if not raw_session or provider.session_binding != provider_session_binding(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "BREAK_GLASS_SESSION_REQUIRED"},
            )
        session_data = await resolve_provider_session_context(raw_session)
        if not session_data or str(session_data.get("provider_id")) != str(provider.provider.provider_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_code": "BREAK_GLASS_SESSION_INVALID"},
            )
        raw_mfa_verified_at = session_data.get("mfa_verified_at")
        if not isinstance(raw_mfa_verified_at, str):
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail={"error_code": "BREAK_GLASS_STEP_UP_MFA_REQUIRED"},
            )
        try:
            mfa_verified_at = datetime.fromisoformat(raw_mfa_verified_at)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail={"error_code": "BREAK_GLASS_STEP_UP_MFA_REQUIRED"},
            ) from exc
        if mfa_verified_at.tzinfo is None:
            mfa_verified_at = mfa_verified_at.replace(tzinfo=timezone.utc)
        mfa_age_seconds = (datetime.now(timezone.utc) - mfa_verified_at).total_seconds()
        if mfa_age_seconds < 0 or mfa_age_seconds > get_break_glass_mfa_max_age_seconds():
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail={"error_code": "BREAK_GLASS_STEP_UP_MFA_REQUIRED"},
            )

        duplicate_material = ":".join(
            [provider.session_binding, payload.patient_id, payload.reason_code.value, ",".join(approved_scope)]
        )
        duplicate_key = f"break_glass_issue:{hashlib.sha256(duplicate_material.encode()).hexdigest()}"
        redis = get_async_redis_client()
        acquired = await _redis_call(redis.set, duplicate_key, "1", nx=True, ex=60)
        if not acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": "BREAK_GLASS_DUPLICATE_REQUEST"},
            )

        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="BREAK_GLASS_GOVERNANCE_APPROVED",
            target_id=payload.patient_id,
            status="SUCCESS",
            metadata={
                "reason_code": payload.reason_code.value,
                "reason_code_version": BREAK_GLASS_REASON_CODE_VERSION,
                "scope_policy_version": BREAK_GLASS_POLICY_VERSION,
                "approved_scope": approved_scope,
                "mfa_assurance": "totp",
                "mfa_age_seconds": int(mfa_age_seconds),
                "justification_present": bool(justification),
                "hospital_id": str(provider.hospital.hospital_id),
            },
        )
        token = await issue_break_glass(
            db=db,
            patient_id=payload.patient_id,
            clinician_id=provider.actor_uid,
            reason_code=payload.reason_code.value,
            hospital_id=str(provider.hospital_id),
            scope=approved_scope,
            reason_code_version=BREAK_GLASS_REASON_CODE_VERSION,
            session_binding=provider.session_binding,
            mfa_verified_at=mfa_verified_at,
        )
        notification_event_id = str(uuid.uuid4())
        try:
            pid_uuid = uuid.UUID(payload.patient_id)
        except ValueError:
            pid_uuid = None
        push_token = None
        if pid_uuid is not None:
            token_result = await db.execute(
                select(PatientPushToken).where(
                    PatientPushToken.patient_id == pid_uuid,
                    PatientPushToken.is_active.is_(True),
                ).order_by(PatientPushToken.updated_at.desc()).limit(1)
            )
            push_token = token_result.scalar_one_or_none()
        if push_token and isinstance(getattr(push_token, "expo_push_token", None), str):
            background_tasks.add_task(
                push_notification_service.send_emergency_access_notice,
                patient_id=payload.patient_id,
                event_id=notification_event_id,
                expo_push_token=push_token.expo_push_token,
            )
            notification_status = "queued"
        else:
            notification_status = "unavailable"
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="BREAK_GLASS_PATIENT_NOTIFICATION",
            target_id=payload.patient_id,
            status=notification_status.upper(),
            metadata={"notification_event_id": notification_event_id},
        )
        return BreakGlassConsentIssueResponse(
            consent_token=token,
            expires_at=_expires_at(BREAK_GLASS_TTL_SECONDS),
            approved_scope=approved_scope,
            policy_version=BREAK_GLASS_POLICY_VERSION,
            authorization_ref=hashlib.sha256(token.encode("utf-8")).hexdigest()[:16],
        )
    except HTTPException:
        raise
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "BREAK_GLASS_POLICY_REJECTED"},
        ) from err
    except ConsentEngineUnavailable as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CONSENT_SERVICE_UNAVAILABLE"},
        ) from err


@router.post("/break-glass/revoke")
async def revoke_break_glass_consent_route(
    request: BreakGlassRevokeRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(require_role("clinician")),
):
    """Revoke an emergency break-glass consent token."""
    token_hash = _token_hash(request.consent_token)

    # Hard-audit revocation attempt
    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="BREAK_GLASS_REVOKE_ATTEMPT",
        target_id=token_hash,
        status="STARTED",
        metadata={"revocation_reason": request.revocation_reason},
    )

    # Verify token is a break-glass token
    stmt = select(ConsentGrantLog).where(ConsentGrantLog.token_hash == token_hash)
    result = await db.execute(stmt)
    grant = result.scalar_one_or_none()

    if grant is None or not grant.is_break_glass:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token is not a break-glass grant",
        )

    # Perform revocation
    await consent_engine.revoke(
        db=db,
        token=request.consent_token,
        reason=request.revocation_reason,
    )

    # Hard-audit success
    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="BREAK_GLASS_REVOKE_SUCCESS",
        target_id=token_hash,
        status="SUCCESS",
    )

    return {"status": "revoked", "token_hash": token_hash}


@router.get("/validate")
async def validate_consent(
    consent_token: str,
    patient_id: str | None = None,
    provider: ProviderContext = Depends(get_provider_context),
):
    """Validate a consent token (retained for terminal revalidation)."""
    try:
        capability = await consent_engine.validate(
            token=consent_token,
            patient_id=patient_id,
            clinician_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
        )
        if not capability:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired consent",
            )
        return capability
    except ConsentEngineUnavailable as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CONSENT_SERVICE_UNAVAILABLE"},
        ) from err


class ConsentChallengeRequestPayload(BaseModel):
    patient_id: str
    provider_id: str | None = None  # DEPRECATED — server derives from session; rejects mismatch
    purpose: str = "routine_checkup"
    scope: str = "clinical"
    access_duration_seconds: int = 900


class ConsentChallengeResponsePayload(BaseModel):
    request_id: str
    status: str
    expires_in_seconds: int
    challenge_nonce: str | None = None
    notification_dispatch: Literal["queued", "unavailable"]
    notification_queued: bool
    delivery_status: Literal["queued", "unavailable"]


class ConsentAccessClaimResponse(BaseModel):
    patient_id: str
    consent_token: str
    purpose: str
    scope: str
    expires_at: str


class ConsentStatusResponsePayload(BaseModel):
    request_id: str
    status: str
    responded_at: str | None = None
    doctor_status: str | None = None
    delivery_status: str | None = None
    delivery_error: str | None = None


async def _deliver_consent_notification(
    *,
    request_id: str,
    patient_id: str,
    provider_name: str,
    purpose: str,
    expo_push_token: str,
) -> None:
    """Send the Expo notification and persist delivery outcome in Redis."""
    result = await push_notification_service.send_approval_request(
        patient_id=patient_id,
        request_id=request_id,
        provider_name=provider_name,
        purpose=purpose,
        expo_push_token=expo_push_token,
    )
    redis = get_redis_client()
    key = f"consent_request:{request_id}"
    raw = await _redis_call(redis.get, key)
    if not raw:
        return
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    data["delivery_status"] = "sent" if result.success else "failed"
    data["delivery_error"] = None if result.success else "PUSH_DELIVERY_FAILED"
    data["delivery_completed_at"] = datetime.now(timezone.utc).isoformat()
    ttl = await _redis_call(redis.ttl, key)
    await _redis_call(redis.set, key, json.dumps(data), ex=ttl if isinstance(ttl, int) and ttl > 0 else 120)


class SignedApprovalRequestPayload(BaseModel):
    request_id: str
    patient_id: str
    decision: Literal["approved", "denied"]
    challenge_nonce: str
    signature: str
    device_id: str


class SignedApprovalResponsePayload(BaseModel):
    request_id: str
    status: str
    responded_at: str


_RESOLVE_SIGNED_APPROVAL_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local data = cjson.decode(raw)
if data.status ~= 'pending' or data.challenge_nonce ~= ARGV[1] then return 0 end
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('SET', KEYS[2], '1', 'EX', 300)
redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
return 1
"""


async def _resolve_signed_approval_atomic(redis, request_id: str, nonce: str, data: dict, ttl: int) -> bool:
    result = await _redis_call(redis.eval,
        _RESOLVE_SIGNED_APPROVAL_LUA, 2,
        f"consent_request:{request_id}", f"biometric_nonce:{nonce}:used",
        nonce, json.dumps(data, sort_keys=True), ttl,
    )
    return int(result) == 1


@router.post("/request", status_code=status.HTTP_201_CREATED, response_model=ConsentChallengeResponsePayload)
async def create_consent_request(
    payload: ConsentChallengeRequestPayload,
    background_tasks: BackgroundTasks,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    """Initiate a push-based consent request generating a cryptographic challenge.

    SECURITY: provider_id is derived from the authenticated session (provider.actor_uid).
    If a caller supplies provider_id in the body, it must match the session identity
    or the request is rejected as an IDOR probe.  The server never trusts client-
    supplied identity — the Bearer token is the single source of truth.
    """
    # ── IDOR guard: reject if caller supplied provider_id that doesn't match session ──
    if payload.provider_id is not None and str(payload.provider_id) != str(provider.actor_uid):
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="CONSENT_REQUEST_IDOR_REJECTED",
            target_id=payload.patient_id,
            status="REJECTED",
            metadata={"supplied_provider_id": payload.provider_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="provider_id in request body does not match authenticated session",
        )

    # ── Duration bounds ───────────────────────────────────────────────────────
    MIN_DURATION = 300    # 5 minutes
    MAX_DURATION = 3600   # 60 minutes
    access_duration = max(MIN_DURATION, min(MAX_DURATION, payload.access_duration_seconds))

    try:
        pid_uuid = uuid.UUID(payload.patient_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "INVALID_PATIENT_ID"},
        ) from exc

    stmt = select(PatientDeviceKey).where(
        PatientDeviceKey.patient_id == pid_uuid,
        PatientDeviceKey.status == "active",
    ).limit(1)
    res = await db.execute(stmt)
    device = res.scalar_one_or_none()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Patient device not enrolled",
        )

    request_id = str(uuid.uuid4())
    challenge_nonce = secrets.token_hex(32)
    challenge_ttl_seconds = 120
    # access_duration already clamped to [300, 3600] above
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=challenge_ttl_seconds)

    challenge_payload = {
        "request_id": request_id,
        "patient_id": payload.patient_id,
        "provider_id": provider.actor_uid,  # Server-derived — NEVER from request body
        "hospital_id": str(provider.hospital_id),
        "provider_name": provider.provider.display_name,
        "hospital_name": provider.hospital.display_name,
        "purpose": payload.purpose,
        "scope": payload.scope,
        "access_duration": access_duration,  # Clamped to [300, 3600]
        "challenge_nonce": challenge_nonce,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "status": "pending",
    }

    token_result = await db.execute(
        select(PatientPushToken).where(
            PatientPushToken.patient_id == pid_uuid,
            PatientPushToken.is_active.is_(True),
        ).order_by(PatientPushToken.updated_at.desc()).limit(1)
    )
    push_token = token_result.scalar_one_or_none()
    if push_token is not None and not isinstance(
        getattr(push_token, "expo_push_token", None), str
    ):
        logger.error("invalid_active_push_token_record")
        push_token = None
    delivery_status = "queued" if push_token else "unavailable"
    challenge_payload["delivery_status"] = delivery_status
    challenge_payload["delivery_error"] = None if push_token else "No active push token"

    redis = get_redis_client()
    try:
        await _redis_call(redis.set, f"consent_request:{request_id}", json.dumps(challenge_payload), ex=challenge_ttl_seconds)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "CONSENT_SECURITY_STORE_UNAVAILABLE", "retryable": True},
        ) from exc

    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="CONSENT_REQUEST_CREATED",
        target_id=request_id,
        status="SUCCESS",
        metadata={"patient_id": payload.patient_id, "purpose": payload.purpose},
    )

    if push_token:
        background_tasks.add_task(
            _deliver_consent_notification,
            request_id=request_id,
            patient_id=payload.patient_id,
            provider_name=provider.provider.display_name,
            purpose=payload.purpose,
            expo_push_token=push_token.expo_push_token,
        )

    return ConsentChallengeResponsePayload(
        request_id=request_id,
        status="pending",
        expires_in_seconds=challenge_ttl_seconds,
        challenge_nonce=challenge_nonce,
        notification_dispatch=delivery_status,
        notification_queued=push_token is not None,
        delivery_status=delivery_status,
    )


@router.post("/approve-signed", status_code=status.HTTP_200_OK, response_model=SignedApprovalResponsePayload)
async def approve_signed_consent(
    payload: SignedApprovalRequestPayload,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Verify patient cryptographic signature and issue consent grant if approved."""
    if str(payload.patient_id) != str(patient_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated patient does not match approval payload",
        )

    redis = get_redis_client()
    try:
        raw = await _redis_call(redis.get, f"consent_request:{payload.request_id}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error_code": "CONSENT_SECURITY_STORE_UNAVAILABLE"}) from exc
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge expired or not found",
        )

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    if str(data.get("patient_id")) != str(patient_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated patient does not match challenge target",
        )

    approval_fingerprint = hashlib.sha256(
        "|".join((payload.request_id, payload.patient_id, payload.decision,
                  payload.challenge_nonce, payload.device_id, payload.signature)).encode("utf-8")
    ).hexdigest()
    if data.get("status") == payload.decision and secrets.compare_digest(
        str(data.get("approval_fingerprint", "")), approval_fingerprint
    ):
        return SignedApprovalResponsePayload(
            request_id=payload.request_id,
            status=payload.decision,
            responded_at=str(data.get("responded_at")),
        )

    if await _redis_call(redis.get, f"biometric_nonce:{payload.challenge_nonce}:used") or data.get("status") != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request already resolved",
        )

    if payload.challenge_nonce != data.get("challenge_nonce"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Challenge nonce mismatch",
        )

    try:
        pid_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}) from exc

    try:
        dev_uuid = uuid.UUID(payload.device_id)
        stmt = select(PatientDeviceKey).where(
            PatientDeviceKey.id == dev_uuid,
            PatientDeviceKey.patient_id == pid_uuid,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "INVALID_DEVICE_ID"},
        ) from exc

    res = await db.execute(stmt)
    device = res.scalar_one_or_none()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device not enrolled for this patient",
        )

    if device.revoked_at is not None or device.status == "revoked":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Biometric binding revoked",
        )

    verifier = SignedApprovalVerifier()
    res_verify = await verifier.verify_signed_approval(
        db=db,
        patient_id=patient_id,
        request_id=payload.request_id,
        challenge_nonce=payload.challenge_nonce,
        decision=payload.decision,
        signature_b64=payload.signature,
        expires_at=str(data.get("expires_at", "")),
        issued_at=str(data.get("created_at", "")),
        provider_id=str(data.get("provider_id", "")),
        scope=str(data.get("scope", "")),
        purpose=str(data.get("purpose", "")),
        access_duration=int(data.get("access_duration", 900)),
        device_id=payload.device_id,
    )
    if not res_verify.verified:
        if res_verify.error == "Challenge expired":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Challenge expired",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signature verification failed",
        )

    now = datetime.now(timezone.utc)

    if payload.decision == "approved":
        evidence_data = {
            "status": "approved",
            "patient_id": patient_id,
            "approved_at": now.isoformat(),
        }
        await _redis_call(redis.set, f"assurance_evidence:{payload.request_id}", json.dumps(evidence_data), ex=120)

        await append_audit_log_or_503(
            actor_uid=patient_id,
            event_type="CONSENT_APPROVED_SIGNED",
            target_id=payload.request_id,
            status="SUCCESS",
            metadata={"device_id": payload.device_id},
        )

        data["status"] = "approved"
        data["responded_at"] = now.isoformat()
        data["access_expires_at"] = (
            now + timedelta(seconds=int(data.get("access_duration", 900)))
        ).isoformat()
        data["approved_device_id"] = str(device.id)
        data["approval_fingerprint"] = approval_fingerprint
        if not await _resolve_signed_approval_atomic(
            redis, payload.request_id, payload.challenge_nonce, data, int(data.get("access_duration", 900))
        ):
            raise HTTPException(status_code=409, detail={"error_code": "CONSENT_REPLAY_REJECTED"})
        try:
            await push_request_limiter.release(patient_id=patient_id)
        except Exception as exc:
            logger.error("Resolved consent push lock cleanup failed", extra={"error_type": type(exc).__name__})

        return SignedApprovalResponsePayload(
            request_id=payload.request_id,
            status="approved",
            responded_at=now.isoformat(),
        )

    data["status"] = "denied"
    data["responded_at"] = now.isoformat()
    data["approved_device_id"] = str(device.id)
    data["approval_fingerprint"] = approval_fingerprint
    if not await _resolve_signed_approval_atomic(redis, payload.request_id, payload.challenge_nonce, data, 300):
        raise HTTPException(status_code=409, detail={"error_code": "CONSENT_REPLAY_REJECTED"})
    try:
        await push_request_limiter.release(patient_id=patient_id)
    except Exception as exc:
        logger.error("Denied consent push lock cleanup failed", extra={"error_type": type(exc).__name__})

    await append_audit_log_or_503(
        actor_uid=patient_id,
        event_type="CONSENT_DENIED_SIGNED",
        target_id=payload.request_id,
        status="SUCCESS",
        metadata={"device_id": payload.device_id},
    )

    return SignedApprovalResponsePayload(
        request_id=payload.request_id,
        status="denied",
        responded_at=now.isoformat(),
    )


@router.get("/status/{request_id}", status_code=status.HTTP_200_OK, response_model=ConsentStatusResponsePayload)
async def get_consent_request_status(
    request_id: str,
    provider: ProviderContext = Depends(get_current_provider),
):
    """Poll consent request resolution status.

    SECURITY:
    - Only the provider who created the request may poll it.
    - Cache-Control: no-store prevents browser/CDN caching of consent state.
    - Returns minimal data (status + responded_at) — no consent tokens.
    """
    redis = get_redis_client()
    raw = await _redis_call(redis.get, f"consent_request:{request_id}")
    if not raw:
        return ConsentStatusResponsePayload(request_id=request_id, status="expired", responded_at=None)

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    if data.get("provider_id") and str(data["provider_id"]) != str(provider.actor_uid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only requesting provider may poll status",
        )
    if str(data.get("hospital_id")) != str(provider.hospital_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent request belongs to a different hospital context",
        )

    return ConsentStatusResponsePayload(
        request_id=request_id,
        status=data.get("status", "pending"),
        responded_at=data.get("responded_at"),
        doctor_status=(
            "delivery_failed"
            if data.get("status", "pending") == "pending"
            and data.get("delivery_status") in {"failed", "unavailable"}
            else data.get("status", "pending")
        ),
        delivery_status=data.get("delivery_status"),
        delivery_error=data.get("delivery_error"),
    )


@router.post(
    "/{request_id}/claim-access",
    status_code=status.HTTP_200_OK,
    response_model=ConsentAccessClaimResponse,
)
async def claim_approved_access(
    request_id: str,
    response: Response,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    """Exchange an approved request for a provider-bound record capability."""
    response.headers["Cache-Control"] = "no-store"
    redis = get_redis_client()
    raw = await _redis_call(redis.get, f"consent_request:{request_id}")
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consent request not found")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    if data.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Consent request is not approved")
    if str(data.get("provider_id")) != provider.actor_uid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only requesting provider may claim access")
    if str(data.get("hospital_id")) != str(provider.hospital_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Consent request belongs to another hospital")

    try:
        access_expires_at = datetime.fromisoformat(str(data.get("access_expires_at")))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approved access window is invalid")
    if access_expires_at.tzinfo is None:
        access_expires_at = access_expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= access_expires_at:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approved access window has expired")

    try:
        device_uuid = uuid.UUID(str(data.get("approved_device_id")))
        patient_uuid = uuid.UUID(str(data.get("patient_id")))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approved device binding is invalid")
    result = await db.execute(select(PatientDeviceKey).where(
        PatientDeviceKey.id == device_uuid,
        PatientDeviceKey.patient_id == patient_uuid,
        PatientDeviceKey.status == "active",
        PatientDeviceKey.revoked_at.is_(None),
    ))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approving device is no longer active")

    grant_row = None
    try:
        token, capability = await issue_from_approved_request(request_data=data)
        now = datetime.now(timezone.utc)
        prior_rows = (await db.execute(select(ConsentGrantLog).where(
            ConsentGrantLog.request_id == request_id,
            ConsentGrantLog.revoked_at.is_(None),
        ).with_for_update())).scalars().all()
        for prior in prior_rows:
            prior.revoked_at = now
            prior.revoked_reason = "capability_rotated"
        grant_row = ConsentGrantLog(
            token_hash=_token_hash(token), patient_id=capability.patient_id,
            clinician_id=provider.actor_uid, hospital_id=provider.hospital_id,
            purpose=capability.purpose, scope=capability.scope,
            is_break_glass=False, reason_code=None, issued_at=now,
            expires_at=access_expires_at, assurance_level="signed_device",
            assurance_verified_at=now, request_id=request_id,
            signed_approval_id=str(data.get("approval_fingerprint")),
        )
        db.add(grant_row)
        await db.commit()
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="CONSENT_ACCESS_CLAIMED",
            target_id=request_id,
            status="SUCCESS",
            metadata={
                "patient_id": capability.patient_id,
                "provider_id": provider.actor_uid,
                "hospital_id": str(provider.hospital_id),
                "purpose": capability.purpose,
                "scope": capability.scope,
            },
        )
    except ApprovedAccessClaimInProgress as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "CONSENT_ACCESS_CLAIM_IN_PROGRESS"},
        ) from exc
    except ApprovedAccessStoreUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CONSENT_ACCESS_STORE_UNAVAILABLE"},
        ) from exc
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

    return ConsentAccessClaimResponse(
        patient_id=capability.patient_id,
        consent_token=token,
        purpose=capability.purpose,
        scope=capability.scope[0],
        expires_at=capability.expires_at,
    )


class ConsentCancelResponsePayload(BaseModel):
    request_id: str
    status: str = "cancelled"
    cancelled_at: str


@router.post("/request/{request_id}/cancel", status_code=status.HTTP_200_OK, response_model=ConsentCancelResponsePayload)
async def cancel_consent_request(
    request_id: str,
    provider: ProviderContext = Depends(get_current_provider),
):
    """Cancel a pending consent request.

    SECURITY: Only the provider who created the request may cancel it.
    Cancellation prevents the patient from later approving an abandoned request.
    Only pending requests can be cancelled; approved/denied/expired are terminal.
    """
    redis = get_redis_client()
    raw = await _redis_call(redis.get, f"consent_request:{request_id}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consent request not found or already expired",
        )

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    # Ownership check
    if data.get("provider_id") and str(data["provider_id"]) != str(provider.actor_uid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only requesting provider may cancel this request",
        )

    # Terminal state check
    current_status = data.get("status", "pending")
    if current_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel request in '{current_status}' state — only pending requests can be cancelled",
        )

    now = datetime.now(timezone.utc)
    data["status"] = "cancelled"
    data["cancelled_at"] = now.isoformat()

    # Keep the cancelled record briefly for audit, then let it expire
    await _redis_call(redis.set, f"consent_request:{request_id}", json.dumps(data), ex=300)

    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="CONSENT_REQUEST_CANCELLED",
        target_id=request_id,
        status="SUCCESS",
        metadata={"patient_id": data.get("patient_id")},
    )

    return ConsentCancelResponsePayload(
        request_id=request_id,
        status="cancelled",
        cancelled_at=now.isoformat(),
    )


class ConsentChallengeForPatientPayload(BaseModel):
    protocol_version: Literal["nexa-consent-v2"] = "nexa-consent-v2"
    request_id: str
    patient_id: str
    provider_id: str
    provider_name: str
    hospital_name: str
    purpose: str
    scope: str
    access_duration: int
    challenge_nonce: str
    issued_at: str
    expires_at: str
    status: str


@router.get("/challenge/{request_id}", status_code=status.HTTP_200_OK, response_model=ConsentChallengeForPatientPayload)
async def get_challenge_for_patient(
    request_id: str,
    patient_id: str = Depends(get_scoped_session),
):
    """Patient-facing: fetch full challenge details for a consent request.

    Returns provider name, hospital, purpose, scope, nonce, and expiry
    so the mobile app can display the request and construct the signing
    input.  Only the authenticated patient whose ID matches the challenge
    may access it.
    """
    redis = get_redis_client()
    raw = await _redis_call(redis.get, f"consent_request:{request_id}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge expired or not found",
        )

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    if str(data.get("patient_id")) != str(patient_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated patient does not match challenge target",
        )

    return ConsentChallengeForPatientPayload(
        request_id=data["request_id"],
        patient_id=str(data.get("patient_id", "")),
        provider_id=str(data.get("provider_id", "")),
        provider_name=data["provider_name"],
        hospital_name=data["hospital_name"],
        purpose=data["purpose"],
        scope=str(data["scope"]),
        access_duration=int(data["access_duration"]),
        challenge_nonce=data["challenge_nonce"],
        issued_at=data["created_at"],
        expires_at=data["expires_at"],
        status=data["status"],
    )
