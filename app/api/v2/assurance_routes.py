"""
API routes for Consent Assurance (Push Approval)
Wires the refactored AssuranceService into the backend.
"""

from __future__ import annotations

import logging
import hashlib
import os
import asyncio
import base64
import binascii
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider, get_scoped_session
from app.core.redis import get_redis_client
from app.models.provider_context import ProviderContext
from app.models.push_token import PatientPushToken
from app.core.rate_limiter import ConcurrentPushLimiter
from app.services.assurance_service import AssuranceService
from app.services.push_notification_service import PushNotificationService
from app.services.biometric_signature_verifier import BiometricSignatureVerifier
from app.services.biometric_registry import update_device_public_key
from app.services.provider_auth_service import resolve_provider_session_context
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")
router = APIRouter(prefix="/api/v2/push", tags=["push-approval"])
service = AssuranceService()
push_service = PushNotificationService()
bio_verifier = BiometricSignatureVerifier()
push_limiter = ConcurrentPushLimiter()

# Feature Flag: Default to 'poll'
PUSH_STATUS_TRANSPORT = os.getenv("PUSH_STATUS_TRANSPORT", "poll")

@router.get("/transport-config")
async def get_transport_config():
    """Returns the configured transport for push status updates."""
    return {"transport": PUSH_STATUS_TRANSPORT}

class PushRequestPayload(BaseModel):
    patient_id: str
    provider_id: str
    purpose: str
    scope: str

class PushRespondPayload(BaseModel):
    decision: Literal["approved", "denied"]
    signature: str = Field(..., min_length=1)
    nonce: str = Field(..., min_length=1)

class PushTokenRegistration(BaseModel):
    expo_push_token: str = Field(..., pattern=r"^ExponentPushToken\[.*\]$")
    platform: Literal["ios", "android"]

class DeviceKeyRegistration(BaseModel):
    public_key: str = Field(..., min_length=1, max_length=4096)

@router.post("/register-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_push_token(
    payload: PushTokenRegistration,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session)
):
    """Register an Expo push token for the authenticated patient."""
    
    # Upsert pattern
    stmt = select(PatientPushToken).where(
        PatientPushToken.patient_id == patient_id,
        PatientPushToken.expo_push_token == payload.expo_push_token
    )
    result = await db.execute(stmt)
    token_record = result.scalar_one_or_none()
    
    if not token_record:
        token_record = PatientPushToken(
            patient_id=patient_id,
            expo_push_token=payload.expo_push_token,
            platform=payload.platform,
            is_active=True
        )
        db.add(token_record)
    else:
        token_record.is_active = True
        token_record.platform = payload.platform
        
    await db.commit()
    return

@router.post("/register-device-key", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_key(
    payload: DeviceKeyRegistration,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session)
):
    """Attach a patient device signing public key to an existing biometric binding."""
    try:
        device_public_key = base64.b64decode(payload.public_key, validate=True)
        public_key = serialization.load_der_public_key(device_public_key)
    except (binascii.Error, UnsupportedAlgorithm, ValueError):
        await append_audit_log_or_503(
            actor_uid=patient_id,
            event_type="DEVICE_KEY_REGISTRATION",
            target_id=patient_id,
            status="FAILED",
            metadata={"reason": "invalid_public_key"},
        )
        raise HTTPException(status_code=400, detail="Invalid device public key.")

    if not (
        isinstance(public_key, ec.EllipticCurvePublicKey)
        and isinstance(public_key.curve, ec.SECP256R1)
    ):
        await append_audit_log_or_503(
            actor_uid=patient_id,
            event_type="DEVICE_KEY_REGISTRATION",
            target_id=patient_id,
            status="FAILED",
            metadata={"reason": "unsupported_public_key_algorithm"},
        )
        raise HTTPException(status_code=400, detail="Unsupported device public key algorithm.")

    updated = await update_device_public_key(
        masked_internal_id=patient_id,
        device_public_key=device_public_key,
        db=db,
    )
    if not updated:
        await append_audit_log_or_503(
            actor_uid=patient_id,
            event_type="DEVICE_KEY_REGISTRATION",
            target_id=patient_id,
            status="FAILED",
            metadata={"reason": "active_biometric_enrollment_required"},
        )
        raise HTTPException(
            status_code=409,
            detail="Active biometric enrollment required before registering a device key.",
        )

    await append_audit_log_or_503(
        actor_uid=patient_id,
        event_type="DEVICE_KEY_REGISTRATION",
        target_id=patient_id,
        status="SUCCESS",
        metadata={"key_algorithm": "ECDSA_P256"},
    )
    return

@router.post("/request", status_code=status.HTTP_201_CREATED)
async def initiate_push_request(
    payload: PushRequestPayload,
    background_tasks: BackgroundTasks,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session)
):
    """Doctor initiates a push approval request."""
    # 1. Enforce Concurrent and Rate Limits
    await push_limiter.check_and_acquire(
        patient_id=payload.patient_id,
        provider_id=provider.actor_uid
    )

    redis = get_redis_client()
    
    # 2. Create the pending request in Redis and Postgres
    result = await service.create_push_request(
        redis=redis,
        db=db,
        patient_id=payload.patient_id,
        provider_id=payload.provider_id,
        purpose=payload.purpose,
        scope=payload.scope
    )
    
    # Look up the patient's push token
    stmt = select(PatientPushToken).where(
        PatientPushToken.patient_id == payload.patient_id,
        PatientPushToken.is_active
    ).order_by(PatientPushToken.updated_at.desc()).limit(1)
    
    db_result = await db.execute(stmt)
    push_token_record = db_result.scalar_one_or_none()
    
    if not push_token_record:
        logger.warning(f"PUSH_TOKEN_NOT_FOUND for patient {payload.patient_id}")
        return {
            **result,
            "notification_sent": False,
            "fallback": "standard"
        }
        
    # Trigger push asynchronously
    background_tasks.add_task(
        push_service.send_approval_request,
        patient_id=payload.patient_id,
        request_id=result["request_id"],
        provider_name=provider.provider.display_name,
        purpose=payload.purpose,
        expo_push_token=push_token_record.expo_push_token
    )
    
    return {
        **result,
        "notification_sent": True
    }

@router.post("/{request_id}/respond")
async def respond_to_push(
    request_id: str,
    payload: PushRespondPayload,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session)
):
    """Patient responds to a push approval request with biometric signature."""
    redis = get_redis_client()
    
    # 1. Verify Biometric Signature
    # Requirement: patient signs SHA-256(nonce + request_id + patient_id)
    verification = await bio_verifier.verify_signature(
        patient_id=patient_id,
        request_id=request_id,
        signature_b64=payload.signature,
        challenge_nonce=payload.nonce,
        redis=redis,
        db=db
    )
    
    if not verification.verified:
        await append_audit_log_or_503(
            actor_uid=patient_id,
            event_type="BIOMETRIC_VERIFICATION_FAILED",
            target_id=request_id,
            status="FAILED",
            metadata={"error": verification.error}
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail=f"Biometric verification failed: {verification.error}"
        )

    # 2. Resolve Approval Atomically
    result = await service.resolve_push_approval(
        redis=redis,
        db=db,
        request_id=request_id,
        patient_id=patient_id,
        decision=payload.decision,
        signature_hash=hashlib.sha256(payload.signature.encode()).hexdigest()
    )
    
    if result is None:
        raise HTTPException(status_code=404, detail="Push request not found or expired")
    
    if isinstance(result, dict):
        # Resolve success: release the patient concurrency lock
        await push_limiter.release(patient_id=patient_id)
        
        if payload.decision == "approved":
            await append_audit_log_or_503(
                actor_uid=patient_id,
                event_type="BIOMETRIC_VERIFICATION_SUCCESS",
                target_id=request_id,
                status="SUCCESS"
            )
            
        return result

    if result == "already_resolved":
        raise HTTPException(status_code=409, detail="Push request already resolved")
    if result == "unauthorized":
        raise HTTPException(status_code=403, detail="Unauthorized response")
        
    return result

@router.get("/{request_id}/status")
async def poll_push_status(
    request_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session)
):
    """Poll the current resolution status of a push request."""
    redis = get_redis_client()
    result = await service.get_push_status(redis, db, request_id)
    
    if result["status"] == "timeout" and result.get("patient_id"):
        await push_limiter.release(patient_id=result["patient_id"])
        
    return result

# WebSocket implementation (gated by feature flag)
@router.websocket("/{request_id}/ws")
async def push_status_websocket(
    websocket: WebSocket,
    request_id: str,
    token: str | None = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    WebSocket endpoint for real-time push status updates.
    Gated by PUSH_STATUS_TRANSPORT feature flag.
    """
    if PUSH_STATUS_TRANSPORT != "websocket":
        await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
        return

    # 1. Authenticate via token in query parameter
    # Since dependencies don't work the same for WebSockets in FastAPI 
    # when using query params for auth, we manual verify.
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    provider_payload = await resolve_provider_session_context(token)
    if not provider_payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    from app.services.consent_engine import get_consent_redis_client
    redis = get_consent_redis_client()
    
    # Prefix for keyspace notifications
    # Requires 'notify-keyspace-events' set to include 'K' and 'g' (or 'E' and 'x' for expiry)
    # We'll listen for SET (resolution) and DEL/EXPIRE (timeout)
    pubsub = redis.pubsub()
    
    # Watching the specific key's events
    # The format depends on Redis configuration, usually __keyspace@0__:<key>
    channel = f"__keyspace@0__:push_request:{request_id}"
    
    try:
        await pubsub.subscribe(channel)
        
        # Send initial status
        current_status = await service.get_push_status(redis, db, request_id)
        await websocket.send_json(current_status)
        
        if current_status["status"] != "pending":
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            return

        # Auto-close after 95 seconds to match push TTL + buffer
        async def close_after_timeout():
            await asyncio.sleep(95)
            try:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            except Exception:
                pass

        timeout_task = asyncio.create_task(close_after_timeout())

        while True:
            # Wait for Redis event
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            
            if message:
                # Refresh status and send to client
                new_status = await service.get_push_status(redis, db, request_id)
                await websocket.send_json(new_status)
                
                if new_status["status"] != "pending":
                    break
            
            # Keep-alive/Check connection
            try:
                # await websocket.receive_text() would block, so we use a small sleep or ping logic
                # Here we just continue the loop
                await asyncio.sleep(0.1)
            except Exception:
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for request {request_id}")
    except Exception as e:
        logger.error(f"WebSocket error for request {request_id}: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        if 'timeout_task' in locals():
            timeout_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass
