"""Additional authority gate for low-entropy patient discovery.

Clinical capability remains necessary for all discovery.  PHONE lookup adds a
fresh provider-MFA requirement so a stolen long-lived provider session cannot
be used as a high-throughput account-enumeration oracle.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Request

from app.models.provider_context import ProviderContext
from app.services.provider_auth_service import resolve_provider_session_context

PHONE_DISCOVERY_MFA_MAX_AGE_SECONDS = 15 * 60


class PatientDiscoveryHighRiskGateError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _session_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, separator, credential = authorization.partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not credential
            or credential != credential.strip()
        ):
            raise PatientDiscoveryHighRiskGateError("DISCOVERY_SESSION_REQUIRED")
        return credential
    cookie = request.cookies.get("nexa_provider_session")
    if isinstance(cookie, str) and cookie:
        return cookie
    raise PatientDiscoveryHighRiskGateError("DISCOVERY_SESSION_REQUIRED")


def _recent_mfa(value: object, *, now: datetime) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        verified_at = datetime.fromisoformat(value)
    except ValueError:
        return False
    if verified_at.tzinfo is None:
        return False
    age = (now - verified_at.astimezone(timezone.utc)).total_seconds()
    return -60 <= age <= PHONE_DISCOVERY_MFA_MAX_AGE_SECONDS


async def require_recent_mfa_for_phone_discovery(
    request: Request,
    *,
    provider: ProviderContext,
    now: datetime | None = None,
) -> None:
    """Revalidate the exact live provider session and recent MFA assertion."""

    token = _session_token(request)
    expected_binding = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if (
        not provider.session_binding
        or not secrets.compare_digest(provider.session_binding, expected_binding)
    ):
        raise PatientDiscoveryHighRiskGateError("DISCOVERY_SESSION_BINDING_MISMATCH")

    session = await resolve_provider_session_context(token)
    if session is None:
        raise PatientDiscoveryHighRiskGateError("DISCOVERY_SESSION_REQUIRED")
    if str(session.get("provider_id", "")) != provider.actor_uid:
        raise PatientDiscoveryHighRiskGateError("DISCOVERY_SESSION_BINDING_MISMATCH")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or not _recent_mfa(
        session.get("mfa_verified_at"), now=current
    ):
        raise PatientDiscoveryHighRiskGateError("DISCOVERY_RECENT_MFA_REQUIRED")
