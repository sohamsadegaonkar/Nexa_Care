"""Biometric registry: verifies that an incoming (nfc_uid, bio_seed) pair
was genuinely enrolled for the patient being claimed at handshake time.

Closes the gap where the handshake previously trusted the client's word
on `masked_internal_id` with no check against anything. The verifier
stored in `biometric_registry` is a one-way HMAC keyed by a server-side
pepper (HANDSHAKE_PEPPER_SECRET) -- never derivable from nfc_uid or
bio_seed alone, and never reversible back to either. Raw biometric data
is never written to the database, before or after this module exists.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException

from app.core.config import get_handshake_config
from app.core.supabase import get_supabase_client
from app.observability.audit_ledger import append_audit_log_or_503


def compute_bio_verifier(nfc_uid: str, bio_seed: str) -> str:
    """One-way verifier. Keyed by the server-side pepper, not by nfc_uid
    or bio_seed themselves -- so a leak of the registry table plus a leak
    of nfc_uid still isn't enough to forge or reverse a verifier."""
    pepper = get_handshake_config().pepper_secret
    message = f"{nfc_uid}:{bio_seed}".encode("utf-8")
    return hmac.new(key=pepper.encode("utf-8"), msg=message, digestmod=hashlib.sha256).hexdigest()


async def verify_biometric_binding(nfc_uid: str, bio_seed: str, masked_internal_id: str) -> bool:
    """True only if (nfc_uid, bio_seed) matches the verifier enrolled for
    this exact masked_internal_id, and that binding hasn't been revoked.
    Fails closed on any DB error, missing row, or revoked binding.

    ACCEPTED RISK (v0.1.0): this lookup is not constant-time end-to-end.
    A masked_internal_id with no enrolled row returns after only a DB
    round-trip; one with an enrolled-but-mismatched binding additionally
    pays for an HMAC computation and a compare_digest call. This is a
    real, formally accepted residual timing side-channel for this
    release -- it is deliberately NOT being closed with a fixed-time
    response budget here. Planned mitigation is infrastructure-level rate
    limiting in the next release, not a code-level fix in this function.
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("biometric_registry")
            .select("bio_verifier_hash,revoked_at")
            .eq("masked_internal_id", masked_internal_id)
            .single()
            .execute()
        )
    except Exception:
        return False

    if getattr(response, "error", None):
        return False

    row = getattr(response, "data", None)
    if not row:
        return False

    if row.get("revoked_at"):
        return False

    expected = row.get("bio_verifier_hash") or ""
    candidate = compute_bio_verifier(nfc_uid, bio_seed)

    # Constant-time comparison -- never `==` for secrets/verifiers.
    return hmac.compare_digest(candidate, expected)


async def enroll_biometric_binding(nfc_uid: str, bio_seed: str, masked_internal_id: str) -> bool:
    """One-time enrollment: binds (nfc_uid, bio_seed) to masked_internal_id.

    Low-level DB write only -- no audit logging, no access control. Wired
    in via enroll_biometric_binding_with_audit below, which is what the
    /api/v1/enroll-biometric route actually calls. That route is gated
    behind verify_provider_token (app/core/dependencies.py), since this
    is the one action that decides which physical card/biometric a
    patient identity trusts.
    """
    try:
        supabase = get_supabase_client()
        verifier = compute_bio_verifier(nfc_uid, bio_seed)
        response = (
            supabase.table("biometric_registry")
            .insert({"masked_internal_id": masked_internal_id, "bio_verifier_hash": verifier})
            .execute()
        )
        return not getattr(response, "error", None)
    except Exception:
        return False


async def enroll_biometric_binding_with_audit(nfc_uid: str, bio_seed: str, masked_internal_id: str) -> bool:
    """Orchestrates enrollment with a full attempt/success/failure audit
    trail, hard-failing the request (HTTPException 503) if the audit
    ledger itself cannot be written to at any stage -- an enrollment that
    can't be audited is not allowed to silently succeed.

    Pulled out of the route handler so the route stays a thin HTTP
    adapter and this orchestration logic can be unit-tested without a
    running FastAPI app.

    Raises HTTPException(503) if any audit write fails.
    Raises HTTPException(502) if the registry write itself fails (e.g.
    already enrolled -- the table has a UNIQUE constraint on
    masked_internal_id -- or a transient DB error).
    Returns True on a fully audited, successful enrollment.
    """
    await append_audit_log_or_503(
        actor_uid="PROVIDER_FACILITY",
        event_type="BIOMETRIC_ENROLLMENT_ATTEMPT",
        target_id=masked_internal_id,
        status="STARTED",
    )

    enrolled = await enroll_biometric_binding(
        nfc_uid=nfc_uid,
        bio_seed=bio_seed,
        masked_internal_id=masked_internal_id,
    )

    if not enrolled:
        await append_audit_log_or_503(
            actor_uid="PROVIDER_FACILITY",
            event_type="BIOMETRIC_ENROLLMENT_FAILED",
            target_id=masked_internal_id,
            status="DB_WRITE_FAILED",
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to write biometric binding (it may already be enrolled, "
                   "or the registry write was rejected).",
        )

    await append_audit_log_or_503(
        actor_uid="PROVIDER_FACILITY",
        event_type="BIOMETRIC_ENROLLMENT_SUCCESS",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    return True