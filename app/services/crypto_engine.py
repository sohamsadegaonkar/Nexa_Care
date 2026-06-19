"""Biometric handshake: derives a per-record session token after verifying
that the supplied (nfc_uid, bio_seed) pair is enrolled for the claimed
patient.

Fixes applied:
  F-07 — corrected import from get_handshake_security_config (non-existent)
          to get_handshake_config(), and attribute from .pepper to
          .pepper_secret.
  F-03 — added verify_biometric_binding() check before _derive_record_salt()
          is allowed to run. A caller supplying an unenrolled or revoked
          (nfc_uid, bio_seed) pair now receives None (→ 400 at the route
          level) and an audit log entry with REJECTED_NO_ENROLLED_BINDING.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid

from app.core.config import get_handshake_config          # F-07 fix: was get_handshake_security_config
from app.core.redis import get_redis_client
from app.core.request_context import trace_id_var
from app.observability.audit_ledger import append_audit_log
from app.services.biometric_registry import verify_biometric_binding  # F-03 fix: import added

# PBKDF2 iteration count for the per-record-salted derivation below.
# Revisit against current OWASP guidance if this needs to move higher --
# it is a straight latency/brute-force-resistance tradeoff.
_PBKDF2_ITERATIONS = 100_000


def _derive_record_salt(nfc_uid: str) -> bytes:
    """Per-record salt: unique to this nfc_uid, not reproducible without
    the server-side pepper (HANDSHAKE_PEPPER_SECRET).

    Replaces the single hardcoded salt that was shared by every record
    (app/core/handshake.py's _STATIC_SALT).  Keying off nfc_uid means
    every distinct record gets its own salt with no enrollment-time
    storage required.
    """
    # F-07 fix: was get_handshake_security_config().pepper
    pepper = get_handshake_config().pepper_secret.encode("utf-8")
    # F-08 guidance: keyword args so key/msg order is unambiguous
    return hmac.new(key=pepper, msg=nfc_uid.encode("utf-8"), digestmod=hashlib.sha256).digest()


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

        # ── Input validation ──────────────────────────────────────────────
        if (
            not isinstance(nfc_uid, str) or not nfc_uid.strip()
            or not isinstance(bio_seed, str) or not bio_seed.strip()
        ):
            await append_audit_log(
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_DENIED",
                target_id=(
                    nfc_uid if isinstance(nfc_uid, str) and nfc_uid.strip()
                    else "INVALID_NFC_SIGNAL"
                ),
                status="REJECTED_BAD_SIGNAL",
            )
            return None

        # ── Patient scope validation ──────────────────────────────────────
        # A handshake session is only ever valid for the ONE patient record
        # it was minted for. Without this an unenrolled token could be
        # replayed against /api/v1/record/{any_id}.
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

        # ── F-03 FIX: Verify enrolled biometric binding ───────────────────
        # verify_biometric_binding() checks (nfc_uid, bio_seed) against the
        # peppered HMAC stored in biometric_registry for this exact
        # scoped_patient_id.  It fails closed on any DB error, missing row,
        # or revoked binding.  Without this check the entire enrollment flow
        # was bypassed — any caller could mint a valid session token by
        # supplying arbitrary values.
        binding_valid = await verify_biometric_binding(
            nfc_uid=nfc_uid,
            bio_seed=bio_seed,
            masked_internal_id=scoped_patient_id,
        )
        if not binding_valid:
            await append_audit_log(
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_DENIED",
                target_id=scoped_patient_id,
                status="REJECTED_NO_ENROLLED_BINDING",
            )
            return None

        # ── Per-record-salted, key-stretched derivation ───────────────────
        # bio_seed is the value being stretched; the salt is derived from
        # the record identifier + server pepper.
        record_salt = _derive_record_salt(nfc_uid)
        derived_alpha = hashlib.pbkdf2_hmac(
            "sha256",
            bio_seed.encode("utf-8"),
            record_salt,
            _PBKDF2_ITERATIONS,
            dklen=32,
        )

        # ── Mint session token ────────────────────────────────────────────
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
            target_id=(
                nfc_uid if isinstance(nfc_uid, str) and nfc_uid else "UNKNOWN_NFC_SIGNAL"
            ),
            status="CRITICAL_FAILURE",
        )
        return None