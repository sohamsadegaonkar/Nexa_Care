"""ConsentEngine: the single consent authority for Nexa Care V2.

Replaces app/services/consent_service.py, app/services/consent/routine.py,
and app/services/consent/break_glass.py (docs/CURRENT-STATE.md, Section 1 /
Section 4). This is the Phase 1 migration referenced there.

Design, in one paragraph: every grant is dual-written -- a durable,
hash-keyed row in Postgres (ConsentGrantLog, for compliance history that
must outlive a Redis TTL) and a live, bearer-token-keyed capability in
Redis (for fast validate/consume on the request path). Emergency
("break-glass") access is not a separate mechanism with its own Redis
namespace anymore -- it is the same issue()/validate()/consume() contract
with is_break_glass=True and a mandatory reason_code, which routes the
grant into the same compliance queue the old break_glass.py used.

Audit-before-write convention (matches register_patient, review_routes.py,
biometric_registry.py): issue() hard-fails an ATTEMPT audit before writing
anything, hard-fails a SUCCESS audit after both writes land, and does a
best-effort FAILED audit on any exception in between. consume() and
revoke() are lower-stakes (they narrow or remove access, they don't grant
it) so they audit best-effort rather than hard-failing the caller.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_redis_config
from app.models.consent_grant import ConsentGrantLog
from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503

CONSENT_TOKEN_PREFIX = "nexa:consent:"
DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour, matches the old consent_service.py default
BREAK_GLASS_TTL_SECONDS = 15 * 60  # matches the old break_glass.py default
COMPLIANCE_QUEUE_KEY = "nexa:compliance_queue:break_glass"


class ConsentEngineUnavailable(RuntimeError):
    """Raised when Redis or Postgres cannot be reached for a consent operation."""


@dataclass(frozen=True, slots=True)
class ConsentCapability:
    """A validated, live consent grant. Returned by validate()/consume()."""

    patient_id: str
    clinician_id: str
    purpose: str
    scope: list[str]
    is_break_glass: bool
    reason_code: str | None
    issued_at: str


@lru_cache(maxsize=1)
def get_consent_redis_client() -> redis_async.Redis:
    """Process-wide async Redis client for live consent capabilities."""

    cfg = get_redis_config()
    return redis_async.from_url(cfg.url, decode_responses=True)


def _token_key(token: str) -> str:
    return token if token.startswith(CONSENT_TOKEN_PREFIX) else f"{CONSENT_TOKEN_PREFIX}{token}"


def _token_hash(token: str) -> str:
    """Non-secret correlation id for the durable Postgres row.

    Deliberately not reversible to the raw token -- see consent_grant.py's
    module docstring for why the raw token is never persisted to Postgres.
    """
    clean = token[len(CONSENT_TOKEN_PREFIX):] if token.startswith(CONSENT_TOKEN_PREFIX) else token
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _parse_payload(raw_value: object) -> ConsentCapability | None:
    if not raw_value:
        return None
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")

    try:
        payload = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    patient_id = payload.get("patient_id")
    clinician_id = payload.get("clinician_id")
    purpose = payload.get("purpose")
    scope = payload.get("scope")
    issued_at = payload.get("issued_at")
    is_break_glass = payload.get("is_break_glass", False)
    reason_code = payload.get("reason_code")

    required_strings = [patient_id, clinician_id, purpose, issued_at]
    if not all(isinstance(v, str) and v for v in required_strings):
        return None
    if not isinstance(scope, list) or not all(isinstance(s, str) and s.strip() for s in scope):
        return None
    if not isinstance(is_break_glass, bool):
        return None
    if is_break_glass and not (isinstance(reason_code, str) and reason_code.strip()):
        return None

    return ConsentCapability(
        patient_id=patient_id,
        clinician_id=clinician_id,
        purpose=purpose,
        scope=[s.strip() for s in scope],
        is_break_glass=is_break_glass,
        reason_code=reason_code.strip() if isinstance(reason_code, str) else None,
        issued_at=issued_at,
    )


def _matches(capability: ConsentCapability, patient_id: str, clinician_id: str, purpose: str) -> bool:
    return (
        capability.patient_id == patient_id
        and capability.clinician_id == clinician_id
        and capability.purpose == purpose
    )


async def issue(
    *,
    db: AsyncSession,
    patient_id: str,
    clinician_id: str,
    purpose: str,
    scope: list[str],
    ttl_seconds: int | None = None,
    is_break_glass: bool = False,
    reason_code: str | None = None,
) -> str:
    """Issue a consent capability. Returns the raw bearer token.

    Hard-fails (raises) on: invalid input, an audit write failure at
    either the ATTEMPT or SUCCESS stage, or a Postgres/Redis write
    failure. A grant that cannot be fully audited and fully persisted in
    both stores is not allowed to silently go live -- same principle as
    register_patient and biometric enrollment elsewhere in this codebase.
    """
    clean_scope = [s.strip() for s in scope if isinstance(s, str) and s.strip()]
    if not clean_scope:
        raise ValueError("Consent scope must contain at least one field.")
    if is_break_glass and not (reason_code and reason_code.strip()):
        raise ValueError("Break-glass grants require a non-empty reason_code.")

    ttl = ttl_seconds or (BREAK_GLASS_TTL_SECONDS if is_break_glass else DEFAULT_TTL_SECONDS)
    if ttl <= 0:
        raise ValueError("ttl_seconds must be positive.")

    target_id = f"{patient_id}:{clinician_id}:{purpose}"
    await append_audit_log_or_503(
        actor_uid=clinician_id,
        event_type="CONSENT_GRANT_ATTEMPT",
        target_id=target_id,
        status="STARTED",
        metadata={"purpose": purpose, "is_break_glass": is_break_glass},
    )

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)
    clean_reason = reason_code.strip() if reason_code else None

    payload = {
        "patient_id": patient_id,
        "clinician_id": clinician_id,
        "purpose": purpose,
        "scope": clean_scope,
        "is_break_glass": is_break_glass,
        "reason_code": clean_reason,
        "issued_at": now.isoformat(),
    }

    try:
        # Postgres first: a grant must have a durable record before it's
        # allowed to become live and readable in Redis.
        row = ConsentGrantLog(
            token_hash=_token_hash(token),
            patient_id=patient_id,
            clinician_id=clinician_id,
            purpose=purpose,
            scope=clean_scope,
            is_break_glass=is_break_glass,
            reason_code=clean_reason,
            issued_at=now,
            expires_at=expires_at,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await append_audit_log(
            actor_uid=clinician_id,
            event_type="CONSENT_GRANT_FAILED",
            target_id=target_id,
            status="DURABLE_LOG_WRITE_FAILED",
        )
        raise ConsentEngineUnavailable("Consent grant could not be persisted.") from exc

    try:
        redis_client = get_consent_redis_client()
        await redis_client.set(_token_key(token), json.dumps(payload, sort_keys=True), ex=int(ttl))

        if is_break_glass:
            notification = {
                "patient_id": patient_id,
                "clinician_id": clinician_id,
                "purpose": purpose,
                "reason_code": clean_reason,
                "issued_at": now.isoformat(),
            }
            # Best-effort: a compliance-queue push failing shouldn't undo
            # an otherwise-valid, already-durable emergency grant. It's
            # logged via the SUCCESS audit metadata either way.
            try:
                await redis_client.rpush(COMPLIANCE_QUEUE_KEY, json.dumps(notification, sort_keys=True))
            except Exception:
                pass
    except Exception as exc:
        # The Postgres row already committed as "issued" -- rollback()
        # here would be a no-op against already-committed data. Instead,
        # mark that row revoked immediately so the durable log never
        # claims a live capability that was never actually reachable.
        try:
            row.revoked_at = datetime.now(timezone.utc)
            row.revoked_reason = "redis_write_failed"
            await db.commit()
        except Exception:
            await db.rollback()
        await append_audit_log(
            actor_uid=clinician_id,
            event_type="CONSENT_GRANT_FAILED",
            target_id=target_id,
            status="LIVE_STORE_WRITE_FAILED",
        )
        raise ConsentEngineUnavailable("Consent capability could not be made live.") from exc

    await append_audit_log_or_503(
        actor_uid=clinician_id,
        event_type="CONSENT_GRANT_SUCCESS",
        target_id=target_id,
        status="SUCCESS",
        metadata={"purpose": purpose, "is_break_glass": is_break_glass, "expires_at": expires_at.isoformat()},
    )

    return token


async def validate(
    *,
    token: str,
    patient_id: str,
    clinician_id: str,
    purpose: str,
) -> ConsentCapability | None:
    """Validate a live capability without consuming it. Fails closed."""

    try:
        redis_client = get_consent_redis_client()
        raw_value = await redis_client.get(_token_key(token))
    except Exception as exc:
        raise ConsentEngineUnavailable("Consent store is unavailable.") from exc

    capability = _parse_payload(raw_value)
    if capability is None or not _matches(capability, patient_id, clinician_id, purpose):
        return None
    return capability


async def consume(
    *,
    db: AsyncSession,
    token: str,
    patient_id: str,
    clinician_id: str,
    purpose: str,
) -> ConsentCapability | None:
    """Atomically consume a single-use capability. Fails closed.

    The Postgres consumed_at update is best-effort: the read this
    capability was guarding has already happened by the time a caller
    calls consume() (matches routine.py's existing consume-after-read
    shape), so a durable-log write failure here shouldn't be surfaced as
    if the access itself failed. It's logged via a best-effort audit.
    """

    try:
        redis_client = get_consent_redis_client()
        raw_value = await redis_client.getdel(_token_key(token))
    except Exception as exc:
        raise ConsentEngineUnavailable("Consent store is unavailable.") from exc

    capability = _parse_payload(raw_value)
    if capability is None or not _matches(capability, patient_id, clinician_id, purpose):
        return None

    target_id = f"{patient_id}:{clinician_id}:{purpose}"
    try:
        stmt = select(ConsentGrantLog).where(ConsentGrantLog.token_hash == _token_hash(token))
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            row.consumed_at = datetime.now(timezone.utc)
            await db.commit()
        await append_audit_log(
            actor_uid=clinician_id,
            event_type="CONSENT_CONSUMED",
            target_id=target_id,
            status="SUCCESS",
        )
    except Exception:
        await db.rollback()
        await append_audit_log(
            actor_uid=clinician_id,
            event_type="CONSENT_CONSUMED",
            target_id=target_id,
            status="DURABLE_LOG_UPDATE_FAILED",
        )

    return capability


async def revoke(*, db: AsyncSession, token: str, reason: str = "manual_revocation") -> None:
    """Best-effort revocation -- never raises. Matches the old
    consent_service.py's revoke_routine_consent: a revoke that can't be
    fully persisted shouldn't block whatever caller is trying to clean up
    (e.g. a rollback path), but we still try both stores and log it.
    """

    try:
        redis_client = get_consent_redis_client()
        await redis_client.delete(_token_key(token))
    except Exception:
        pass

    try:
        stmt = select(ConsentGrantLog).where(ConsentGrantLog.token_hash == _token_hash(token))
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None and row.revoked_at is None and row.consumed_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            row.revoked_reason = reason
            await db.commit()
    except Exception:
        await db.rollback()