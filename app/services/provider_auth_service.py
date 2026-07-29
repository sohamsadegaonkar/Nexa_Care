"""Provider authentication and affiliation resolution for Nexa Care V2.

Credentials are verified against ``provider_credential``; successful
authentication yields a ``ProviderContext`` with the resolved hospital
affiliation. MFA is implemented via TOTP (pyotp) with a pending-token
step between password verification and final session issuance.
"""

from __future__ import annotations

import enum
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from typing import Any, NamedTuple

import pyotp
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.redis import get_async_redis_client as get_redis_client
from app.core.security import (
    decrypt_mfa_secret,
    hash_client_ip,
    hash_user_agent,
)
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

_ARGON2_AVAILABLE = find_spec("argon2") is not None

_PASSWORD_CONTEXT = CryptContext(
    schemes=["argon2", "pbkdf2_sha256", "bcrypt"]
    if _ARGON2_AVAILABLE
    else ["pbkdf2_sha256", "bcrypt"],
    deprecated=["pbkdf2_sha256", "bcrypt"] if _ARGON2_AVAILABLE else ["bcrypt"],
)
_DUMMY_PASSWORD_HASH = _PASSWORD_CONTEXT.hash("nexa-provider-auth-dummy-password")
_PROVIDER_SESSION_PREFIX = "provider_session:"
_PROVIDER_SESSION_TTL_SECONDS = 60 * 60 * 8  # 8 hours

_MFA_PENDING_PREFIX = "mfa_pending:"
_MFA_PENDING_TTL_SECONDS = 5 * 60  # 5 minutes
_MFA_ISSUER_NAME = "Nexa Care"
_MAX_FAILED_MFA_ATTEMPTS = 5
_MFA_LOCKOUT_SECONDS = 15 * 60
_MFA_FAILS_PREFIX = "mfa_fails:"
_MFA_USED_TOTP_PREFIX = "mfa_totp_used:"
_TOTP_VALID_WINDOW = 1


logger = logging.getLogger("nexa_logger")


class ProviderSessionStoreUnavailable(RuntimeError):
    """Provider session revocation could not be durably applied."""


class ProviderAuthFailure(str, enum.Enum):
    """Machine-readable provider authentication failure causes."""

    INVALID_CREDENTIALS = "invalid_credentials"
    ACCOUNT_LOCKED = "account_locked"
    MFA_REQUIRED = "mfa_required"
    MFA_SESSION_EXPIRED = "mfa_session_expired"
    MFA_INVALID_CODE = "mfa_invalid_code"
    MFA_NOT_CONFIGURED = "mfa_not_configured"
    PROVIDER_INACTIVE = "provider_inactive"
    AFFILIATION_REQUIRED = "affiliation_required"
    AFFILIATION_NOT_FOUND = "affiliation_not_found"
    SESSION_BINDING_MISMATCH = "session_binding_mismatch"
    MFA_RATE_LIMITED = "mfa_rate_limited"


class ProviderAuthResult(NamedTuple):
    """Outcome of a provider authentication attempt.

    If ``failure`` is ``MFA_REQUIRED``, ``mfa_pending_token`` will contain
    a short-lived token the client must supply to the MFA verify endpoint
    along with the current TOTP code.
    """

    context: ProviderContext | None
    failure: ProviderAuthFailure | None = None
    mfa_pending_token: str | None = None
    binding_warning: str | None = None


def hash_provider_password(plain_password: str) -> str:
    """Hash a plaintext password for storage in provider_credential."""

    return _PASSWORD_CONTEXT.hash(plain_password)


def verify_provider_password(plain_password: str, password_hash: str) -> bool:
    """Verify a provider password and fail closed for malformed hashes."""

    try:
        return _PASSWORD_CONTEXT.verify(plain_password, password_hash)
    except (TypeError, ValueError):
        return False


def normalize_provider_login_identifier(login_identifier: str) -> str:
    """Return the canonical case-insensitive provider login identifier."""

    return login_identifier.strip().lower()


async def _maybe_await(value: Any) -> Any:
    return await value if hasattr(value, "__await__") else value


async def _record_failed_login(
    db: AsyncSession, credential: ProviderCredential
) -> None:
    """Record an anomaly signal without creating attacker-controlled lockout."""

    credential.failed_login_attempts = min(
        (credential.failed_login_attempts or 0) + 1, 1_000_000
    )
    await db.commit()


async def _record_successful_login(
    db: AsyncSession, credential: ProviderCredential
) -> None:
    """Clear brute-force counters after a fully successful provider login."""

    credential.failed_login_attempts = 0
    credential.locked_until = None
    await db.commit()


def generate_totp_secret() -> str:
    """Generate a new TOTP shared secret for MFA enrollment."""

    return pyotp.random_base32()


def get_totp_provisioning_uri(secret: str, login_identifier: str) -> str:
    """Return the otpauth:// URI a provider scans into their authenticator app."""

    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=login_identifier,
        issuer_name=_MFA_ISSUER_NAME,
    )


def _matched_totp_counter(
    secret: str, code: str, valid_window: int = _TOTP_VALID_WINDOW
) -> int | None:
    """Return the accepted TOTP counter for ``code``, or None if invalid."""
    if not secret or not code:
        return None
    try:
        totp = pyotp.totp.TOTP(secret)
        now = datetime.now(timezone.utc)
        for offset in range(-valid_window, valid_window + 1):
            candidate_time = now + timedelta(seconds=offset * totp.interval)
            if totp.verify(code, for_time=candidate_time, valid_window=0):
                return int(totp.timecode(candidate_time))
    except Exception:
        return None
    return None


def verify_totp_code(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code against the stored secret."""

    return _matched_totp_counter(secret, code) is not None


async def _consume_totp_counter(
    provider_id: uuid.UUID, counter: int, redis_client: Any | None = None
) -> bool:
    """Atomically mark a TOTP timestep used for replay protection.

    Returns False on replay or Redis failure. MFA replay protection is
    fail-closed: a valid code is not accepted unless the used-counter marker
    can be written atomically.
    """
    try:
        redis = redis_client or get_redis_client()
        ttl_seconds = (_TOTP_VALID_WINDOW * 2 + 2) * 30
        key = f"{_MFA_USED_TOTP_PREFIX}{provider_id}:{counter}"
        set_result = redis.set(key, "1", nx=True, ex=ttl_seconds)
        if hasattr(set_result, "__await__"):
            set_result = await set_result
        return bool(set_result)
    except Exception:
        logger.warning("MFA TOTP replay store unavailable; failing closed")
        return False


async def verify_totp_code_once(
    provider_id: uuid.UUID, secret: str, code: str, redis_client: Any | None = None
) -> bool:
    """Verify a TOTP code and atomically consume its accepted timestep."""
    if not verify_totp_code(secret, code):
        return False
    accepted_counter = _matched_totp_counter(secret, code)
    if accepted_counter is None:
        accepted_counter = int(datetime.now(timezone.utc).timestamp() // 30)
    return await _consume_totp_counter(
        provider_id, accepted_counter, redis_client=redis_client
    )


async def issue_mfa_pending_token(provider_id: uuid.UUID) -> str:
    """Mint a short-lived Redis token for the MFA completion step."""

    token = secrets.token_urlsafe(32)
    payload = {
        "provider_id": str(provider_id),
        "authenticated": True,  # password was already verified
    }
    redis = get_redis_client()
    key = f"{_MFA_PENDING_PREFIX}{token}"
    set_result = redis.setex(key, _MFA_PENDING_TTL_SECONDS, json.dumps(payload))
    if hasattr(set_result, "__await__"):
        await set_result
    return token


async def resolve_mfa_pending_token(token: str) -> uuid.UUID | None:
    """Load a provider_id from an MFA pending token, or None if invalid/expired."""

    clean_token = token.removeprefix("Bearer ").strip()
    if not clean_token:
        return None

    try:
        redis = get_redis_client()
        cached = redis.get(f"{_MFA_PENDING_PREFIX}{clean_token}")
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


async def delete_mfa_pending_token(token: str) -> None:
    """Burn a used MFA pending token."""

    try:
        redis = get_redis_client()
        delete_result = redis.delete(
            f"{_MFA_PENDING_PREFIX}{token.removeprefix('Bearer ').strip()}"
        )
        if hasattr(delete_result, "__await__"):
            await delete_result
    except Exception as exc:
        logger.warning(
            "MFA pending-token deletion failed",
            extra={"error_type": type(exc).__name__},
        )


async def issue_provider_session_token(
    provider_id: uuid.UUID,
    user_agent: str | None = None,
    client_ip: str | None = None,
    mfa_verified_at: datetime | None = None,
) -> str:
    """Mint an opaque Redis-backed session token bound to UA/IP."""

    token = secrets.token_urlsafe(32)
    payload = {
        "authenticated": True,
        "provider_id": str(provider_id),
        "ua_hash": hash_user_agent(user_agent),
        "ip_hash": hash_client_ip(client_ip),
        "expires_at": (
            datetime.now(timezone.utc)
            + timedelta(seconds=_PROVIDER_SESSION_TTL_SECONDS)
        ).isoformat(),
        "mfa_verified_at": mfa_verified_at.isoformat() if mfa_verified_at else None,
    }
    redis = get_redis_client()
    key = f"{_PROVIDER_SESSION_PREFIX}{token}"
    set_result = redis.setex(key, _PROVIDER_SESSION_TTL_SECONDS, json.dumps(payload))
    if hasattr(set_result, "__await__"):
        await set_result
    return token


async def revoke_provider_auth_sessions(provider_id: uuid.UUID) -> int:
    """Revoke all bearer and pending-MFA sessions for one provider.

    Existing sessions predate a provider-indexed Redis set, so this uses
    bounded SCAN operations. It fails closed if Redis cannot be inspected.
    No token-bearing key is logged or returned.
    """

    redis = get_redis_client()
    revoked = 0
    for prefix in (_PROVIDER_SESSION_PREFIX, _MFA_PENDING_PREFIX):
        cursor: int | str = 0
        while True:
            scan_result = await _maybe_await(
                redis.scan(cursor=cursor, match=f"{prefix}*", count=100)
            )
            cursor, keys = scan_result
            for key in keys:
                raw = await _maybe_await(redis.get(key))
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    payload = json.loads(raw) if raw else {}
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                if str(payload.get("provider_id", "")) == str(provider_id):
                    revoked += int(bool(await _maybe_await(redis.delete(key))))
            if int(cursor) == 0:
                break
    return revoked


async def resolve_provider_session_context(token: str) -> dict[str, Any] | None:
    """Load the full session context from a Redis session token."""

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
        return payload
    except Exception:
        return None


async def mark_provider_session_mfa_verified(
    token: str, provider_id: uuid.UUID
) -> bool:
    """Record fresh MFA on the same live opaque session without rotating it."""
    clean_token = token.removeprefix("Bearer ").strip()
    if not clean_token:
        return False
    redis = get_redis_client()
    key = f"{_PROVIDER_SESSION_PREFIX}{clean_token}"
    cached = await _maybe_await(redis.get(key))
    if not cached:
        return False
    if isinstance(cached, bytes):
        cached = cached.decode("utf-8")
    try:
        payload = json.loads(cached)
    except (TypeError, json.JSONDecodeError):
        return False
    if str(payload.get("provider_id")) != str(provider_id) or not payload.get(
        "authenticated"
    ):
        return False
    ttl = await _maybe_await(redis.ttl(key))
    if not isinstance(ttl, int) or ttl <= 0:
        return False
    payload["mfa_verified_at"] = datetime.now(timezone.utc).isoformat()
    await _maybe_await(redis.setex(key, ttl, json.dumps(payload, sort_keys=True)))
    return True


async def resolve_provider_session_token(token: str) -> uuid.UUID | None:
    """Load a provider_id from a Redis session token, or None if invalid."""

    payload = await resolve_provider_session_context(token)
    if payload is None:
        return None
    try:
        return uuid.UUID(str(payload["provider_id"]))
    except Exception:
        return None


async def delete_provider_session_token(token: str) -> None:
    """Burn a provider session token (logout)."""

    try:
        redis = get_redis_client()
        clean_token = token.removeprefix("Bearer ").strip()
        if clean_token:
            delete_result = redis.delete(f"{_PROVIDER_SESSION_PREFIX}{clean_token}")
            if hasattr(delete_result, "__await__"):
                await delete_result
    except Exception as exc:
        logger.error(
            "Provider session deletion unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise ProviderSessionStoreUnavailable(
            "Provider session revocation is unavailable"
        ) from exc


async def refresh_provider_session_token(
    old_token: str,
    user_agent: str | None = None,
    client_ip: str | None = None,
) -> str | None:
    """Rotate a valid provider session token to a new one.

    Returns the new token on success, or None if the old token is invalid.
    The old token is deleted immediately to prevent replay. The new token
    is bound to the UA/IP of the current request (refresh rebind).
    """

    payload = await resolve_provider_session_context(old_token)
    if payload is None:
        return None

    try:
        provider_id = uuid.UUID(str(payload["provider_id"]))
    except Exception:
        return None

    await delete_provider_session_token(old_token)
    mfa_verified_at: datetime | None = None
    raw_mfa_verified_at = payload.get("mfa_verified_at")
    if isinstance(raw_mfa_verified_at, str):
        try:
            mfa_verified_at = datetime.fromisoformat(raw_mfa_verified_at)
        except ValueError:
            return None
    return await issue_provider_session_token(
        provider_id,
        user_agent,
        client_ip,
        mfa_verified_at=mfa_verified_at,
    )


async def _get_mfa_fails_count(provider_id: uuid.UUID, ip_hash: str) -> int:
    """Return the current failed MFA attempt count for the composite key."""

    try:
        redis = get_redis_client()
        key = f"{_MFA_FAILS_PREFIX}{provider_id}:{ip_hash}"
        count = redis.get(key)
        if hasattr(count, "__await__"):
            count = await count
        if count is None:
            return 0
        return int(count)
    except Exception as exc:
        logger.error(
            "MFA failure counter unavailable; failing closed",
            extra={"error_type": type(exc).__name__},
        )
        return _MAX_FAILED_MFA_ATTEMPTS


async def _record_failed_mfa_attempt(provider_id: uuid.UUID, ip_hash: str) -> int:
    """Increment the composite-key failed MFA counter and set its TTL."""

    try:
        redis = get_redis_client()
        key = f"{_MFA_FAILS_PREFIX}{provider_id}:{ip_hash}"
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, _MFA_LOCKOUT_SECONDS)
        results = pipe.execute()
        if hasattr(results, "__await__"):
            results = await results
        return int(results[0])
    except Exception as exc:
        logger.error(
            "MFA failure counter update unavailable; failing closed",
            extra={"error_type": type(exc).__name__},
        )
        return _MAX_FAILED_MFA_ATTEMPTS


async def _clear_mfa_fails(provider_id: uuid.UUID, ip_hash: str) -> None:
    """Reset the composite-key failed MFA counter on successful verification."""

    try:
        redis = get_redis_client()
        key = f"{_MFA_FAILS_PREFIX}{provider_id}:{ip_hash}"
        delete_result = redis.delete(key)
        if hasattr(delete_result, "__await__"):
            await delete_result
    except Exception as exc:
        logger.warning(
            "MFA failure counter cleanup failed",
            extra={"error_type": type(exc).__name__},
        )


async def _is_mfa_rate_limited(provider_id: uuid.UUID, ip_hash: str) -> bool:
    """True if the composite-key MFA counter has reached the threshold."""

    count = await _get_mfa_fails_count(provider_id, ip_hash)
    return count >= _MAX_FAILED_MFA_ATTEMPTS


async def load_credential_by_login(
    db: AsyncSession,
    login_identifier: str,
) -> ProviderCredential | None:
    """Fetch an active credential row with its provider eagerly loaded."""

    normalized = normalize_provider_login_identifier(login_identifier)
    stmt = (
        select(ProviderCredential)
        .where(
            ProviderCredential.login_identifier == normalized,
            ProviderCredential.is_active.is_(True),
        )
        .options(selectinload(ProviderCredential.provider))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


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
            ProviderIdentity.status == "active",
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
            display_name=provider.display_name
            or provider.provider_uid
            or str(provider.id),
            medical_registration_number=provider.medical_registration_number,
            specialty=provider.specialty,
            contact_email=provider.contact_email
            or provider.provider_uid
            or str(provider.id),
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
        # Keep the unknown-account path close to a real password verification
        # so the public generic 401 is not also an account-existence oracle.
        verify_provider_password(plain_password, _DUMMY_PASSWORD_HASH)
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
    if not credential.provider.is_active or credential.provider.status != "active":
        return ProviderAuthResult(None, ProviderAuthFailure.PROVIDER_INACTIVE)
    if _credential_is_locked(credential):
        return ProviderAuthResult(None, ProviderAuthFailure.ACCOUNT_LOCKED)
    if not verify_provider_password(plain_password, credential.password_hash):
        await _record_failed_login(db, credential)
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
    if credential.mfa_enabled:
        mfa_secret = decrypt_mfa_secret(credential.mfa_secret_encrypted)
        if not mfa_secret:
            # MFA is enabled but no secret has been enrolled. This is an
            # inconsistent credential state; fail closed rather than allow
            # password-only access. The provider must contact an admin to
            # either disable MFA or enroll a TOTP secret.
            logger.critical(
                json.dumps(
                    {
                        "event": "provider_auth_mfa_enabled_without_secret",
                        "provider_id": str(credential.provider_id),
                    }
                )
            )
            return ProviderAuthResult(None, ProviderAuthFailure.MFA_NOT_CONFIGURED)

        # Password is correct. Issue a short-lived MFA pending token and
        # require the TOTP code to be verified via /mfa/verify before a
        # real session token is issued.
        mfa_token = await issue_mfa_pending_token(credential.provider_id)
        return ProviderAuthResult(
            None,
            ProviderAuthFailure.MFA_REQUIRED,
            mfa_pending_token=mfa_token,
        )

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
    user_agent: str | None = None,
    client_ip: str | None = None,
) -> ProviderAuthResult:
    """Resolve a Redis session token to a ProviderContext.

    Session binding is enforced as a hard UA check and a soft IP check:
    - UA mismatch -> SESSION_BINDING_MISMATCH (401)
    - IP mismatch -> context is returned with binding_warning
      "SESSION_IP_ROTATION_DETECTED" so the caller can log it.
    """

    payload = await resolve_provider_session_context(session_token)
    if payload is None:
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)

    try:
        provider_id = uuid.UUID(str(payload["provider_id"]))
    except Exception:
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)

    stored_ua_hash = payload.get("ua_hash", "")
    stored_ip_hash = payload.get("ip_hash", "")
    current_ua_hash = hash_user_agent(user_agent)
    current_ip_hash = hash_client_ip(client_ip)

    if stored_ua_hash and current_ua_hash and stored_ua_hash != current_ua_hash:
        return ProviderAuthResult(None, ProviderAuthFailure.SESSION_BINDING_MISMATCH)

    binding_warning: str | None = None
    if stored_ip_hash and current_ip_hash and stored_ip_hash != current_ip_hash:
        binding_warning = "SESSION_IP_ROTATION_DETECTED"

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

    return ProviderAuthResult(
        build_provider_context(provider, affiliation),
        binding_warning=binding_warning,
    )


async def complete_mfa_login(
    db: AsyncSession,
    mfa_token: str,
    totp_code: str,
    hospital_id: uuid.UUID | None,
    client_ip: str | None = None,
    claimed_provider_id: uuid.UUID | None = None,
) -> ProviderAuthResult:
    """Complete a provider login after MFA verification.

    Resolves the MFA pending token, verifies the TOTP code against the
    provider's stored secret, and returns a full ProviderContext on
    success. The pending token is burned regardless of outcome. Failed
    attempts are tracked under the composite key
    ``mfa_fails:{provider_id}:{ip_hash}`` to prevent MFA brute-force
    from being used as a provider-wide account lockout vector.

    ``claimed_provider_id``, if supplied by the caller, is NEVER used to
    resolve identity — identity comes exclusively from the Redis-backed
    pending token, which is proof the password step already succeeded for
    a specific provider. ``claimed_provider_id`` is checked only as a
    defense-in-depth integrity assertion: a mismatch means the caller's
    token and claimed identity disagree, which is treated as a
    session-confusion / IDOR probe (``SESSION_BINDING_MISMATCH``) and
    rejected before any further DB work is done.
    """

    provider_id = await resolve_mfa_pending_token(mfa_token)
    await delete_mfa_pending_token(mfa_token)

    if provider_id is None:
        return ProviderAuthResult(None, ProviderAuthFailure.MFA_SESSION_EXPIRED)

    if claimed_provider_id is not None and claimed_provider_id != provider_id:
        return ProviderAuthResult(None, ProviderAuthFailure.SESSION_BINDING_MISMATCH)

    ip_hash = hash_client_ip(client_ip)
    if await _is_mfa_rate_limited(provider_id, ip_hash):
        return ProviderAuthResult(None, ProviderAuthFailure.MFA_RATE_LIMITED)

    provider = await load_provider_with_affiliations(db, provider_id)
    if provider is None:
        return ProviderAuthResult(None, ProviderAuthFailure.PROVIDER_INACTIVE)
    if provider.credential is None or not provider.credential.is_active:
        return ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS)
    if _credential_is_locked(provider.credential):
        return ProviderAuthResult(None, ProviderAuthFailure.ACCOUNT_LOCKED)
    mfa_secret = decrypt_mfa_secret(provider.credential.mfa_secret_encrypted)
    if not mfa_secret or not provider.credential.mfa_enabled:
        return ProviderAuthResult(None, ProviderAuthFailure.MFA_NOT_CONFIGURED)

    accepted_counter = _matched_totp_counter(mfa_secret, totp_code)
    if accepted_counter is None:
        count = await _record_failed_mfa_attempt(provider_id, ip_hash)
        if count >= _MAX_FAILED_MFA_ATTEMPTS:
            return ProviderAuthResult(None, ProviderAuthFailure.MFA_RATE_LIMITED)
        return ProviderAuthResult(None, ProviderAuthFailure.MFA_INVALID_CODE)

    if not await _consume_totp_counter(provider_id, accepted_counter):
        count = await _record_failed_mfa_attempt(provider_id, ip_hash)
        if count >= _MAX_FAILED_MFA_ATTEMPTS:
            return ProviderAuthResult(None, ProviderAuthFailure.MFA_RATE_LIMITED)
        return ProviderAuthResult(None, ProviderAuthFailure.MFA_INVALID_CODE)

    await _clear_mfa_fails(provider_id, ip_hash)

    affiliation = _select_affiliation(provider.affiliations, hospital_id)
    if affiliation is None:
        return ProviderAuthResult(
            None,
            _resolve_affiliation_failure(provider.affiliations, hospital_id),
        )

    await _record_successful_login(db, provider.credential)
    return ProviderAuthResult(build_provider_context(provider, affiliation))
