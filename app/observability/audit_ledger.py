import hashlib
import json
import logging
import datetime
from typing import Dict, Any

from fastapi import HTTPException

from app.core.supabase import get_supabase_client
from app.core.request_context import trace_id_var

logger = logging.getLogger("nexa_logger")


def _calculate_hash(payload: dict, previous_hash: str) -> str:
    minified_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    combined_buffer = minified_payload + previous_hash
    return hashlib.sha256(combined_buffer.encode("utf-8")).hexdigest()


async def append_audit_log(
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
) -> bool:
    trace_id = trace_id_var.get()

    try:
        supabase = get_supabase_client()

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
                "error": str(getattr(latest_response, "error", None)),
            }))
            return False

        latest_rows = getattr(latest_response, "data", None) or []
        previous_hash = (
            latest_rows[0].get("record_hash")
            if latest_rows and latest_rows[0].get("record_hash")
            else "GENESIS"
        )

        payload: Dict[str, Any] = {
            "trace_id": trace_id,
            "actor_uid": actor_uid,
            "event": event_type,
            "target_id": target_id,
            "status": status,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }

        new_hash = _calculate_hash(payload, previous_hash)

        insert_response = (
            supabase.table("system_audit")
            .insert(
                {
                    "trace_id": str(trace_id),
                    "actor_uid": actor_uid,
                    "event_type": event_type,
                    "target_resource_id": target_id,
                    "status": status,
                    "payload": payload,
                    "previous_hash": previous_hash,
                    "record_hash": new_hash,
                }
            )
            .execute()
        )

        if getattr(insert_response, "error", None):
            logger.critical(json.dumps({
                "event": "audit_log_write_failed",
                "reason": "insert_rejected",
                "trace_id": trace_id,
                "actor_uid": actor_uid,
                "event_type": event_type,
                "target_id": target_id,
                "error": str(getattr(insert_response, "error", None)),
            }))
            return False

        return True

    except Exception as e:
        logger.critical(json.dumps({
            "event": "audit_log_write_failed",
            "reason": "exception",
            "trace_id": trace_id,
            "actor_uid": actor_uid,
            "event_type": event_type,
            "target_id": target_id,
            "exception": str(e),
        }))
        return False


async def append_audit_log_or_503(
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
) -> None:
    """Same as append_audit_log, but raises HTTPException(503) instead of
    returning False on failure.

    append_audit_log() itself is intentionally left returning bool rather
    than raising, so its existing call sites (and the tests asserting
    they degrade gracefully on a logging failure) are untouched. This is
    an additive, opt-in wrapper for routes where "the audit write failed"
    must abort the request rather than let it continue unaudited -- e.g.
    biometric enrollment, where the action being taken (deciding which
    physical device a patient identity trusts) is too sensitive to leave
    unaudited even once.
    """
    success = await append_audit_log(
        actor_uid=actor_uid,
        event_type=event_type,
        target_id=target_id,
        status=status,
    )
    if not success:
        raise HTTPException(
            status_code=503,
            detail="Audit ledger write failed; request aborted to avoid an unaudited action.",
        )