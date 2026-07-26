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

from app.security.audit_context import (
    AuditDomain,
    bind_trusted_audit_hospital,
    bind_trusted_audit_tenant,
    current_audit_context,
)

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
from app.core.security import hash_client_ip
from app.core.client_ip import resolve_client_ip
import hashlib
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.services.auth_service import validate_session_context
from app.services.patient_auth_service import decode_patient_access_token
from app.services.consent_engine import (
    ConsentEngineUnavailable,
    validate as validate_consent_capability,
)
from app.services.provider_auth_service import (
    ProviderAuthFailure,
    authenticate_provider_password,
    authenticate_provider_session,
)

logger = logging.getLogger("nexa_logger")


async def get_scoped_session(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="SESSION_GUARD",
            event_type="SESSION_VALIDATION_FAILED",
            target_id="UNKNOWN",
            status="MISSING_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Missing authorization token")

    patient_claims = decode_patient_access_token(authorization)
    if patient_claims:
        patient_id = str(patient_claims["patient_id"])
        bind_trusted_audit_tenant(patient_id)
        return patient_id

    session_context = await validate_session_context(authorization)
    if not session_context:
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="SESSION_GUARD",
            event_type="SESSION_VALIDATION_FAILED",
            target_id="UNKNOWN",
            status="INVALID_OR_EXPIRED",
        )
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    masked_internal_id = session_context.get("masked_internal_id")
    if not masked_internal_id:
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="SESSION_GUARD",
            event_type="SESSION_VALIDATION_FAILED",
            target_id="UNKNOWN",
            status="UNSCOPED_SESSION",
        )
        raise HTTPException(
            status_code=401, detail="Session is not scoped to a patient"
        )

    patient_id = str(masked_internal_id)
    bind_trusted_audit_tenant(patient_id)
    return patient_id


_provider_bearer_scheme = HTTPBearer(auto_error=False)
_provider_basic_scheme = HTTPBasic(auto_error=False)


def _client_ip_from_request(request: Request) -> str:
    return resolve_client_ip(request)


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
        # MFA is implemented via /api/v2/auth/login + /mfa/verify. Routes
        # that use this dependency (Bearer or Basic auth) should not accept
        # a half-authenticated password-only session. Tell the caller to
        # complete the MFA login flow and present a valid Bearer token.
        return HTTPException(
            status_code=401,
            detail=(
                "Multi-factor authentication required. "
                "Complete login via POST /api/v2/auth/mfa/verify and use the "
                "returned Bearer session token."
            ),
        )

    if failure is ProviderAuthFailure.MFA_NOT_CONFIGURED:
        # Inconsistent credential state: MFA flag is enabled but no secret
        # is enrolled. Fail closed and do not allow password-only access.
        return HTTPException(
            status_code=401,
            detail="Provider MFA is misconfigured. Contact an administrator.",
        )

    if failure is ProviderAuthFailure.SESSION_BINDING_MISMATCH:
        return HTTPException(
            status_code=401,
            detail="Session binding mismatch — User-Agent verification failed.",
        )

    if failure is ProviderAuthFailure.MFA_RATE_LIMITED:
        return HTTPException(
            status_code=429,
            detail="Too many failed MFA attempts. Please try again later.",
        )

    return HTTPException(status_code=401, detail="Invalid provider credentials")


async def get_provider_context(
    request: Request,
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

    Session binding is enforced on the bearer path: a User-Agent mismatch
    returns 401; an IP mismatch is allowed but logged as a warning.
    """

    user_agent = request.headers.get("user-agent")
    client_ip = _client_ip_from_request(request)

    cookies = getattr(request, "cookies", {})
    cookie_session = cookies.get("nexa_provider_session")
    if credentials is not None and credentials.credentials:
        result = await authenticate_provider_session(
            db,
            credentials.credentials,
            hospital_id,
            user_agent=user_agent,
            client_ip=client_ip,
        )
    elif isinstance(cookie_session, str) and cookie_session:
        result = await authenticate_provider_session(
            db,
            cookie_session,
            hospital_id,
            user_agent=user_agent,
            client_ip=client_ip,
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
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="PROVIDER_GUARD",
            event_type="PROVIDER_AUTH_FAILED",
            target_id=str(hospital_id) if hospital_id else "UNKNOWN",
            status="MISSING_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Missing provider credentials")

    if result.context is not None:
        bind_trusted_audit_hospital(str(result.context.hospital.hospital_id))
        if result.binding_warning == "SESSION_IP_ROTATION_DETECTED":
            logger.warning(
                json.dumps(
                    {
                        "event": "SESSION_IP_ROTATION_DETECTED",
                        "provider_id": str(result.context.provider.provider_id),
                        "ip_hash": hash_client_ip(client_ip),
                    }
                )
            )
        raw_session = (
            credentials.credentials
            if credentials is not None and credentials.credentials
            else cookie_session
        )
        binding = (
            hashlib.sha256(raw_session.encode("utf-8")).hexdigest()
            if raw_session
            else None
        )
        return result.context.model_copy(update={"session_binding": binding})

    assert result.failure is not None
    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid="PROVIDER_GUARD",
        event_type="PROVIDER_AUTH_FAILED",
        target_id=str(hospital_id) if hospital_id else "UNKNOWN",
        status=_audit_status_for_failure(result.failure),
    )
    raise _http_exception_for_failure(result.failure)


async def get_current_provider(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_provider_bearer_scheme),
    hospital_id: UUID | None = Header(default=None, alias="X-Hospital-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderContext:
    """Authenticate a provider using only ``Authorization: Bearer``.

    This is the Phase A provider-centric dependency for API-key/session-token
    protected routes. The bearer token is resolved to a provider, then the
    provider credential row is checked for active and lockout state by the auth
    service before a ``ProviderContext`` is returned.

    Session binding is enforced: a User-Agent mismatch returns 401; an IP
    mismatch is allowed but logged as a warning.
    """

    user_agent = request.headers.get("user-agent")
    client_ip = _client_ip_from_request(request)

    cookie_token = getattr(request, "cookies", {}).get("nexa_provider_session")
    if not isinstance(cookie_token, str):
        cookie_token = None
    session_token = (
        credentials.credentials
        if credentials is not None and credentials.credentials
        else cookie_token
    )
    if not session_token:
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.AUTH),
            actor_uid="PROVIDER_GUARD",
            event_type="PROVIDER_AUTH_FAILED",
            target_id=str(hospital_id) if hospital_id else "UNKNOWN",
            status="MISSING_TOKEN",
        )
        raise HTTPException(status_code=401, detail="Missing provider credentials")

    result = await authenticate_provider_session(
        db,
        session_token,
        hospital_id,
        user_agent=user_agent,
        client_ip=client_ip,
    )
    if result.context is not None:
        bind_trusted_audit_hospital(str(result.context.hospital.hospital_id))
        if result.binding_warning == "SESSION_IP_ROTATION_DETECTED":
            logger.warning(
                json.dumps(
                    {
                        "event": "SESSION_IP_ROTATION_DETECTED",
                        "provider_id": str(result.context.provider.provider_id),
                        "ip_hash": hash_client_ip(client_ip),
                    }
                )
            )
        binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        return result.context.model_copy(update={"session_binding": binding})

    assert result.failure is not None
    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid="PROVIDER_GUARD",
        event_type="PROVIDER_AUTH_FAILED",
        target_id=str(hospital_id) if hospital_id else "UNKNOWN",
        status=_audit_status_for_failure(result.failure),
    )
    raise _http_exception_for_failure(result.failure)


def require_role(required_role: str):
    """Factory that returns a FastAPI dependency enforcing a provider role.

    The role is checked against the active affiliation's ``roles`` list.
    Callers with multiple affiliations should supply ``X-Hospital-Id`` so
    the correct affiliation (and its roles) is selected.
    """

    async def _require_role(
        provider: ProviderContext = Depends(get_provider_context),
    ) -> ProviderContext:
        roles = provider.affiliation.roles or []
        if required_role not in roles:
            await append_audit_log(
                audit_context=current_audit_context(AuditDomain.AUTH),
                actor_uid=provider.actor_uid,
                event_type="PROVIDER_ROLE_DENIED",
                target_id=str(provider.provider.provider_id),
                status="FORBIDDEN",
                metadata={"required_role": required_role, "roles": roles},
            )
            raise HTTPException(
                status_code=403,
                detail=f"This action requires the '{required_role}' role.",
            )
        return provider

    return _require_role


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
