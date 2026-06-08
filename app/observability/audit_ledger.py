import hashlib
import json
import datetime
from typing import Dict, Any

from app.core.supabase import get_supabase_client
from app.core.request_context import trace_id_var


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
            return False

        latest_rows = getattr(latest_response, "data", None) or []
        previous_hash = (
            latest_rows[0].get("record_hash")
            if latest_rows and latest_rows[0].get("record_hash")
            else "GENESIS"
        )

        trace_id = trace_id_var.get()
        payload: Dict[str, Any] = {
            "trace_id": trace_id,
            "actor_uid": actor_uid,
            "event": event_type,
            "target_id": target_id,
            "status": status,
            "timestamp": datetime.datetime.utcnow().isoformat(),
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
            return False

        return True

    except Exception:
        return False