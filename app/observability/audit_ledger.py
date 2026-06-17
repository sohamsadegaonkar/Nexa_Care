"""Tamper-evident Cryptographic Audit Ledger."""
import logging
from fastapi.concurrency import run_in_threadpool
from app.core.supabase import get_supabase_client

logger = logging.getLogger(__name__)

async def append_audit_log(actor_uid: str, event_type: str, target_id: str, status: str) -> dict | None:
    """
    Appends a cryptographically linked log to the immutable ledger.
    Uses an atomic Postgres RPC to prevent hash-chain forks during high concurrency.
    """
    try:
        supabase = get_supabase_client()
        
        payload = {
            "p_actor_uid": actor_uid,
            "p_event_type": event_type,
            "p_target_id": target_id,
            "p_status": status
        }
        
        # [FINDING #4 FIX]: Run the blocking synchronous DB call in a worker thread 
        # so it doesn't freeze the FastAPI event loop.
        response = await run_in_threadpool(
            lambda: supabase.rpc("append_audit_log_atomic", payload).execute()
        )
        
        return getattr(response, "data", None)

    except Exception as e:
        # Fallback to standard logging if the cryptographic ledger fails
        logger.critical(f"AUDIT LEDGER FAILURE: {str(e)} | Payload: {payload}")
        return None