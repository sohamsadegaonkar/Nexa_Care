"""FastAPI dependencies for access control.

Two distinct trust models live in this module — do not conflate them:

- get_scoped_session(): PATIENT-level trust. Resolves masked_internal_id
  from a biometric handshake session bound at handshake time (see
  crypto_engine.py). Proves "this caller is currently authenticated AS
  this specific patient." Never accepts the id from a URL, query string,
  or body — that was the IDOR this dependency exists to close.

- get_provider_context(): PROVIDER-level trust. Authenticates an
  individual clinician via ``provider_credential`` (password hash or
  Redis-backed session token) and resolves their active
  ``provider_hospital_affiliation`` so every gated route knows which
  facility context the provider is operating under. Pass ``X-Hospital-Id``
  when the provider holds multiple active affiliations.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.services.auth_service import validate_session_context
from app.services.consent_engine import ConsentEngineUnavailable, validate as validate_consent_capability
from app.services.provider_auth_service import (
    ProviderAuthFailure,
    authenticate_provider_password,
    authenticate_provider_session,
)

logger = logging.getLogger("nexa_logger")


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
        await append_audit_log(
            actor_uid="SESSION_GUARD",
            event_type="SESSION_VALIDATION_FAILED",
            target_id="UNKNOWN",
            status="UNSCOPED_SESSION",
        )
        raise HTTPException(status_code=401, detail="Session is not scoped to a patient")

    return masked_internal_id


_provider_bearer_scheme = HTTPBearer(auto_error=False)
_provider_basic_scheme = HTTPBasic(auto_error=False)


def _audit_status_for_failure(failure: ProviderAuthFailure) -> str:
    return failure.value.upper()


def _http_exception_for_failure(failure: ProviderAuthFailure) -> HTTPException:
    if failure in {
        ProviderAuthFailure.AFFILIATION_REQUIRED,
        ProviderAuthFailure.AFFILIATION_NOT_FOUND,
    }:
        if failure is ProviderAuthFailure.AFFILIATION_REQUIRED:
            detail = "Hospital context required — supply X-Hospital-Id header"
        else:
            detail = "No active affiliation for the requested hospital context"
        return HTTPException(status_code=400, detail=detail)

    if failure is ProviderAuthFailure.MFA_REQUIRED:
        # MFA-DISABLED-EXPLICITLY (2026-07-03): mfa_enabled=True is a real
        # provider_credential state with no completion path anywhere in
        # this codebase (no /mfa/verify route exists). The old 403 here
        # was indistinguishable from an ordinary permission denial, which
        # silently and permanently locked out any provider whose account
        # had MFA turned on. 501 says plainly: this is a server gap, not
        # something the caller can fix by retrying or re-authenticating.
        return HTTPException(
            status_code=501,
            detail=(
                "This provider account has multi-factor authentication "
                "enabled, but MFA verification is not yet implemented. "
                "Login cannot proceed. Contact an administrator to "
                "disable MFA on this account."
            ),
        )

    return HTTPException(status_code=401, detail="Invalid provider credentials")


async def get_provider_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_provider_bearer_scheme),
    basic_credentials: HTTPBasicCredentials | None = Depends(_provider_basic_scheme),
    hospital_id: UUID | None = Header(default=None, alias="X-Hospital-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderContext:
    """Authenticate a provider and resolve their hospital operating context.

    Supports two credential transports:

    1. ``Authorization: Bearer <session_token>`` — opaque token issued after
       password login (stored in Redis by ``issue_provider_session_token``).
    2. ``Authorization: Basic <base64(login:password)>`` — direct credential
       verification against ``provider_credential.password_hash``.

    When a provider has multiple active affiliations, callers must supply
    ``X-Hospital-Id`` so the dependency can select the correct facility.
    """

    if credentials is not None and credentials.credentials:
        result = await authenticate_provider_session(
            db,
            credentials.credentials,
            hospital_id,
        )
    elif basic_credentials is not None and basic_credentials.username:
        result = await authenticate_provider_password(
            db,
            basic_credentials.username,
            basic_credentials.password,
            hospital_id,
        )
    else:
        await append_audit_log(
            actor_uid="PROVIDER_GUARD",
            event_type="PROVIDER_AUTH_FAILED",
            target_id=str(hospital_id) if hospital_id else "UNKNOWN",
            status="MISSING_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Missing provider credentials")

    if result.context is not None:
        return result.context

    assert result.failure is not None
    await append_audit_log(
        actor_uid="PROVIDER_GUARD",
        event_type="PROVIDER_AUTH_FAILED",
        target_id=str(hospital_id) if hospital_id else "UNKNOWN",
        status=_audit_status_for_failure(result.failure),
    )
    if result.failure is ProviderAuthFailure.MFA_REQUIRED:
        # Not routine auth-failure noise -- every occurrence is a provider
        # who cannot get past login at all until an admin disables MFA on
        # their account. See MFA-DISABLED-EXPLICITLY note above.
        logger.critical(json.dumps({
            "event": "provider_auth_blocked_mfa_not_implemented",
            "hospital_id": str(hospital_id) if hospital_id else "UNKNOWN",
        }))
    raise _http_exception_for_failure(result.failure)


async def get_current_provider(
    credentials: HTTPAuthorizationCredentials | None = Depends(_provider_bearer_scheme),
    hospital_id: UUID | None = Header(default=None, alias="X-Hospital-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderContext:
    """Authenticate a provider using only ``Authorization: Bearer``.

    This is the Phase A provider-centric dependency for API-key/session-token
    protected routes. The bearer token is resolved to a provider, then the
    provider credential row is checked for active and lockout state by the auth
    service before a ``ProviderContext`` is returned.
    """

    if credentials is None or not credentials.credentials:
        await append_audit_log(
            actor_uid="PROVIDER_GUARD",
            event_type="PROVIDER_AUTH_FAILED",
            target_id=str(hospital_id) if hospital_id else "UNKNOWN",
            status="MISSING_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Missing provider credentials")

    result = await authenticate_provider_session(db, credentials.credentials, hospital_id)
    if result.context is not None:
        return result.context

    assert result.failure is not None
    await append_audit_log(
        actor_uid="PROVIDER_GUARD",
        event_type="PROVIDER_AUTH_FAILED",
        target_id=str(hospital_id) if hospital_id else "UNKNOWN",
        status=_audit_status_for_failure(result.failure),
    )
    raise _http_exception_for_failure(result.failure)

async def require_active_consent(
    request: Request,
    provider: ProviderContext = Depends(get_provider_context),
) -> ProviderContext:
    """Require a live provider-bound consent capability for a patient path.

    The dependency reads ``X-Consent-Token`` and the ``patient_id`` path
    parameter. It fails closed on missing values, consent-store errors,
    expired tokens, and patient/provider mismatches.

    Phase 1 migration (docs/CURRENT-STATE.md, Section 1): this used to
    validate against app/services/consent_service.py, which never checked
    a ``purpose`` at all. It now validates through ConsentEngine, which
    always binds a purpose. Callers of this dependency (currently
    fhir_routes.py) don't carry an ``X-Consent-Purpose`` header the way
    app/api/v2/patient_routes.py does, so this checks against the same
    ``"routine_access"`` default that consent_routes.py's ``/grant``
    endpoint uses when the caller doesn't specify one. A token granted for
    a different, more specific purpose (e.g. patient_routes.py's
    ``treatment``) will correctly NOT authorize FHIR export under this
    dependency -- that's purpose-scoping working as intended, not a bug.
    """

    consent_token = request.headers.get("X-Consent-Token")
    patient_id = request.path_params.get("patient_id")

    if not consent_token or not patient_id:
        raise HTTPException(
            status_code=403,
            detail="Active consent token required or expired.",
        )

    try:
        capability = await validate_consent_capability(
            token=consent_token,
            patient_id=str(patient_id),
            clinician_id=provider.actor_uid,
            purpose="routine_access",
        )
    except ConsentEngineUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Consent service is temporarily unavailable.",
        ) from exc

    if capability is None:
        raise HTTPException(
            status_code=403,
            detail="Active consent token required or expired.",
        )

    return provider