import hmac
import hashlib
import secrets
import json

from app.core.redis import get_redis_client
from app.core.request_context import trace_id_var
from app.observability.audit_ledger import append_audit_log


async def process_biometric_handshake(nfc_uid: str, bio_seed: str, masked_internal_id: str) -> dict | None:
    try:
        await append_audit_log(
            actor_uid="HANDSHAKE_PROV",
            event_type="BIOMETRIC_HANDSHAKE_STARTED",
            target_id=nfc_uid,
            status="PROCESSING",
        )

        if (
            not isinstance(nfc_uid, str) or not nfc_uid.strip()
            or not isinstance(bio_seed, str) or not bio_seed.strip()
            or not isinstance(masked_internal_id, str) or not masked_internal_id.strip()
        ):
            await append_audit_log(
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_DENIED",
                target_id=nfc_uid if isinstance(nfc_uid, str) and nfc_uid.strip() else "INVALID_NFC_SIGNAL",
                status="REJECTED_BAD_SIGNAL",
            )
            return None

# Key Stretching: PBKDF2 with 600,000 iterations to prevent brute-force attacks
        derived_alpha = hashlib.pbkdf2_hmac(
            hash_name="sha256",
            password=bio_seed.encode("utf-8"),
            salt=nfc_uid.encode("utf-8"),
            iterations=600_000
        )

        token = secrets.token_urlsafe(32)
        redis = get_redis_client()

        session_state = {
            "authenticated": True,
            "trace_id": trace_id_var.get(),
            "nfc_uid": nfc_uid,
            "masked_internal_id": masked_internal_id,
            "derived_alpha": derived_alpha.hex()[:16],
        }

        setex_result = redis.setex(token, 1800, json.dumps(session_state, separators=(",", ":")))
        if hasattr(setex_result, "__await__"):
            await setex_result

        await append_audit_log(
            actor_uid="HANDSHAKE_PROV",
            event_type="BIOMETRIC_HANDSHAKE_SUCCESS",
            target_id=nfc_uid,
            status="SUCCESS",
        )

        return {
            "session_token": token,
            "expires_in_secs": 1800,
            "status": "authorized",
        }

    except Exception:
        await append_audit_log(
            actor_uid="HANDSHAKE_PROV",
            event_type="BIOMETRIC_HANDSHAKE_FAILED",
            target_id=nfc_uid if isinstance(nfc_uid, str) and nfc_uid else "UNKNOWN_NFC_SIGNAL",
            status="CRITICAL_FAILURE",
        )
        return None