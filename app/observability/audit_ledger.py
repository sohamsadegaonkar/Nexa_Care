"""Fork-aware, concurrency-safe append-only audit ledger.

The existing schema is one global chain.  This module keeps that scope
explicit; tenant/patient identifiers remain event attributes and are not
silently treated as independent chains.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import uuid
from collections import Counter
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from app.core.database import get_async_engine
from app.core.request_context import trace_id_var
from app.observability.safe_exceptions import log_safe_exception
from app.observability.security_metrics import AUDIT_LEDGER_INTEGRITY_FAILURES

logger = logging.getLogger("nexa_logger")
security_logger = logging.getLogger("nexa_security")

AUDIT_PROTOCOL_VERSION = 2
AUDIT_CHAIN_SCOPE = "global"
_AUDIT_MAX_RETRIES = 5
_SAFE_METADATA_KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_SAFE_METADATA_VALUE = re.compile(r"^[a-zA-Z0-9_.:@/ -]{0,128}$")

_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))")
_READ_CHAIN_SQL = text(
    """
    SELECT audit_id, previous_hash, record_hash, details, protocol_version,
           idempotency_key
    FROM public.audit_ledger
    WHERE chain_scope = :chain_scope
    ORDER BY created_at ASC, audit_id ASC
    """
)
_IDEMPOTENCY_SQL = text(
    """SELECT record_hash FROM public.audit_ledger
       WHERE chain_scope = :chain_scope AND idempotency_key = :idempotency_key"""
)
_INSERT_SQL = text(
    """
    INSERT INTO public.audit_ledger (
        patient_uuid, actor_type, actor_id, action, resource, details,
        timestamp, trace_id, status, previous_hash, record_hash,
        chain_scope, protocol_version, idempotency_key
    ) VALUES (
        CAST(:patient_uuid AS UUID), :actor_type, :actor_id, :action,
        :resource, CAST(:details AS JSONB), CAST(:event_timestamp AS TIMESTAMPTZ),
        :trace_id, :status, :previous_hash, :record_hash,
        :chain_scope, :protocol_version, :idempotency_key
    )
    """
)
_READ_FOR_TARGET_SQL = text(
    """
    SELECT audit_id, trace_id, actor_id AS actor_uid, action AS event_type,
           resource AS target_resource_id, status, details AS payload,
           previous_hash, record_hash, created_at, timestamp,
           chain_scope, protocol_version
    FROM public.audit_ledger
    WHERE resource = :target_id
    ORDER BY created_at DESC, audit_id DESC
    LIMIT :limit
    """
)


class AuditIntegrityError(RuntimeError):
    """Raised when an existing ledger chain is not safe to append to."""

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


def _row_mapping(row: Any) -> dict[str, Any]:
    return dict(row) if not isinstance(row, dict) else row


def _validate_chain(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "GENESIS"
    hashes = [str(row["record_hash"]) for row in rows]
    predecessors = [str(row["previous_hash"]) for row in rows]
    if any(count > 1 for count in Counter(hashes).values()):
        raise AuditIntegrityError("duplicate_record_hash")
    if any(count > 1 for count in Counter(predecessors).values()):
        raise AuditIntegrityError("duplicate_previous_hash")
    if predecessors.count("GENESIS") != 1:
        raise AuditIntegrityError("invalid_root_count")
    known_hashes = set(hashes)
    if any(previous != "GENESIS" and previous not in known_hashes for previous in predecessors):
        raise AuditIntegrityError("missing_predecessor")
    tips = [record_hash for record_hash in hashes if record_hash not in set(predecessors)]
    if len(tips) != 1:
        raise AuditIntegrityError("multiple_or_missing_tips")
    for row in rows:
        details = row["details"]
        if isinstance(details, str):
            details = json.loads(details)
        if not isinstance(details, dict) or _calculate_hash(details, str(row["previous_hash"])) != row["record_hash"]:
            raise AuditIntegrityError("record_hash_mismatch")
    return tips[0]


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
    idempotency_key: str,
) -> dict[str, Any]:
    async with get_async_engine().begin() as connection:
        await connection.execute(_LOCK_SQL, {"lock_key": f"nexa:audit:{AUDIT_CHAIN_SCOPE}:v{AUDIT_PROTOCOL_VERSION}"})
        existing = await connection.execute(
            _IDEMPOTENCY_SQL,
            {"chain_scope": AUDIT_CHAIN_SCOPE, "idempotency_key": idempotency_key},
        )
        existing_hash = existing.scalar_one_or_none()
        if existing_hash:
            return {"record_hash": existing_hash, "idempotent_replay": True}
        result = await connection.execute(_READ_CHAIN_SQL, {"chain_scope": AUDIT_CHAIN_SCOPE})
        rows = [_row_mapping(row) for row in result.mappings().all()]
        previous_hash = _validate_chain(rows)
        payload: dict[str, Any] = {
            "protocol_version": AUDIT_PROTOCOL_VERSION,
            "chain_scope": AUDIT_CHAIN_SCOPE,
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
            "record_hash": record_hash, "chain_scope": AUDIT_CHAIN_SCOPE,
            "protocol_version": AUDIT_PROTOCOL_VERSION, "idempotency_key": idempotency_key,
        }
        await connection.execute(_INSERT_SQL, row)
        return {**row, "payload": payload}


def _report_integrity_failure(reason: str, trace_id: str) -> None:
    AUDIT_LEDGER_INTEGRITY_FAILURES.labels(chain_scope=AUDIT_CHAIN_SCOPE, reason=reason).inc()
    security_logger.critical(
        _canonical_json({
            "event": "audit_ledger_integrity_violation", "severity": "critical",
            "chain_scope": AUDIT_CHAIN_SCOPE, "reason": reason, "trace_id": trace_id,
            "operator_action": "halt_audit_writes_and_investigate",
        })
    )


async def append_audit_log(
    actor_uid: str, event_type: str, target_id: str, status: str, *,
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
            )
            return True
        except AuditIntegrityError as exc:
            _report_integrity_failure(exc.reason, trace_id)
            return False
        except Exception as exc:
            if _is_unique_violation(exc) and attempt < _AUDIT_MAX_RETRIES:
                logger.warning(_canonical_json({
                    "event": "audit_append_concurrency_retry", "trace_id": trace_id,
                    "attempt": attempt, "chain_scope": AUDIT_CHAIN_SCOPE,
                }))
                continue
            log_safe_exception(
                logger, logging.CRITICAL, "audit_log_write_failed", exc,
                subsystem="database", operation="append_audit_event",
                fields={"trace_id": trace_id, "attempt": attempt, "chain_scope": AUDIT_CHAIN_SCOPE},
            )
            return False
    return False


async def read_audit_events(target_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 200))
    async with get_async_engine().connect() as connection:
        result = await connection.execute(_READ_FOR_TARGET_SQL, {"target_id": str(target_id), "limit": bounded_limit})
        return [dict(row) for row in result.mappings().all()]


async def append_audit_log_or_503(
    actor_uid: str, event_type: str, target_id: str, status: str, *,
    metadata: dict[str, Any] | None = None, event_timestamp: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    if not await append_audit_log(
        actor_uid, event_type, target_id, status, metadata=metadata,
        event_timestamp=event_timestamp, idempotency_key=idempotency_key,
    ):
        raise HTTPException(
            status_code=503,
            detail={"error_code": "AUDIT_LEDGER_UNAVAILABLE", "message": "Protected operation aborted because audit integrity could not be assured."},
        )
