"""
API routes for Consent Assurance (Push Approval)
Wires the refactored AssuranceService into the backend.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import logging
import inspect
import os
import asyncio
import base64
import binascii
from datetime import datetime, timezone
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider, get_scoped_session
from app.core.redis import get_async_redis_client as get_redis_client
from app.models.provider_context import ProviderContext
from app.models.push_token import PatientPushToken
from app.core.rate_limiter import ConcurrentPushLimiter
from app.services.assurance_service import AssuranceService
from app.services.push_notification_service import PushNotificationService
from app.services.biometric_registry import update_device_public_key
from app.services.provider_auth_service import resolve_provider_session_context
from app.observability.audit_ledger import append_audit_log_or_503
from app.observability.safe_exceptions import log_safe_exception

logger = logging.getLogger("nexa_logger")
router = APIRouter(prefix="/api/v2/push", tags=["push-approval"])
service = AssuranceService()
push_service = PushNotificationService()
push_limiter = ConcurrentPushLimiter()

# Feature Flag: Default to 'poll'
PUSH_STATUS_TRANSPORT = os.getenv("PUSH_STATUS_TRANSPORT", "poll")


@router.get("/transport-config")
async def get_transport_config():
    """Returns the configured transport for push status updates."""
    return {"transport": PUSH_STATUS_TRANSPORT}


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _check_keyspace_notifications(redis) -> tuple[bool, str]:
    """Verify Redis keyspace notifications required by push WebSockets."""

    config_get = getattr(redis, "config_get", None)
    if config_get is None:
        config = getattr(redis, "config", None)
        if config is None:
            return (
                False,
                "Redis client does not expose CONFIG GET for keyspace notification checks.",
            )
        try:
            raw = await _maybe_await(config("GET", "notify-keyspace-events"))
        except Exception as exc:
            return (
                False,
                f"Redis CONFIG GET notify-keyspace-events is unavailable: {exc}",
            )
    else:
        try:
            raw = await _maybe_await(config_get("notify-keyspace-events"))
        except Exception as exc:
            return (
                False,
                f"Redis CONFIG GET notify-keyspace-events is unavailable: {exc}",
            )

    if isinstance(raw, dict):
        value = (
            raw.get("notify-keyspace-events")
            or raw.get(b"notify-keyspace-events")
            or ""
        )
    elif isinstance(raw, list | tuple) and len(raw) >= 2:
        value = raw[1]
    else:
        value = raw or ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    flags = str(value)

    if "K" not in flags or not any(flag in flags for flag in ("A", "$", "g", "x")):
        return False, (
            "Redis keyspace notifications are disabled or incomplete for push WebSockets. "
            "Set notify-keyspace-events to include K and string/generic/expired events, or use polling."
        )
    return True, flags


class PushRequestPayload(BaseModel):
    patient_id: str
    provider_id: str
    purpose: str
    scope: str


class PushTokenRegistration(BaseModel):
    expo_push_token: str = Field(
        ...,
        pattern=r"^(?:ExponentPushToken|ExpoPushToken)\[[^\]]+\]$",
        max_length=255,
    )
    platform: Literal["ios", "android"]


class DeviceKeyRegistration(BaseModel):
    public_key: str = Field(..., min_length=1, max_length=4096)


async def _send_push_and_record_delivery(
    *,
    request_id: str,
    patient_id: str,
    provider_name: str,
    purpose: str,
    expo_push_token: str,
) -> None:
    redis = get_redis_client()
    await service.mark_delivery_attempted(redis, request_id)
    result = await push_service.send_approval_request(
        patient_id=patient_id,
        request_id=request_id,
        provider_name=provider_name,
        purpose=purpose,
        expo_push_token=expo_push_token,
    )
    if result is None:
        await service.mark_delivery_result(
            redis,
            request_id,
            success=False,
            error="Push delivery did not return a result",
        )
        return
    await service.mark_delivery_result(
        redis,
        request_id,
        success=result.success,
        error=result.error,
    )


@router.post("/register-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_push_token(
    payload: PushTokenRegistration,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Register an Expo push token for the authenticated patient."""

    # Lock every record for this device token so a token can be active for
    # only the patient who most recently authenticated on the device.
    stmt = (
        select(PatientPushToken)
        .where(PatientPushToken.expo_push_token == payload.expo_push_token)
        .with_for_update()
    )
    result = await db.execute(stmt)
    token_records = result.scalars().all()
    token_record = next(
        (
            record
            for record in token_records
            if str(record.patient_id) == str(patient_id)
        ),
        None,
    )

    for record in token_records:
        if record is not token_record:
            record.is_active = False

    now = datetime.now(timezone.utc)
    if token_record is None:
        token_record = PatientPushToken(
            patient_id=patient_id,
            expo_push_token=payload.expo_push_token,
            platform=payload.platform,
            is_active=True,
        )
        db.add(token_record)
    else:
        token_record.is_active = True
        token_record.platform = payload.platform
        token_record.updated_at = now

    await db.commit()


@router.post(
    "/register-device-key", status_code=status.HTTP_204_NO_CONTENT, deprecated=True
)
async def register_device_key(
    payload: DeviceKeyRegistration,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Deprecated legacy biometric-registry key attachment.

    Canonical signed approval uses ``patient_device_keys`` via
    ``/api/v2/patient/devices/enroll`` and ``/api/v2/consent/approve-signed``.
    This route is retained only for legacy push-approval compatibility.
    """
    try:
        device_public_key = base64.b64decode(payload.public_key, validate=True)
        public_key = serialization.load_der_public_key(device_public_key)
    except (binascii.Error, UnsupportedAlgorithm, ValueError):
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
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
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=patient_id,
            event_type="DEVICE_KEY_REGISTRATION",
            target_id=patient_id,
            status="FAILED",
            metadata={"reason": "unsupported_public_key_algorithm"},
        )
        raise HTTPException(
            status_code=400, detail="Unsupported device public key algorithm."
        )

    updated = await update_device_public_key(
        masked_internal_id=patient_id,
        device_public_key=device_public_key,
        db=db,
    )
    if not updated:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
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
        audit_context=current_audit_context(AuditDomain.CONSENT),
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
    db: AsyncSession = Depends(get_db_session),
):
    """Doctor initiates a push approval request."""
    # 1. Enforce Concurrent and Rate Limits
    await push_limiter.check_and_acquire(
        patient_id=payload.patient_id, provider_id=provider.actor_uid
    )

    redis = get_redis_client()
    try:
        # 2. Create the pending request in Redis and Postgres
        result = await service.create_push_request(
            redis=redis,
            db=db,
            patient_id=payload.patient_id,
            provider_id=payload.provider_id,
            purpose=payload.purpose,
            scope=payload.scope,
        )

        # Look up the patient's push token
        stmt = (
            select(PatientPushToken)
            .where(
                PatientPushToken.patient_id == payload.patient_id,
                PatientPushToken.is_active,
            )
            .order_by(PatientPushToken.updated_at.desc())
            .limit(1)
        )

        db_result = await db.execute(stmt)
        push_token_record = db_result.scalar_one_or_none()

        if not push_token_record:
            logger.warning(
                "push_token_not_found",
                extra={"patient_ref": str(payload.patient_id)[-8:]},
            )
            await service.mark_delivery_unavailable(
                redis, result["request_id"], "No active push token"
            )
            return {
                **result,
                "notification_dispatch": "unavailable",
                "notification_queued": False,
                "delivery_status": "unavailable",
                "fallback": "standard",
            }

        # Trigger push asynchronously. The endpoint reports queuing only; the
        # background task records sent/failed delivery status after the Expo call.
        background_tasks.add_task(
            _send_push_and_record_delivery,
            request_id=result["request_id"],
            patient_id=payload.patient_id,
            provider_name=provider.provider.display_name,
            purpose=payload.purpose,
            expo_push_token=push_token_record.expo_push_token,
        )

        return {
            **result,
            "notification_dispatch": "queued",
            "notification_queued": True,
            "delivery_status": "queued",
        }
    except Exception:
        await push_limiter.release(patient_id=payload.patient_id)
        raise


@router.get("/{request_id}/status")
async def poll_push_status(
    request_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
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
    db: AsyncSession = Depends(get_db_session),
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

    keyspace_ready, keyspace_detail = await _check_keyspace_notifications(redis)
    if not keyspace_ready:
        logger.warning("Push WebSocket transport unavailable: %s", keyspace_detail)
        await websocket.send_json(
            {
                "request_id": request_id,
                "status": "websocket_unavailable",
                "fallback": "poll",
                "detail": keyspace_detail,
            }
        )
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return

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
            except Exception as exc:
                logger.debug(
                    "Push WebSocket already closed after timeout",
                    extra={"error_type": type(exc).__name__},
                )

        timeout_task = asyncio.create_task(close_after_timeout())

        while True:
            # Wait for Redis event
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )

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
        logger.info("Push WebSocket disconnected", extra={"request_id": request_id})
    except Exception as exc:
        log_safe_exception(
            logger,
            exc,
            subsystem="websocket",
            operation="push_status_stream",
            fields={"correlation_id": request_id},
        )
    finally:
        await pubsub.unsubscribe(channel)
        if "timeout_task" in locals():
            timeout_task.cancel()
        try:
            await websocket.close()
        except Exception as exc:
            logger.debug(
                "Push WebSocket close during cleanup failed",
                extra={"error_type": type(exc).__name__},
            )
