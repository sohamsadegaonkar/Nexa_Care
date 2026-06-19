"""Biometric registry: verifies that an incoming (nfc_uid, bio_seed) pair
was genuinely enrolled for the patient being claimed at handshake time.

Closes the gap where the handshake previously trusted the client's word
on `masked_internal_id` with no check against anything. The verifier
stored in `biometric_registry` is a one-way HMAC keyed by a server-side
pepper (HANDSHAKE_PEPPER_SECRET) -- never derivable from nfc_uid or
bio_seed alone, and never reversible back to either. Raw biometric data
is never written to the database, before or after this module exists.

Fixes applied in this file
--------------------------
F-15 — enroll_biometric_binding() and verify_biometric_binding() previously
        swallowed every exception with a bare `except: return False/True`.
        The actual PostgREST error (e.g. "relation does not exist", RLS
        violation, constraint conflict) was therefore invisible in logs,
        making smoke-test 502s impossible to diagnose without live DB access.

        Both functions now log the exception at CRITICAL / ERROR level
        (matching the audit_ledger pattern) before returning the fail-safe
        value.  The fail-safe behaviour is unchanged — callers still receive
        False on any error — but ops now have the actionable detail in the
        structured log stream.

F-16 — supabase-py 2.x (postgrest-py ≥ 0.11) raises an APIError on any
        HTTP 4xx/5xx response from PostgREST rather than returning a result
        object with a truthy `.error` attribute.  The old
        `getattr(response, "error", None)` checks therefore always evaluated
        to None (falsy) on the success path and were dead code on the error
        path (because exceptions were caught before reaching them).

        The checks are replaced with explicit post-execute success
        assertions.  For `enroll_biometric_binding`, a successful execute()
        is the only non-exceptional path, so reaching the return statement
        is sufficient.  For `verify_biometric_binding`, the response data is
        validated directly instead of checking a phantom .error attribute.

        NOTE: .single() in postgrest-py raises when 0 rows are returned.
        That exception is caught by the outer try/except, logged at DEBUG
        (not enrolled is a normal operational state, not an error), and
        returns False as before.

TIMING SIDE-CHANNEL FIX (this revision) — verify_biometric_binding() was
        previously NOT constant-time end-to-end: a masked_internal_id with
        no enrolled row returned after only a DB round-trip, while one with
        an enrolled-but-mismatched binding additionally paid for an HMAC
        compute and a compare_digest call. That gap was a real, formally
        accepted residual timing side-channel, with the planned mitigation
        (infrastructure-level rate limiting) deferred to "the next release"
        indefinitely.

        verify_biometric_binding() is now a thin wrapper that runs the
        actual lookup (_verify_biometric_binding_impl) and pads the total
        wall-clock time up to _MIN_VERIFY_DURATION_SECONDS before
        returning, regardless of which internal path was taken. This closes
        the side-channel at the code level immediately, as defense in depth
        ALONGSIDE (not instead of) the infrastructure-level rate limiting
        that's still worth doing separately -- the floor here defends
        against an attacker distinguishing the fast "not enrolled" path
        from the fast "enrolled, mismatched" path; it does not, and cannot,
        defend against distinguishing either fast path from a genuinely
        slow DB outage, which is what rate limiting is for.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time

from fastapi import HTTPException

from app.core.config import get_handshake_config
from app.core.supabase import get_supabase_client
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")

# Floor on verify_biometric_binding()'s total wall-clock time. Chosen to
# comfortably exceed the slowest of the two internal paths (DB round-trip
# + HMAC compute + compare_digest) under normal conditions while staying
# small enough not to meaningfully slow down the handshake endpoint or
# the test suite. Tune upward if profiling shows the "enrolled,
# mismatched" path routinely exceeds this on your infrastructure.
_MIN_VERIFY_DURATION_SECONDS = 0.05


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

    TIMING SIDE-CHANNEL FIX: wraps _verify_biometric_binding_impl() in a
    fixed-time response budget. See module docstring.
    """
    start = time.monotonic()
    result = await _verify_biometric_binding_impl(nfc_uid, bio_seed, masked_internal_id)
    elapsed = time.monotonic() - start
    remaining = _MIN_VERIFY_DURATION_SECONDS - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)
    return result


async def _verify_biometric_binding_impl(
    nfc_uid: str, bio_seed: str, masked_internal_id: str
) -> bool:
    """The actual lookup, unchanged in behavior from the prior revision --
    only the timing characteristics of the public verify_biometric_binding()
    wrapper above have changed."""
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("biometric_registry")
            .select("bio_verifier_hash,revoked_at")
            .eq("masked_internal_id", masked_internal_id)
            .single()           # raises APIError if 0 rows (not enrolled)
            .execute()
        )
    except Exception as exc:
        # F-15: log the real exception before returning the fail-safe False.
        #
        # Distinguish operational "not found" (expected on first enrollment
        # attempt or after revocation) from genuine infrastructure failures
        # (table missing, RLS violation, network partition) so that:
        #   - Normal "not enrolled" cases log at DEBUG and don't page on-call.
        #   - Real DB failures log at CRITICAL and are immediately actionable.
        #
        # postgrest-py raises APIError with a message that contains the
        # PostgREST error code (e.g. "PGRST116" for "exactly one row required"
        # when .single() finds 0 rows).  Any other exception is unexpected.
        exc_str = str(exc)
        if "PGRST116" in exc_str or "JSON object requested, multiple (or no) rows returned" in exc_str:
            # 0 rows from .single() — patient simply has no enrolled binding.
            # This is a normal operational state, not an infrastructure error.
            logger.debug(json.dumps({
                "event": "biometric_verify_not_enrolled",
                "masked_internal_id": masked_internal_id,
                "detail": "No binding row found for this patient.",
            }))
        else:
            # Unexpected: table missing, RLS rejection, network error, etc.
            # Log at CRITICAL so it appears in alerting thresholds.
            logger.critical(json.dumps({
                "event": "biometric_verify_db_error",
                "masked_internal_id": masked_internal_id,
                "exception": exc_str,
                "action": "returning_false_fail_closed",
            }))
        return False

    # F-16: in supabase-py 2.x, execute() does not set a .error attribute;
    # failures raise APIError (caught above).  Validate the response data
    # directly rather than checking a phantom .error attribute.
    row = getattr(response, "data", None)
    if not row:
        # .single() returned successfully but with no data — treat as not enrolled.
        logger.debug(json.dumps({
            "event": "biometric_verify_empty_row",
            "masked_internal_id": masked_internal_id,
        }))
        return False

    if row.get("revoked_at"):
        # Binding exists but has been administratively revoked.
        # Do NOT log the masked_internal_id at a high severity here —
        # revocation is an expected operational state, not a security alert.
        logger.warning(json.dumps({
            "event": "biometric_verify_revoked",
            "masked_internal_id": masked_internal_id,
            "revoked_at": str(row["revoked_at"]),
        }))
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
    is the one action that decides which physical card/biometric the
    patient identity trusts.
    """
    try:
        supabase = get_supabase_client()
        verifier = compute_bio_verifier(nfc_uid, bio_seed)

        response = supabase.table("biometric_registry").insert(
            {
                "masked_internal_id": masked_internal_id,
                "bio_verifier_hash": verifier,
            }
        ).execute()

        # F-16: in supabase-py 2.x, execute() raises APIError on any PostgREST
        # error (4xx/5xx) before this line is reached, so the check below is a
        # no-op in production.  It is retained for two reasons:
        #   1. Test compatibility: the test suite mocks execute() with a
        #      FakeResult that carries a truthy .error attribute to simulate a
        #      DB rejection (supabase-py 1.x style).  Re-raising here causes
        #      the mock error to flow through the except branch, which logs and
        #      returns False — matching what real 2.x APIError raises would do.
        #   2. Belt-and-suspenders: if a future supabase-py version changes
        #      error-surfacing behavior, this check catches it.
        error = getattr(response, "error", None)
        if error:
            raise RuntimeError(f"PostgREST insert error: {error}")

        return True

    except Exception as exc:
        # F-15: log the real PostgREST error before returning False.
        #
        # The most common failure modes and their signatures:
        #   "relation biometric_registry does not exist"
        #       -> Migration 0002 has not been applied to this Supabase instance.
        #          Apply migrations/0002_biometric_registry_schema.sql and retry.
        #   "duplicate key value violates unique constraint uq_biometric_registry_patient"
        #       -> This masked_internal_id is already enrolled.
        #          Use the revocation endpoint before re-enrolling.
        #   "new row violates row-level security policy"
        #       -> The backend is not authenticating as the service role.
        #          Check that SUPABASE_KEY is the service-role key, not the anon key.
        #
        # All three are CRITICAL because they represent either a deployment
        # gap (missing migration) or a misconfiguration that will block
        # every enrollment until resolved.
        logger.critical(json.dumps({
            "event": "biometric_enroll_db_error",
            "masked_internal_id": masked_internal_id,
            "exception": str(exc),
            "action": "returning_false",
            "hint": (
                "If exception contains 'does not exist': apply "
                "migrations/0002_biometric_registry_schema.sql. "
                "If 'duplicate key': patient already enrolled, revoke first. "
                "If 'row-level security': verify SUPABASE_KEY is the service-role key."
            ),
        }))
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