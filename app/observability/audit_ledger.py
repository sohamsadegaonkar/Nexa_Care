"""Partitioned, concurrency-safe, O(1)-append audit ledger.

DEFECT 8: replaces the previous global design (one advisory lock, full-chain
read, full-chain validation on every append -- O(n) per event) with a
partitioned chain-head design. Each chain_scope ("partition") owns exactly
one row in audit_chain_heads; appending to a partition locks only that row
(SELECT ... FOR UPDATE), never scans the partition's history, and updates
the head in the same transaction as the insert. Different partitions never
block each other.

Full cryptographic verification (walking from GENESIS, recalculating every
hash, confirming sequence continuity) is intentionally NOT done on the hot
append path anymore -- that is the job of the separate operator verifier,
scripts/verify_audit_partitions.py, which marks a partition unhealthy on
failure. An unhealthy partition fails closed: appends to it are rejected
until an operator resolves the issue.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from app.core.database import get_async_engine
from app.core.request_context import trace_id_var
from app.observability.safe_exceptions import log_safe_exception
from app.observability.security_metrics import AUDIT_LEDGER_INTEGRITY_FAILURES
from app.security.audit_context import AuditContext, derive_audit_partition

logger = logging.getLogger("nexa_logger")
security_logger = logging.getLogger("nexa_security")

AUDIT_PROTOCOL_VERSION = 2
_AUDIT_MAX_RETRIES = 5
_SAFE_METADATA_KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_SAFE_METADATA_VALUE = re.compile(r"^[a-zA-Z0-9_.:@/ -]{0,128}$")

_HEAD_LOCK_SQL = text(
    """
    SELECT chain_partition, head_event_id, head_hash, sequence_number, protocol_version, is_healthy
    FROM public.audit_chain_heads
    WHERE chain_partition = :chain_partition
    FOR UPDATE
    """
)
_HEAD_INSERT_SQL = text(
    """
    INSERT INTO public.audit_chain_heads
        (chain_partition, head_event_id, head_hash, sequence_number, protocol_version, is_healthy, updated_at)
    VALUES (:chain_partition, :head_event_id, :head_hash, :sequence_number, :protocol_version, TRUE, now())
    """
)
_HEAD_UPDATE_SQL = text(
    """
    UPDATE public.audit_chain_heads
    SET head_event_id = :head_event_id, head_hash = :head_hash,
        sequence_number = :sequence_number, updated_at = now()
    WHERE chain_partition = :chain_partition
    """
)
_IDEMPOTENCY_SQL = text(
    """SELECT record_hash FROM public.audit_ledger
       WHERE chain_scope = :chain_partition AND idempotency_key = :idempotency_key"""
)
_INSERT_SQL = text(
    """
    INSERT INTO public.audit_ledger (
        patient_uuid, actor_type, actor_id, action, resource, details,
        timestamp, trace_id, status, previous_hash, record_hash,
        chain_scope, protocol_version, idempotency_key, sequence_number
    ) VALUES (
        CAST(:patient_uuid AS UUID), :actor_type, :actor_id, :action,
        :resource, CAST(:details AS JSONB), CAST(:event_timestamp AS TIMESTAMPTZ),
        :trace_id, :status, :previous_hash, :record_hash,
        :chain_scope, :protocol_version, :idempotency_key, :sequence_number
    )
    RETURNING audit_id
    """
)
_READ_FOR_TARGET_SQL = text(
    """
    SELECT audit_id, trace_id, actor_id AS actor_uid, action AS event_type,
           resource AS target_resource_id, status, details AS payload,
           previous_hash, record_hash, created_at, timestamp,
           chain_scope, protocol_version, sequence_number
    FROM public.audit_ledger
    WHERE resource = :target_id
    ORDER BY created_at DESC, audit_id DESC
    LIMIT :limit
    """
)
_MARK_UNHEALTHY_SQL = text(
    "UPDATE public.audit_chain_heads SET is_healthy = FALSE, updated_at = now() WHERE chain_partition = :chain_partition"
)


class AuditIntegrityError(RuntimeError):
    """Raised when a chain partition is not safe to append to."""

    def __init__(self, reason: str):
        super().__init__("Audit ledger integrity violation")
        self.reason = reason


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _calculate_hash(payload: dict[str, Any], previous_hash: str) -> str:
    return hashlib.sha256((_canonical_json(payload) + previous_hash).encode("utf-8")).hexdigest()


def _is_unique_violation(exc: BaseException) -> bool:
    candidates = (exc, getattr(exc, "orig", None), getattr(getattr(exc, "orig", None), "__cause__", None))
    return any(
        candidate is not None
        and any(getattr(candidate, attr, None) == "23505" for attr in ("sqlstate", "pgcode", "code"))
        for candidate in candidates
    )


def _patient_uuid_or_none(target_id: str) -> str | None:
    try:
        return str(uuid.UUID(str(target_id)))
    except (TypeError, ValueError, AttributeError):
        return None


def _event_datetime(timestamp: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not _SAFE_METADATA_KEY.fullmatch(key):
            continue
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value if _SAFE_METADATA_VALUE.fullmatch(value) else "[REDACTED]"
        elif isinstance(value, list) and len(value) <= 32:
            clean[key] = [
                item if isinstance(item, (bool, int, float)) or item is None
                else item if isinstance(item, str) and _SAFE_METADATA_VALUE.fullmatch(item)
                else "[REDACTED]"
                for item in value
            ]
    return clean or None


def _idempotency_key(
    *, trace_id: str, event_type: str, target_id: str, status: str,
    metadata: dict[str, Any] | None, supplied: str | None,
) -> str:
    if supplied:
        return hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    identity = {"trace_id": trace_id, "event": event_type, "target_id": target_id, "status": status, "metadata": metadata}
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


async def _append_once(
    *, actor_uid: str, event_type: str, target_id: str, status: str,
    trace_id: str, timestamp: str, metadata: dict[str, Any] | None,
    idempotency_key: str, chain_partition: str,
) -> dict[str, Any]:
    """O(1) append: lock only this partition's head row, no history scan."""
    async with get_async_engine().begin() as connection:
        existing = await connection.execute(
            _IDEMPOTENCY_SQL,
            {"chain_partition": chain_partition, "idempotency_key": idempotency_key},
        )
        existing_hash = existing.scalar_one_or_none()
        if existing_hash:
            return {"record_hash": existing_hash, "idempotent_replay": True}

        head_result = await connection.execute(_HEAD_LOCK_SQL, {"chain_partition": chain_partition})
        head_row = head_result.mappings().first()

        if head_row is not None and not head_row["is_healthy"]:
            raise AuditIntegrityError("partition_unhealthy")

        previous_hash = head_row["head_hash"] if head_row is not None else "GENESIS"
        next_sequence = (head_row["sequence_number"] + 1) if head_row is not None else 1

        payload: dict[str, Any] = {
            "protocol_version": AUDIT_PROTOCOL_VERSION,
            "chain_scope": chain_partition,
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
            "patient_uuid": _patient_uuid_or_none(target_id), "actor_type": "application",
            "actor_id": actor_uid, "action": event_type, "resource": target_id,
            "details": _canonical_json(payload), "event_timestamp": _event_datetime(timestamp),
            "trace_id": trace_id, "status": status, "previous_hash": previous_hash,
            "record_hash": record_hash, "chain_scope": chain_partition,
            "protocol_version": AUDIT_PROTOCOL_VERSION, "idempotency_key": idempotency_key,
            "sequence_number": next_sequence,
        }
        insert_result = await connection.execute(_INSERT_SQL, row)
        new_audit_id = insert_result.scalar_one()

        if head_row is None:
            await connection.execute(_HEAD_INSERT_SQL, {
                "chain_partition": chain_partition, "head_event_id": new_audit_id,
                "head_hash": record_hash, "sequence_number": 1,
                "protocol_version": AUDIT_PROTOCOL_VERSION,
            })
        else:
            await connection.execute(_HEAD_UPDATE_SQL, {
                "chain_partition": chain_partition, "head_event_id": new_audit_id,
                "head_hash": record_hash, "sequence_number": next_sequence,
            })

        return {**row, "payload": payload, "audit_id": new_audit_id}


async def _report_integrity_failure(reason: str, trace_id: str, chain_partition: str) -> None:
    AUDIT_LEDGER_INTEGRITY_FAILURES.labels(chain_scope=chain_partition, reason=reason).inc()
    security_logger.critical(
        _canonical_json({
            "event": "audit_ledger_integrity_violation", "severity": "critical",
            "chain_scope": chain_partition, "reason": reason, "trace_id": trace_id,
            "operator_action": "halt_audit_writes_and_investigate",
        })
    )
    try:
        async with get_async_engine().begin() as connection:
            await connection.execute(_MARK_UNHEALTHY_SQL, {"chain_partition": chain_partition})
    except Exception:
        pass  # best-effort; the append already failed closed regardless


async def _append_audit_log_to_partition(
    *, chain_partition: str, actor_uid: str, event_type: str, target_id: str, status: str,
    metadata: dict[str, Any] | None = None, event_timestamp: str | None = None,
    idempotency_key: str | None = None,
) -> bool:
    trace_id = trace_id_var.get() or f"trace-{uuid.uuid4().hex}"
    timestamp = (event_timestamp or datetime.datetime.now(datetime.UTC).isoformat())
    timestamp = _event_datetime(timestamp).isoformat()
    safe_metadata = _sanitize_metadata(metadata)
    event_identity = _idempotency_key(
        trace_id=trace_id, event_type=event_type, target_id=target_id,
        status=status, metadata=safe_metadata, supplied=idempotency_key,
    )
    for attempt in range(1, _AUDIT_MAX_RETRIES + 1):
        try:
            await _append_once(
                actor_uid=actor_uid, event_type=event_type, target_id=target_id,
                status=status, trace_id=trace_id, timestamp=timestamp,
                metadata=safe_metadata, idempotency_key=event_identity,
                chain_partition=chain_partition,
            )
            return True
        except AuditIntegrityError as exc:
            await _report_integrity_failure(exc.reason, trace_id, chain_partition)
            return False
        except Exception as exc:
            if _is_unique_violation(exc) and attempt < _AUDIT_MAX_RETRIES:
                logger.warning(_canonical_json({
                    "event": "audit_append_concurrency_retry", "trace_id": trace_id,
                    "attempt": attempt, "chain_scope": chain_partition,
                }))
                continue
            log_safe_exception(
                logger, logging.CRITICAL, "audit_log_write_failed", exc,
                subsystem="database", operation="append_audit_event",
                fields={"trace_id": trace_id, "attempt": attempt, "chain_scope": chain_partition},
            )
            return False
    return False


async def append_audit_log(
    *, audit_context: AuditContext, actor_uid: str, event_type: str,
    target_id: str, status: str, metadata: dict[str, Any] | None = None,
    event_timestamp: str | None = None, idempotency_key: str | None = None,
) -> bool:
    """Append using a mandatory trusted tenant, hospital, or platform context."""
    return await _append_audit_log_to_partition(
        chain_partition=derive_audit_partition(audit_context),
        actor_uid=actor_uid, event_type=event_type, target_id=target_id, status=status,
        metadata=metadata, event_timestamp=event_timestamp, idempotency_key=idempotency_key,
    )


async def append_audit_log_for_stored_partition(
    *, stored_partition: str, actor_uid: str, event_type: str,
    target_id: str, status: str, metadata: dict[str, Any] | None = None,
    event_timestamp: str | None = None, idempotency_key: str | None = None,
) -> bool:
    """Outbox-only path honoring a partition persisted by a trusted transaction."""
    if not stored_partition.startswith(("tenant:", "hospital:", "platform:")):
        raise ValueError("Stored audit partition is not a trusted canonical partition.")
    return await _append_audit_log_to_partition(
        chain_partition=stored_partition,
        actor_uid=actor_uid, event_type=event_type, target_id=target_id, status=status,
        metadata=metadata, event_timestamp=event_timestamp, idempotency_key=idempotency_key,
    )


async def read_audit_events(target_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 200))
    async with get_async_engine().connect() as connection:
        result = await connection.execute(_READ_FOR_TARGET_SQL, {"target_id": str(target_id), "limit": bounded_limit})
        return [dict(row) for row in result.mappings().all()]


async def append_audit_log_or_503(
    *, audit_context: AuditContext, actor_uid: str, event_type: str, target_id: str, status: str,
    metadata: dict[str, Any] | None = None, event_timestamp: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    if not await append_audit_log(
        audit_context=audit_context, actor_uid=actor_uid, event_type=event_type,
        target_id=target_id, status=status, metadata=metadata,
        event_timestamp=event_timestamp, idempotency_key=idempotency_key,
    ):
        raise HTTPException(
            status_code=503,
            detail={"error_code": "AUDIT_LEDGER_UNAVAILABLE", "message": "Protected operation aborted because audit integrity could not be assured."},
        )
