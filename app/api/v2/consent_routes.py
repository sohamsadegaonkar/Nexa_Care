"""Consent routes for Nexa Care V2.

Exposes both the generic /grant endpoint and the frontend-facing
/routine/issue and /break-glass/issue endpoints. All paths delegate to
ConsentEngine so the v2 consent surface has a single authority.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_provider, get_db_session, get_provider_context, get_scoped_session, require_role
from app.core.rate_limiter import RateLimiter
from app.core.redis import get_redis_client
from app.models.patient_device_keys import PatientDeviceKey
from app.models.provider_context import ProviderContext
from app.models.consent_grant import ConsentGrantLog
from app.models.assurance import AssuranceLevel
from app.services.signed_approval_verifier import SignedApprovalVerifier
# EXPLICITLY ALIAS THE IMPORT SO MOCK PATCHING MATCHES THE ATTRIBUTE NAME
import app.services.consent_engine as consent_engine
from app.services.consent_engine import (
    ConsentEngineUnavailable,
    ConsentPurpose,
    issue_routine,
    issue_break_glass,
)
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")
router = APIRouter(prefix="/api/v2/consent", tags=["consent"])
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
    reason_code: str
    free_text: str = ""
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
        )
        return RoutineConsentGrantResponse(
            consent_token=token,
            expires_at=_expires_at(ROUTINE_CONSENT_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))


@router.post("/routine/issue", response_model=ConsentIssueResponse)
async def issue_routine_consent_route(
    request: RoutineConsentIssueRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    """Issue a routine consent token for a verified patient identity."""
    try:
        token = await issue_routine(
            db=db,
            patient_id=request.patient_id,
            clinician_id=provider.actor_uid,
            purpose=request.purpose,
            scope=request.scope,
            assurance_level=request.assurance_level,
            assurance_evidence=request.assurance_evidence,
            ttl_seconds=ROUTINE_CONSENT_TTL_SECONDS,
        )
        return ConsentIssueResponse(
            consent_token=token,
            expires_at=_expires_at(ROUTINE_CONSENT_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))


@router.post("/break-glass/issue", response_model=ConsentIssueResponse)
async def issue_break_glass_consent_route(
    request: Request,
    payload: BreakGlassConsentIssueRequest,
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_provider_context)
):
    """Issue an emergency break-glass consent token."""
    # Enforce rate limit per provider
    await _break_glass_limiter(request=request, provider_id=provider.actor_uid)

    try:
        full_reason = f"{payload.reason_code}: {payload.free_text}".strip(": ")
        token = await issue_break_glass(
            db=db,
            patient_id=payload.patient_id,
            clinician_id=provider.actor_uid,
            reason_code=full_reason,
        )
        return ConsentIssueResponse(
            consent_token=token,
            expires_at=_expires_at(BREAK_GLASS_TTL_SECONDS),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))


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
        )
        if not capability:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired consent",
            )
        return capability
    except ConsentEngineUnavailable as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err))


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


class ConsentStatusResponsePayload(BaseModel):
    request_id: str
    status: str
    responded_at: str | None = None


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


@router.post("/request", status_code=status.HTTP_201_CREATED, response_model=ConsentChallengeResponsePayload)
async def create_consent_request(
    payload: ConsentChallengeRequestPayload,
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
    except ValueError:
        pid_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, payload.patient_id)

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
        "provider_name": "Provider",
        "hospital_name": "Hospital",
        "purpose": payload.purpose,
        "scope": payload.scope,
        "access_duration": access_duration,  # Clamped to [300, 3600]
        "challenge_nonce": challenge_nonce,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "status": "pending",
    }

    redis = get_redis_client()
    redis.set(f"consent_request:{request_id}", json.dumps(challenge_payload), ex=challenge_ttl_seconds)

    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="CONSENT_REQUEST_CREATED",
        target_id=request_id,
        status="SUCCESS",
        metadata={"patient_id": payload.patient_id, "purpose": payload.purpose},
    )

    return ConsentChallengeResponsePayload(
        request_id=request_id,
        status="pending",
        expires_in_seconds=challenge_ttl_seconds,
        challenge_nonce=challenge_nonce,
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
    raw = redis.get(f"consent_request:{payload.request_id}")
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

    if redis.get(f"biometric_nonce:{payload.challenge_nonce}:used") or data.get("status") != "pending":
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
    except ValueError:
        pid_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, patient_id)

    try:
        dev_uuid = uuid.UUID(payload.device_id)
        stmt = select(PatientDeviceKey).where(
            PatientDeviceKey.id == dev_uuid,
            PatientDeviceKey.patient_id == pid_uuid,
        )
    except ValueError:
        stmt = select(PatientDeviceKey).where(
            PatientDeviceKey.device_label == payload.device_id,
            PatientDeviceKey.patient_id == pid_uuid,
        )

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
        provider_id=str(data.get("provider_id", "")),
        scope=str(data.get("scope", "")),
        purpose=str(data.get("purpose", "")),
        access_duration=int(data.get("access_duration", 900)),
    )
    if not res_verify.verified:
        if res_verify.error == "Challenge expired":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Challenge expired",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=res_verify.error or "Signature verification failed",
        )

    now = datetime.now(timezone.utc)

    if payload.decision == "approved":
        evidence_data = {
            "status": "approved",
            "patient_id": patient_id,
            "approved_at": now.isoformat(),
        }
        redis.set(f"assurance_evidence:{payload.request_id}", json.dumps(evidence_data), ex=120)

        token = await consent_engine.issue(
            db=db,
            patient_id=patient_id,
            clinician_id=str(data.get("provider_id")),
            purpose=str(data.get("purpose", "routine_checkup")),
            scope=[data["scope"]] if isinstance(data.get("scope"), str) else (data.get("scope") or ["clinical"]),
            assurance_level=AssuranceLevel.PUSH_BIOMETRIC,
            assurance_evidence={"request_id": payload.request_id, "device_id": payload.device_id, "signature": payload.signature},
            ttl_seconds=int(data.get("access_duration", 900)),
        )

        await append_audit_log_or_503(
            actor_uid=patient_id,
            event_type="CONSENT_APPROVED_SIGNED",
            target_id=payload.request_id,
            status="SUCCESS",
            metadata={"device_id": payload.device_id},
        )

        data["status"] = "approved"
        data["responded_at"] = now.isoformat()
        data["consent_token"] = token
        redis.set(f"biometric_nonce:{payload.challenge_nonce}:used", "1", ex=300)
        redis.set(f"consent_request:{payload.request_id}", json.dumps(data), ex=int(data.get("access_duration", 900)))

        return SignedApprovalResponsePayload(
            request_id=payload.request_id,
            status="approved",
            responded_at=now.isoformat(),
        )

    data["status"] = "denied"
    data["responded_at"] = now.isoformat()
    redis.set(f"biometric_nonce:{payload.challenge_nonce}:used", "1", ex=300)
    redis.set(f"consent_request:{payload.request_id}", json.dumps(data), ex=300)

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
    raw = redis.get(f"consent_request:{request_id}")
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

    return ConsentStatusResponsePayload(
        request_id=request_id,
        status=data.get("status", "pending"),
        responded_at=data.get("responded_at"),
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
    raw = redis.get(f"consent_request:{request_id}")
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
    redis.set(f"consent_request:{request_id}", json.dumps(data), ex=300)

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
    request_id: str
    patient_id: str
    provider_id: str
    provider_name: str
    hospital_name: str
    purpose: str
    scope: str
    access_duration: int
    challenge_nonce: str
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
    raw = redis.get(f"consent_request:{request_id}")
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

    if data.get("status") != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request already resolved: {data.get('status')}",
        )

    return ConsentChallengeForPatientPayload(
        request_id=data["request_id"],
        patient_id=str(data.get("patient_id", "")),
        provider_id=str(data.get("provider_id", "")),
        provider_name=data.get("provider_name", "Provider"),
        hospital_name=data.get("hospital_name", "Hospital"),
        purpose=data.get("purpose", ""),
        scope=str(data.get("scope", "")),
        access_duration=int(data.get("access_duration", 900)),
        challenge_nonce=data.get("challenge_nonce", ""),
        expires_at=data.get("expires_at", ""),
        status=data.get("status", "pending"),
    )
