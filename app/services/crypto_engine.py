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

PII-IN-LEDGER FIX (this revision) — every append_audit_log() call in this
          module previously used target_id=nfc_uid before the patient
          scope was resolved (BIOMETRIC_HANDSHAKE_STARTED, the
          REJECTED_BAD_SIGNAL and REJECTED_INVALID_PATIENT_SCOPE denial
          paths, and the outer CRITICAL_FAILURE handler). nfc_uid is a
          biometric *device* identifier and personal data under India's
          DPDP Act 2023 -- migration 0003 dropped it from
          biometric_registry for exactly that reason. Writing it into
          the application audit ledger instead is worse, not better: it is
          hash-chained (migration 0001) and rows can never be deleted
          without breaking tamper-evidence for everything chained after
          them, so there was no remediation path short of breaking the
          chain.

          Every such call now uses _audit_safe_device_ref(nfc_uid), a
          one-way SHA-256-derived reference that lets ops correlate
          repeated failures from the same device without ever persisting
          the raw identifier anywhere -- not in Redis (session_state no
          longer includes nfc_uid either) and not in the audit ledger.

AUDIT-FAILURE-HANDLING FIX (this revision) — BIOMETRIC_HANDSHAKE_SUCCESS
          previously used the fire-and-forget append_audit_log(), whose
          boolean return value was never checked: a session token was
          minted, stored in Redis, and returned to the caller regardless
          of whether the success audit entry actually got written. That
          violated the "no silent audit failures" rule enforced
          elsewhere (e.g. app/services/biometric_registry.py's
          enroll_biometric_binding_with_audit).

          The success path now calls append_audit_log_or_503(). If that
          raises (audit write failed), the just-minted Redis session is
          deleted before the HTTPException(503) propagates, so a session
          that can't be proven in the ledger never remains valid. The
          503 is re-raised as-is rather than being swallowed by this
          function's own outer except-and-return-None handler -- see the
          dedicated `except HTTPException: raise` clause below.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import hashlib
import hmac
import json
import secrets
import uuid

from fastapi import HTTPException

from app.core.config import (
    get_handshake_config,
)  # F-07 fix: was get_handshake_security_config
from app.core.redis import get_redis_client
from app.core.request_context import trace_id_var
from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503
from app.services.biometric_registry import (
    verify_biometric_binding,
)  # F-03 fix: import added

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
    return hmac.new(
        key=pepper, msg=nfc_uid.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()


def _audit_safe_device_ref(nfc_uid: str) -> str:
    """One-way, non-reversible reference for an nfc_uid, safe to persist
    in the audit ledger.

    Deliberately NOT keyed by HANDSHAKE_PEPPER_SECRET -- that key is
    reserved for the verification material in biometric_registry
    (compute_bio_verifier), and reusing it here would mean a leak of this
    "harmless bookkeeping" reference and a leak of the registry table
    together could be cross-checked against each other. This is a plain,
    unkeyed SHA-256 over a fixed, namespaced prefix plus the raw nfc_uid
    -- one-way and good enough for "is this the same device as last
    time", which is all an audit reference needs to support.
    """
    if not isinstance(nfc_uid, str) or not nfc_uid:
        return "UNKNOWN_DEVICE"
    digest = hashlib.sha256(f"device-audit-ref:{nfc_uid}".encode("utf-8")).hexdigest()
    return f"devref:{digest[:16]}"


async def process_biometric_handshake(
    nfc_uid: str, bio_seed: str, masked_internal_id: str
) -> dict | None:
    device_ref = _audit_safe_device_ref(nfc_uid)

    try:
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="HANDSHAKE_PROV",
            event_type="BIOMETRIC_HANDSHAKE_STARTED",
            target_id=device_ref,
            status="PROCESSING",
        )

        # ── Input validation ──────────────────────────────────────────────
        if (
            not isinstance(nfc_uid, str)
            or not nfc_uid.strip()
            or not isinstance(bio_seed, str)
            or not bio_seed.strip()
        ):
            await append_audit_log(
                audit_context=current_audit_context(AuditDomain.AUTH),
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_DENIED",
                target_id=device_ref,
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
                audit_context=current_audit_context(AuditDomain.AUTH),
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_DENIED",
                target_id=device_ref,
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
                audit_context=current_audit_context(AuditDomain.AUTH),
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

        # nfc_uid is intentionally NOT included here. The session only
        # needs to prove "this patient was authenticated", not which
        # physical device did it -- keeping nfc_uid out of Redis too
        # limits its blast radius to this single in-memory function call.
        session_state = {
            "authenticated": True,
            "trace_id": trace_id_var.get(),
            "masked_internal_id": scoped_patient_id,
            "derived_alpha": derived_alpha.hex()[:16],
        }

        setex_result = redis.setex(
            token, 1800, json.dumps(session_state, separators=(",", ":"))
        )
        if hasattr(setex_result, "__await__"):
            await setex_result

        # AUDIT-FAILURE-HANDLING FIX: hard-fail instead of fire-and-forget.
        # If the success entry can't be written, the session we just
        # minted must not be allowed to remain valid -- revoke it, then
        # let the 503 propagate untouched (see the `except HTTPException:
        # raise` clause below, which exists specifically so this isn't
        # swallowed into a generic 401).
        try:
            await append_audit_log_or_503(
                audit_context=current_audit_context(AuditDomain.AUTH),
                actor_uid="HANDSHAKE_PROV",
                event_type="BIOMETRIC_HANDSHAKE_SUCCESS",
                target_id=scoped_patient_id,
                status="SUCCESS",
            )
        except HTTPException:
            delete_result = redis.delete(token)
            if hasattr(delete_result, "__await__"):
                await delete_result
            raise

        return {
            "session_token": token,
            "expires_in_secs": 1800,
            "status": "authorized",
        }

    except HTTPException:
        # A hard-fail audit-write failure (503) must propagate as-is. It
        # must NOT be caught by the broad `except Exception` below and
        # turned into a silent `return None` (which the route would then
        # report as a generic 401) -- the whole point of hard-failing is
        # to surface "we couldn't audit this" distinctly from "biometric
        # verification failed."
        raise
    except Exception:
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="HANDSHAKE_PROV",
            event_type="BIOMETRIC_HANDSHAKE_FAILED",
            target_id=device_ref,
            status="CRITICAL_FAILURE",
        )
        return None
