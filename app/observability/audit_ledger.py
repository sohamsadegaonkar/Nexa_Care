"""Immutable, hash-chained audit ledger for Nexa Care.

Fix applied:
  F-05 (corrected 2026-07-03) — append_audit_log() catches the
          PostgreSQL unique_violation (code 23505) that the
          UNIQUE(previous_hash) constraint raises when two concurrent
          writers race on the same previous_hash.  It re-reads the
          current latest hash and retries up to 5 times before giving up.

          VERIFIED against the actual pinned dependency: requirements.txt
          pins supabase==2.9.1, which requires postgrest>=0.17.0,<0.18.0.
          Reading postgrest 0.17.2's request_builder.py directly (both
          _async and _sync) shows execute() raises postgrest.exceptions.
          APIError on any non-2xx PostgREST response — it does not return
          a response object with a populated .error attribute on any code
          path. The prior version of this fix checked
          getattr(response, "error", None), which is unreachable dead
          code under this dependency: a real unique_violation surfaces as
          a raised exception, so it was always being swallowed by the
          generic `except Exception` at the bottom of the loop and
          returned as an unconditional permanent failure — no retry ever
          actually ran.

          The fix now catches the exception directly and duck-types on
          its `.code` attribute (which postgrest.exceptions.APIError sets
          from PostgREST's JSON error body) rather than importing
          postgrest.exceptions.APIError, since postgrest is a transitive
          dependency of supabase-py and not declared directly in
          requirements.txt.
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


def _is_unique_violation(exc: BaseException) -> bool:
    """Return True when `exc` (an exception raised by .execute()) represents
    a PostgreSQL unique_violation (code 23505) on previous_hash.

    postgrest.exceptions.APIError sets a `.code` attribute directly from
    PostgREST's JSON error body — we duck-type on that attribute rather
    than importing the class, since postgrest is a transitive dependency
    of supabase-py and not declared directly in requirements.txt. Fall
    back to a string search for robustness against other exception shapes
    (e.g. a raw httpx error that never got wrapped).
    """
    if getattr(exc, "code", None) == "23505":
        return True
    return "23505" in str(exc)


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

    previous_hash = "GENESIS"

    for attempt in range(1, _AUDIT_MAX_RETRIES + 1):
        # ── Read latest hash ──────────────────────────────────────────────
        try:
            supabase = get_supabase_client()
            latest_response = (
                supabase.table("system_audit")
                .select("record_hash")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.critical(json.dumps({
                "event": "audit_log_write_failed",
                "reason": "could_not_read_previous_hash",
                "trace_id": trace_id,
                "actor_uid": actor_uid,
                "event_type": event_type,
                "target_id": target_id,
                "attempt": attempt,
                "exception": str(exc),
            }))
            return False

        latest_rows = getattr(latest_response, "data", None) or []
        previous_hash = (
            latest_rows[0].get("record_hash")
            if latest_rows and latest_rows[0].get("record_hash")
            else "GENESIS"
        )

        # ── Build payload and hash ──────────────────────────────────────────
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

        # ── Insert ────────────────────────────────────────────────────────
        try:
            supabase.table("system_audit").insert({
                "trace_id": str(trace_id),
                "actor_uid": actor_uid,
                "event_type": event_type,
                "target_resource_id": target_id,
                "status": status,
                "payload": payload,
                "previous_hash": previous_hash,
                "record_hash": new_hash,
            }).execute()

            # execute() only returns here on a 2xx response — see module
            # docstring. No response.error check needed; a real failure
            # would already have raised and landed in the except below.
            return True

        except Exception as exc:
            # F-05: unique_violation means another writer beat us to this
            # previous_hash. Re-read the (now-current) latest hash and
            # retry the insert on the next loop iteration.
            if _is_unique_violation(exc):
                logger.warning(json.dumps({
                    "event": "audit_log_hash_collision",
                    "reason": "unique_violation_on_previous_hash",
                    "trace_id": trace_id,
                    "attempt": attempt,
                    "previous_hash": previous_hash,
                }))
                if attempt < _AUDIT_MAX_RETRIES:
                    continue        # re-read latest hash and retry insert
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

            # Any other exception (insert rejected for a different reason,
            # network failure, etc.) is a permanent failure — do not retry.
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