"""Canonical, fail-closed hash-chain service for ``public.audit_ledger``."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from app.core.database import get_async_engine
from app.core.request_context import trace_id_var

logger = logging.getLogger("nexa_logger")

_AUDIT_MAX_RETRIES = 5
_LATEST_HASH_SQL = text(
    """
    SELECT candidate.record_hash
    FROM public.audit_ledger AS candidate
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.audit_ledger AS successor
        WHERE successor.previous_hash = candidate.record_hash
    )
    LIMIT 2
    """
)
_INSERT_SQL = text(
    """
    INSERT INTO public.audit_ledger (
        patient_uuid,
        actor_type,
        actor_id,
        action,
        resource,
        details,
        timestamp,
        trace_id,
        status,
        previous_hash,
        record_hash
    ) VALUES (
        CAST(:patient_uuid AS UUID),
        :actor_type,
        :actor_id,
        :action,
        :resource,
        CAST(:details AS JSONB),
        CAST(:event_timestamp AS TIMESTAMPTZ),
        :trace_id,
        :status,
        :previous_hash,
        :record_hash
    )
    """
)
_READ_FOR_TARGET_SQL = text(
    """
    SELECT
        audit_id,
        trace_id,
        actor_id AS actor_uid,
        action AS event_type,
        resource AS target_resource_id,
        status,
        details AS payload,
        previous_hash,
        record_hash,
        created_at,
        timestamp
    FROM public.audit_ledger
    WHERE resource = :target_id
    ORDER BY created_at DESC, audit_id DESC
    LIMIT :limit
    """
)


def _calculate_hash(payload: dict[str, Any], previous_hash: str) -> str:
    minified_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((minified_payload + previous_hash).encode("utf-8")).hexdigest()


def _is_unique_violation(exc: BaseException) -> bool:
    """Recognize PostgreSQL unique violations through SQLAlchemy/asyncpg wrappers."""

    candidates = (exc, getattr(exc, "orig", None), getattr(getattr(exc, "orig", None), "__cause__", None))
    for candidate in candidates:
        if candidate is None:
            continue
        if getattr(candidate, "sqlstate", None) == "23505":
            return True
        if getattr(candidate, "pgcode", None) == "23505":
            return True
        if getattr(candidate, "code", None) == "23505":
            return True
    return "23505" in str(exc)


def _patient_uuid_or_none(target_id: str) -> str | None:
    try:
        return str(uuid.UUID(str(target_id)))
    except (TypeError, ValueError, AttributeError):
        return None


def _event_datetime(timestamp: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


async def _append_once(
    *,
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
    trace_id: str,
    timestamp: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attempt one atomic read-current-tip and append transaction."""

    async with get_async_engine().begin() as connection:
        latest = await connection.execute(_LATEST_HASH_SQL)
        previous_hash = latest.scalar_one_or_none() or "GENESIS"

        payload: dict[str, Any] = {
            "trace_id": trace_id,
            "actor_uid": actor_uid,
            "event": event_type,
            "target_id": target_id,
            "status": status,
            "timestamp": timestamp,
        }
        if metadata:
            payload["metadata"] = metadata

        record_hash = _calculate_hash(payload, previous_hash)
        row = {
            "patient_uuid": _patient_uuid_or_none(target_id),
            "actor_type": "application",
            "actor_id": actor_uid,
            "action": event_type,
            "resource": target_id,
            "details": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            "event_timestamp": _event_datetime(timestamp),
            "trace_id": trace_id,
            "status": status,
            "previous_hash": previous_hash,
            "record_hash": record_hash,
            "payload": payload,
        }
        await connection.execute(_INSERT_SQL, {key: value for key, value in row.items() if key != "payload"})
        return row


async def append_audit_log(
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
    *,
    metadata: dict[str, Any] | None = None,
    event_timestamp: str | None = None,
) -> bool:
    """Append one canonical audit event, retrying only chain-tip races."""

    trace_id = trace_id_var.get() or f"trace-{uuid.uuid4().hex}"
    timestamp = event_timestamp or datetime.datetime.now(datetime.UTC).isoformat()

    for attempt in range(1, _AUDIT_MAX_RETRIES + 1):
        try:
            await _append_once(
                actor_uid=actor_uid,
                event_type=event_type,
                target_id=target_id,
                status=status,
                trace_id=trace_id,
                timestamp=timestamp,
                metadata=metadata,
            )
            return True
        except Exception as exc:
            if _is_unique_violation(exc):
                logger.warning(
                    json.dumps(
                        {
                            "event": "audit_log_hash_collision",
                            "reason": "unique_violation_on_previous_hash",
                            "trace_id": trace_id,
                            "attempt": attempt,
                        }
                    )
                )
                if attempt < _AUDIT_MAX_RETRIES:
                    continue
                reason = "unique_violation_max_retries_exceeded"
            else:
                reason = "database_read_or_write_failure"

            logger.critical(
                json.dumps(
                    {
                        "event": "audit_log_write_failed",
                        "reason": reason,
                        "trace_id": trace_id,
                        "actor_uid": actor_uid,
                        "event_type": event_type,
                        "target_id": target_id,
                        "attempt": attempt,
                        "exception": str(exc),
                    }
                )
            )
            return False

    return False


async def read_audit_events(target_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Read canonical audit events for one target through the same SQL layer."""

    bounded_limit = max(1, min(int(limit), 200))
    async with get_async_engine().connect() as connection:
        result = await connection.execute(
            _READ_FOR_TARGET_SQL,
            {"target_id": str(target_id), "limit": bounded_limit},
        )
        return [dict(row) for row in result.mappings().all()]


async def append_audit_log_or_503(
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
    *,
    metadata: dict[str, Any] | None = None,
    event_timestamp: str | None = None,
) -> None:
    """Append an event or abort the protected operation with HTTP 503."""

    success = await append_audit_log(
        actor_uid=actor_uid,
        event_type=event_type,
        target_id=target_id,
        status=status,
        metadata=metadata,
        event_timestamp=event_timestamp,
    )
    if not success:
        raise HTTPException(
            status_code=503,
            detail="Audit ledger write failed; request aborted to avoid an unaudited action.",
        )
