"""Provider authentication and affiliation resolution for Nexa Care V2.

Credentials are verified against ``provider_credential``; successful
authentication yields a ``ProviderContext`` with the resolved hospital
affiliation. Biometric step-up is intentionally out of scope here.
"""

from __future__ import annotations

import enum
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

from passlib.context import CryptContext

try:
    import argon2  # noqa: F401
    _ARGON2_AVAILABLE = True
except Exception:
    _ARGON2_AVAILABLE = False
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.redis import get_redis_client
from app.models.provider import (
    AffiliationType,
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)

_PASSWORD_CONTEXT = CryptContext(
    schemes=["argon2", "pbkdf2_sha256", "bcrypt"] if _ARGON2_AVAILABLE else ["pbkdf2_sha256", "bcrypt"],
    deprecated=["pbkdf2_sha256", "bcrypt"] if _ARGON2_AVAILABLE else ["bcrypt"],
)
_PROVIDER_SESSION_PREFIX = "provider_session:"
_PROVIDER_SESSION_TTL_SECONDS = 60 * 60 * 8  # 8 hours
_MAX_FAILED_LOGIN_ATTEMPTS = 5
_LOCKOUT_DURATION = timedelta(minutes=15)


class ProviderAuthFailure(str, enum.Enum):
    """Machine-readable provider authentication failure causes."""

    INVALID_CREDENTIALS = "invalid_credentials"
    ACCOUNT_LOCKED = "account_locked"
    # MFA-DISABLED-EXPLICITLY (2026-07-03): mfa_enabled=True is a real,
    # reachable provider_credential state, but no /mfa/verify route (or
    # any other MFA-completion path) exists anywhere in this codebase.
    # This correctly fails closed -- a password match alone is never
    # sufficient for an MFA-enabled account -- but until a real
    # /mfa/verify flow ships, this is a *permanent* dead end for that
    # account, not a step in a working flow. Callers (auth_routes.py,
    # core/dependencies.py) surface this as an explicit 501, distinct
    # from a routine credential failure, precisely because there is
    # currently nothing the caller can do to get past it.
    MFA_REQUIRED = "mfa_required"
    PROVIDER_INACTIVE = "provider_inactive"
    AFFILIATION_REQUIRED = "affiliation_required"
    AFFILIATION_NOT_FOUND = "affiliation_not_found"


class ProviderAuthResult(NamedTuple):
    """Outcome of a provider authentication attempt."""

    context: ProviderContext | None
    failure: ProviderAuthFailure | None = None


def hash_provider_password(plain_password: str) -> str:
    """Hash a plaintext password for storage in provider_credential."""

    return _PASSWORD_CONTEXT.hash(plain_password)


def verify_provider_password(plain_password: str, password_hash: str) -> bool:
    """Constant-time password verification against a stored password hash."""

    return _PASSWORD_CONTEXT.verify(plain_password, password_hash)


async def _record_failed_login(db: AsyncSession, credential: ProviderCredential) -> None:
    """Persist a failed password attempt before returning an auth failure."""

    credential.failed_login_attempts = (credential.failed_login_attempts or 0) + 1
    if credential.failed_login_attempts >= _MAX_FAILED_LOGIN_ATTEMPTS:
        credential.locked_until = datetime.now(timezone.utc) + _LOCKOUT_DURATION
    await db.commit()


async def _record_successful_login(db: AsyncSession, credential: ProviderCredential) -> None:
    """Clear brute-force counters after a fully successful provider login."""

    credential.failed_login_attempts = 0
    credential.locked_until = None
    await db.commit()


async def issue_provider_session_token(provider_id: uuid.UUID) -> str:
    """Mint an opaque Redis-backed session token for an authenticated provider."""

    token = secrets.token_urlsafe(32)
    payload = {
        "authenticated": True,
        "provider_id": str(provider_id),
    }
    redis = get_redis_client()
    key = f"{_PROVIDER_SESSION_PREFIX}{token}"
    set_result = redis.setex(key, _PROVIDER_SESSION_TTL_SECONDS, json.dumps(payload))
    if hasattr(set_result, "__await__"):
        await set_result
    return token


async def resolve_provider_session_token(token: str) -> uuid.UUID | None:
    """Load a provider_id from a Redis session token, or None if invalid."""

    clean_token = token.removeprefix("Bearer ").strip()
    if not clean_token:
        return None

    try:
        redis = get_redis_client()
        cached = redis.get(f"{_PROVIDER_SESSION_PREFIX}{clean_token}")
        if hasattr(cached, "__await__"):
            cached = await cached
        if not cached:
            return None
        if isinstance(cached, bytes):
            cached = cached.decode("utf-8")
        payload: dict[str, Any] = json.loads(cached)
        if not payload.get("authenticated"):
            return None
        return uuid.UUID(str(payload["provider_id"]))
    except Exception:
        return None


async def load_credential_by_login(
    db: AsyncSession,
    login_identifier: str,
) -> ProviderCredential | None:
    """Fetch an active credential row with its provider eagerly loaded."""

    stmt = (
        select(ProviderCredential)
        .where(
            ProviderCredential.login_identifier == login_identifier,
            ProviderCredential.is_active.is_(True),
        )
        .options(selectinload(ProviderCredential.provider))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _stored_password_hash(credential: ProviderCredential) -> str:
    """Return the active stored provider password hash across schema versions."""

    return credential.hashed_password or credential.password_hash


async def load_provider_with_affiliations(
    db: AsyncSession,
    provider_id: uuid.UUID,
) -> ProviderIdentity | None:
    """Load a provider and all affiliations (with hospitals) in one round trip."""

    stmt = (
        select(ProviderIdentity)
        .where(
            ProviderIdentity.id == provider_id,
            ProviderIdentity.is_active.is_(True),
        )
        .options(
            selectinload(ProviderIdentity.credential),
            selectinload(ProviderIdentity.affiliations).selectinload(
                ProviderHospitalAffiliation.hospital
            ),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _credential_is_locked(credential: ProviderCredential) -> bool:
    if credential.locked_until is None:
        return False
    return credential.locked_until > datetime.now(timezone.utc)


def _affiliation_is_current(affiliation: ProviderHospitalAffiliation) -> bool:
    if not affiliation.is_active:
        return False
    now = datetime.now(timezone.utc)
    if affiliation.valid_from is not None and affiliation.valid_from > now:
        return False
    if affiliation.valid_until is not None and affiliation.valid_until <= now:
        return False
    return True


def _select_affiliation(
    affiliations: list[ProviderHospitalAffiliation],
    hospital_id: uuid.UUID | None,
) -> ProviderHospitalAffiliation | None:
    """Pick the active affiliation for the requested hospital context."""

    active = [row for row in affiliations if _affiliation_is_current(row)]
    if not active:
        return None

    if hospital_id is not None:
        for row in active:
            if row.hospital_id == hospital_id:
                return row
        return None

    primary = [row for row in active if row.is_primary]
    if len(primary) == 1:
        return primary[0]
    if len(active) == 1:
        return active[0]
    return None


def build_provider_context(
    provider: ProviderIdentity,
    affiliation: ProviderHospitalAffiliation,
) -> ProviderContext:
    """Construct an immutable ProviderContext from ORM rows."""

    hospital: HospitalRegistry = affiliation.hospital
    affiliation_type = AffiliationType(affiliation.affiliation_type)

    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider.id,
            display_name=provider.display_name or provider.provider_uid or str(provider.id),
            medical_registration_number=provider.medical_registration_number,
            specialty=provider.specialty,
            contact_email=provider.contact_email or provider.provider_uid or str(provider.id),
        ),
        hospital=HospitalContext(
            hospital_id=hospital.id,
            facility_code=hospital.facility_code,
            display_name=hospital.display_name,
        ),
        affiliation=AffiliationContext(
            affiliation_id=affiliation.id,
            affiliation_type=affiliation_type,
            department=affiliation.department,
            roles=list(affiliation.roles or []),
            is_primary=affiliation.is_primary,
            valid_from=affiliation.valid_from,
            valid_until=affiliation.valid_until,
        ),
    )


def _resolve_affiliation_failure(
    affiliations: list[ProviderHospitalAffiliation],
    hospital_id: uuid.UUID | None,
) -> ProviderAuthFailure:
    active = [row for row in affiliations if _affiliation_is_current(row)]
    if not active:
        return ProviderAuthFailure.AFFILIATION_NOT_FOUND
    if hospital_id is None and len(active) > 1:
        return ProviderAuthFailure.AFFILIATION_REQUIRED
    return ProviderAuthFailure.AFFILIATION_NOT_FOUND


async def authenticate_provider_password(
    db: AsyncSession,
    login_identifier: str,
    plain_password: str,
    hospital_id: uuid.UUID | None,
) -> ProviderAuthResult:
    """Verify login credentials and resolve hospital affiliation."""

    credential = await load_credential_by_login(db, login_identifier)
    if credential is None or credential.provider is None:
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
    if not credential.provider.is_active:
        return ProviderAuthResult(None, ProviderAuthFailure.PROVIDER_INACTIVE)
    if _credential_is_locked(credential):
        return ProviderAuthResult(None, ProviderAuthFailure.ACCOUNT_LOCKED)
    if not verify_provider_password(plain_password, _stored_password_hash(credential)):
        await _record_failed_login(db, credential)
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
    if credential.mfa_enabled:
        # See ProviderAuthFailure.MFA_REQUIRED docstring: fails closed
        # correctly, but is a permanent dead end until /mfa/verify exists.
        return ProviderAuthResult(None, ProviderAuthFailure.MFA_REQUIRED)

    provider = await load_provider_with_affiliations(db, credential.provider_id)
    if provider is None:
        return ProviderAuthResult(None, ProviderAuthFailure.PROVIDER_INACTIVE)
    if provider.credential is None or not provider.credential.is_active:
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
    if _credential_is_locked(provider.credential):
        return ProviderAuthResult(None, ProviderAuthFailure.ACCOUNT_LOCKED)

    affiliation = _select_affiliation(provider.affiliations, hospital_id)
    if affiliation is None:
        return ProviderAuthResult(
            None,
            _resolve_affiliation_failure(provider.affiliations, hospital_id),
        )

    await _record_successful_login(db, credential)
    return ProviderAuthResult(build_provider_context(provider, affiliation))


async def authenticate_provider_session(
    db: AsyncSession,
    session_token: str,
    hospital_id: uuid.UUID | None,
) -> ProviderAuthResult:
    """Resolve a Redis session token to a ProviderContext."""

    provider_id = await resolve_provider_session_token(session_token)
    if provider_id is None:
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)

    provider = await load_provider_with_affiliations(db, provider_id)
    if provider is None:
        return ProviderAuthResult(None, ProviderAuthFailure.PROVIDER_INACTIVE)
    if provider.credential is None or not provider.credential.is_active:
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
    if _credential_is_locked(provider.credential):
        return ProviderAuthResult(None, ProviderAuthFailure.ACCOUNT_LOCKED)

    affiliation = _select_affiliation(provider.affiliations, hospital_id)
    if affiliation is None:
        return ProviderAuthResult(
            None,
            _resolve_affiliation_failure(provider.affiliations, hospital_id),
        )

    return ProviderAuthResult(build_provider_context(provider, affiliation))