import hashlib
import json
import datetime
from typing import Dict, Any

from app.core.supabase import get_supabase_client
from app.core.request_context import trace_id_var

# How many times to retry the read-latest-hash -> insert sequence if we
# lose the race to another concurrent writer. See
# migrations/0001_audit_ledger_atomic_chain.sql -- a UNIQUE constraint on
# previous_hash means at most one writer can ever win a given slot;
# everyone else gets a 23505 and must re-read the new latest hash.
_MAX_CHAIN_RETRIES = 5


def _calculate_hash(payload: dict, previous_hash: str) -> str:
    minified_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    combined_buffer = minified_payload + previous_hash
    return hashlib.sha256(combined_buffer.encode("utf-8")).hexdigest()


def _is_chain_conflict(error: Any) -> bool:
    """True if `error` looks like the previous_hash UNIQUE constraint
    rejecting this insert because another writer claimed that slot first
    (Postgres error code 23505).

    Supabase client versions differ in whether a failed .execute() returns
    an object with an .error attribute/dict or raises an exception with a
    .code attribute -- this checks the shapes we know about. If your
    installed supabase-py version represents this differently, this is the
    function to adjust; everything else here is unaffected either way,
    since append_audit_log()'s outer except also retries on any exception.
    """
    code = getattr(error, "code", None)
    if code is None and isinstance(error, dict):
        code = error.get("code")
    if code == "23505":
        return True

    message = str(error).lower()
    return "previous_hash" in message and ("duplicate" in message or "unique" in message)


async def append_audit_log(
    actor_uid: str,
    event_type: str,
    target_id: str,
    status: str,
) -> bool:
    supabase = get_supabase_client()

    for _attempt in range(_MAX_CHAIN_RETRIES):
        try:
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
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
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

            error = getattr(insert_response, "error", None)
            if error is not None:
                if _is_chain_conflict(error):
                    # Someone else's insert claimed this previous_hash
                    # slot first. Re-read the now-updated latest hash and
                    # try again rather than failing or, worse, silently
                    # forking the chain.
                    continue
                return False

            return True

        except Exception as exc:
            if _is_chain_conflict(exc):
                continue
            return False

    return False