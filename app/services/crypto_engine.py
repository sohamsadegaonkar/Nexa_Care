import hashlib
import hmac
import json
import secrets
import uuid

from app.core.config import get_handshake_security_config
from app.core.redis import get_redis_client
from app.core.request_context import trace_id_var
from app.observability.audit_ledger import append_audit_log

# PBKDF2 iteration count for the per-record-salted derivation below.
# Carried over from the (now-deprecated) app/core/handshake.py reference
# implementation. Revisit against current OWASP guidance if this needs to
# move higher -- it's a straight latency/brute-force-resistance tradeoff.
_PBKDF2_ITERATIONS = 100_000


def _derive_record_salt(nfc_uid: str) -> bytes:
    """Per-record salt: unique to this nfc_uid, but not reproducible
    without the server-side pepper (HANDSHAKE_PEPPER_SECRET).

    Replaces the single hardcoded salt that used to be shared by every
    record (app/core/handshake.py's _STATIC_SALT) -- a precomputed table
    built against that one salt could previously attack every record at
    once. Keying off nfc_uid here means every distinct record gets its own
    salt with no enrollment-time storage required.
    """
    pepper = get_handshake_security_config().pepper.encode("utf-8")
    return hmac.new(pepper, nfc_uid.encode("utf-8"), hashlib.sha256).digest()


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

        # SCOPING (unchanged from the previous fix): a handshake session is
        # only ever valid for the ONE patient record it was minted for.
        # Without this, any session obtained here could be replayed against
        # /api/v1/record/{any_id}.
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

        # Per-record-salted, key-stretched derivation (PBKDF2-HMAC-SHA256).
        # Replaces the previous single-round HMAC, which also used the
        # low-entropy nfc_uid as the HMAC *key* and the secret bio_seed as
        # the *message* -- backwards from how a secret should be treated.
        # Here the secret (bio_seed) is the value being stretched, and the
        # salt is derived from the record identifier + server pepper.
        record_salt = _derive_record_salt(nfc_uid)
        derived_alpha = hashlib.pbkdf2_hmac(
            "sha256",
            bio_seed.encode("utf-8"),
            record_salt,
            _PBKDF2_ITERATIONS,
            dklen=32,
        )

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