"""FastAPI dependencies for access control.

Two distinct trust models live in this module -- do not conflate them:

- get_scoped_session(): PATIENT-level trust. Resolves masked_internal_id
  from a biometric handshake session bound at handshake time (see
  crypto_engine.py). Proves "this caller is currently authenticated AS
  this specific patient." Never accepts the id from a URL, query string,
  or body -- that was the IDOR this dependency exists to close.

- verify_provider_token(): FACILITY-level trust. Proves "this caller is a
  legitimate hospital/clinic system" via a shared bearer credential
  (CLINIC_API_KEY) compared in constant time. NOT tied to any individual
  patient -- a route gated only by this can act on any masked_internal_id
  supplied in its own request body. That's by design (a facility
  registers many patients), but it means every such route is a
  high-value target and must audit-log every call.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_clinic_config
from app.observability.audit_ledger import append_audit_log
from app.services.auth_service import validate_session_context


async def get_scoped_session(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        await append_audit_log(
            actor_uid="SESSION_GUARD",
            event_type="SESSION_VALIDATION_FAILED",
            target_id="UNKNOWN",
            status="MISSING_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Missing authorization token")

    session_context = await validate_session_context(authorization)
    if not session_context:
        await append_audit_log(
            actor_uid="SESSION_GUARD",
            event_type="SESSION_VALIDATION_FAILED",
            target_id="UNKNOWN",
            status="INVALID_OR_EXPIRED",
        )
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    masked_internal_id = session_context.get("masked_internal_id")
    if not masked_internal_id:
        # Catches sessions created before this fix shipped, or any future
        # handshake path that forgets to bind an id -- fail closed rather
        # than letting an unscoped session slip through.
        await append_audit_log(
            actor_uid="SESSION_GUARD",
            event_type="SESSION_VALIDATION_FAILED",
            target_id="UNKNOWN",
            status="UNSCOPED_SESSION",
        )
        raise HTTPException(status_code=401, detail="Session is not scoped to a patient")

    return masked_internal_id


_provider_bearer_scheme = HTTPBearer(auto_error=False)


async def verify_provider_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_provider_bearer_scheme),
) -> None:
    """Authenticates a clinic-facility caller against CLINIC_API_KEY.

    auto_error=False on the underlying HTTPBearer scheme so a missing
    header reaches this function as None (auditable, controlled 401)
    rather than FastAPI's own un-audited 403 short-circuit.

    Uses hmac.compare_digest rather than `==` so the comparison takes
    time independent of how many leading characters happen to match --
    `==` on strings short-circuits at the first mismatching byte, which
    leaks a usable timing signal to an attacker probing the key byte by
    byte. This is a single shared facility credential, not a per-user
    identity, so there is no individual actor to return -- callers should
    log their own audit entries using a fixed actor_uid such as
    "PROVIDER_FACILITY".
    """
    if credentials is None or not credentials.credentials:
        await append_audit_log(
            actor_uid="PROVIDER_GUARD",
            event_type="PROVIDER_AUTH_FAILED",
            target_id="UNKNOWN",
            status="MISSING_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Missing provider credentials")

    expected = get_clinic_config().api_key
    supplied = credentials.credentials

    if not hmac.compare_digest(supplied, expected):
        await append_audit_log(
            actor_uid="PROVIDER_GUARD",
            event_type="PROVIDER_AUTH_FAILED",
            target_id="UNKNOWN",
            status="INVALID_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Invalid provider credentials")