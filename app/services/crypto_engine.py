import hmac
import hashlib
import secrets
import json
import uuid

from app.core.redis import get_redis_client
from app.core.request_context import trace_id_var
from app.observability.audit_ledger import append_audit_log


async def process_biometric_handshake(
    nfc_uid: str, bio_seed: str, masked_internal_id: str
) -> dict | None:
    try:
        await append_audit_log(
            actor_uid="HANDSHAKE_PROV",
            event_type="BIOMETRIC_HANDSHAKE_STARTED",
            target_id=nfc_uid,
            status="PROCESSING",
        )

        if not isinstance(nfc_uid, str) or not nfc_uid.strip() or not isinstance(bio_seed, str) or not bio_seed.strip():
            await append_audit_log(
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_DENIED",
                target_id=nfc_uid if isinstance(nfc_uid, str) and nfc_uid.strip() else "INVALID_NFC_SIGNAL",
                status="REJECTED_BAD_SIGNAL",
            )
            return None

        # SCOPING FIX: a handshake session must be bound to exactly one
        # patient record. Without this, any session minted via this endpoint
        # could later be replayed against /api/v1/record/{any_id}. We
        # validate + normalize the id now so the session can be checked
        # against it at retrieval time.
        try:
            scoped_patient_id = str(uuid.UUID(str(masked_internal_id)))
        except (ValueError, AttributeError, TypeError):
            await append_audit_log(
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_DENIED",
                target_id=nfc_uid,
                status="REJECTED_INVALID_PATIENT_SCOPE",
            )
            return None

        derived_alpha = hmac.new(
            key=nfc_uid.encode("utf-8"),
            msg=bio_seed.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()

        token = secrets.token_urlsafe(32)
        redis = get_redis_client()

        session_state = {
            "authenticated": True,
            "trace_id": trace_id_var.get(),
            "nfc_uid": nfc_uid,
            "masked_internal_id": scoped_patient_id,
            "derived_alpha": derived_alpha.hex()[:16],
        }

        setex_result = redis.setex(token, 1800, json.dumps(session_state, separators=(",", ":")))
        if hasattr(setex_result, "__await__"):
            await setex_result

        await append_audit_log(
            actor_uid="HANDSHAKE_PROV",
            event_type="BIOMETRIC_HANDSHAKE_SUCCESS",
            target_id=scoped_patient_id,
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