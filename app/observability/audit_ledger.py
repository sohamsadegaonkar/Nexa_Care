"""Immutable, hash-chained audit ledger for Nexa Care.

Fix applied:
  F-05 — append_audit_log() now catches the PostgreSQL unique_violation
          (code 23505) that the UNIQUE(previous_hash) constraint raises
          when two concurrent writers race on the same previous_hash.  It
          re-reads the current latest hash and retries up to 5 times before
          giving up.  This closes the gap between the migration comment
          (which described this retry) and the old implementation (which had
          none).

          PostgREST surfaces the violation inside the supabase-py response
          object as response.error with a 'code' of '23505', NOT as a
          native psycopg2 exception — the Python process never talks to
          Postgres directly.  The detection therefore inspects
          str(response.error) for the code string, which is the only
          reliable cross-version approach with supabase-py 2.x.
"""
from __future__ import annotations

import hashlib
import json
import logging
import datetime
from typing import Dict, Any

from fastapi import HTTPException

from app.core.supabase import get_supabase_client
from app.core.request_context import trace_id_var

logger = logging.getLogger("nexa_logger")

_AUDIT_MAX_RETRIES = 5          # F-05: bounded retry limit


def _calculate_hash(payload: dict, previous_hash: str) -> str:
    minified_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    combined_buffer = minified_payload + previous_hash
    return hashlib.sha256(combined_buffer.encode("utf-8")).hexdigest()


def _is_unique_violation(error: object) -> bool:
    """Return True when the supabase-py error object represents a PostgreSQL
    unique_violation (code 23505).

    PostgREST wraps the Postgres error in a JSON body.  supabase-py 2.x
    exposes this as a string representation of that body on the .error
    attribute of the execute() result, so we look for the canonical code
    string rather than relying on exception type or HTTP status alone.
    """
    return "23505" in str(error)


async def append_audit_log(
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
    *,
    metadata: dict[str, Any] | None = None,
    event_timestamp: str | None = None,
) -> bool:
    """Append one entry to the hash-chained system_audit table.

    Returns True on success, False on permanent failure.
    Never raises — callers that need a hard failure should use
    append_audit_log_or_503() instead.

    F-05: on a unique_violation on previous_hash (two concurrent writers
    raced), re-reads the now-current latest hash and retries up to
    _AUDIT_MAX_RETRIES times before treating as a permanent failure.
    """
    trace_id = trace_id_var.get()

    for attempt in range(1, _AUDIT_MAX_RETRIES + 1):
        try:
            supabase = get_supabase_client()

            # ── Read latest hash ──────────────────────────────────────────
            latest_response = (
                supabase.table("system_audit")
                .select("record_hash")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

            if getattr(latest_response, "error", None):
                logger.critical(json.dumps({
                    "event": "audit_log_write_failed",
                    "reason": "could_not_read_previous_hash",
                    "trace_id": trace_id,
                    "actor_uid": actor_uid,
                    "event_type": event_type,
                    "target_id": target_id,
                    "attempt": attempt,
                    "error": str(getattr(latest_response, "error", None)),
                }))
                return False

            latest_rows = getattr(latest_response, "data", None) or []
            previous_hash = (
                latest_rows[0].get("record_hash")
                if latest_rows and latest_rows[0].get("record_hash")
                else "GENESIS"
            )

            # ── Build payload and hash ────────────────────────────────────
            payload: Dict[str, Any] = {
                "trace_id": trace_id,
                "actor_uid": actor_uid,
                "event": event_type,
                "target_id": target_id,
                "status": status,
                "timestamp": event_timestamp or datetime.datetime.now(datetime.UTC).isoformat(),
            }
            if metadata:
                payload["metadata"] = metadata
            new_hash = _calculate_hash(payload, previous_hash)

            # ── Insert ────────────────────────────────────────────────────
            insert_response = (
                supabase.table("system_audit")
                .insert({
                    "trace_id": str(trace_id),
                    "actor_uid": actor_uid,
                    "event_type": event_type,
                    "target_resource_id": target_id,
                    "status": status,
                    "payload": payload,
                    "previous_hash": previous_hash,
                    "record_hash": new_hash,
                })
                .execute()
            )

            insert_error = getattr(insert_response, "error", None)

            if not insert_error:
                # Success
                return True

            # F-05: unique_violation means another writer beat us to this
            # previous_hash.  Re-read on the next iteration.
            if _is_unique_violation(insert_error):
                logger.warning(json.dumps({
                    "event": "audit_log_hash_collision",
                    "reason": "unique_violation_on_previous_hash",
                    "trace_id": trace_id,
                    "attempt": attempt,
                    "previous_hash": previous_hash,
                }))
                if attempt < _AUDIT_MAX_RETRIES:
                    continue        # re-read latest hash and retry insert
                # Exhausted retries — fall through to permanent failure log
                logger.critical(json.dumps({
                    "event": "audit_log_write_failed",
                    "reason": "unique_violation_max_retries_exceeded",
                    "trace_id": trace_id,
                    "actor_uid": actor_uid,
                    "event_type": event_type,
                    "target_id": target_id,
                    "attempts": _AUDIT_MAX_RETRIES,
                }))
                return False

            # Any other insert error is a permanent failure.
            logger.critical(json.dumps({
                "event": "audit_log_write_failed",
                "reason": "insert_rejected",
                "trace_id": trace_id,
                "actor_uid": actor_uid,
                "event_type": event_type,
                "target_id": target_id,
                "attempt": attempt,
                "error": str(insert_error),
            }))
            return False

        except Exception as exc:
            logger.critical(json.dumps({
                "event": "audit_log_write_failed",
                "reason": "exception",
                "trace_id": trace_id,
                "actor_uid": actor_uid,
                "event_type": event_type,
                "target_id": target_id,
                "attempt": attempt,
                "exception": str(exc),
            }))
            return False

    # Should be unreachable, but satisfies the type checker.
    return False


async def append_audit_log_or_503(
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
    *,
    metadata: dict[str, Any] | None = None,
    event_timestamp: str | None = None,
) -> None:
    """Same as append_audit_log, but raises HTTPException(503) on failure.

    Use this on routes where a failed audit write must abort the request
    (e.g. biometric enrollment) rather than silently proceeding.
    """
    kwargs: dict[str, Any] = {}
    if metadata is not None:
        kwargs["metadata"] = metadata
    if event_timestamp is not None:
        kwargs["event_timestamp"] = event_timestamp

    success = await append_audit_log(
        actor_uid=actor_uid,
        event_type=event_type,
        target_id=target_id,
        status=status,
        **kwargs,
    )
    if not success:
        raise HTTPException(
            status_code=503,
            detail="Audit ledger write failed; request aborted to avoid an unaudited action.",
        )