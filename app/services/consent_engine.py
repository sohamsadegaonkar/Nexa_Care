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
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from typing import Optional, List, Any

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_redis_config
from app.models.assurance import AssuranceLevel
from app.models.consent_grant import ConsentGrantLog
from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503
from app.services.assurance_verifier import AssuranceVerifier, RedisAssuranceVerifier

logger = logging.getLogger("nexa_logger")

CONSENT_TOKEN_PREFIX = "nexa:consent:"
DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour, matches the old consent_service.py default
BREAK_GLASS_TTL_SECONDS = 15 * 60  # matches the old break_glass.py default
COMPLIANCE_QUEUE_KEY = "nexa:compliance_queue:break_glass"


class ConsentPurpose(str, Enum):
    """Canonical consent purposes for Nexa Care V2."""
    TREATMENT = "TREATMENT"
    PAYMENT = "PAYMENT"
    OPERATIONS = "OPERATIONS"
    RESEARCH = "RESEARCH"


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
    expires_at: str
    hospital_id: str | None
    session_binding: str | None
    reason_code_version: str | None


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
    expires_at = payload.get("expires_at")
    hospital_id = payload.get("hospital_id")
    session_binding = payload.get("session_binding")
    reason_code_version = payload.get("reason_code_version")

    required_strings = [patient_id, clinician_id, purpose, issued_at, expires_at]
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
        expires_at=expires_at,
        hospital_id=hospital_id if isinstance(hospital_id, str) and hospital_id else None,
        session_binding=session_binding if isinstance(session_binding, str) and session_binding else None,
        reason_code_version=(reason_code_version if isinstance(reason_code_version, str) else None),
    )


def _matches(
    capability: ConsentCapability,
    patient_id: str | None,
    clinician_id: str | None,
    purpose: str | None,
    hospital_id: str | None = None,
    session_binding: str | None = None,
) -> bool:
    """Match a capability against caller-supplied constraints.

    ``None`` means "do not check this field". V2 routes always bind all
    three fields; v1 self-consent routes bind clinician and purpose but
    discover the patient_id from the token itself.
    """
    if patient_id is not None and capability.patient_id != patient_id:
        return False
    if clinician_id is not None and capability.clinician_id != clinician_id:
        return False
    if purpose is not None:
        if capability.is_break_glass:
            if purpose not in capability.scope:
                return False
        elif capability.purpose != purpose and purpose not in capability.scope:
            return False
    if hospital_id is not None and capability.hospital_id != hospital_id:
        return False
    if session_binding is not None and capability.session_binding != session_binding:
        return False
    if capability.is_break_glass and capability.session_binding and session_binding is None:
        return False
    return True


async def issue(
    *,
    db: AsyncSession,
    patient_id: str,
    clinician_id: str,
    purpose: str,
    scope: list[str],
    assurance_level: AssuranceLevel,
    assurance_evidence: dict[str, Any],
    hospital_id: str | None = None,
    ttl_seconds: int | None = None,
    is_break_glass: bool = False,
    reason_code: str | None = None,
    reason_code_version: str | None = None,
    session_binding: str | None = None,
    verifier: Optional[AssuranceVerifier] = None,
) -> str:
    """Issue a consent capability. Returns the raw bearer token.

    Security Sprint (Sprint 2): Verifies the claimed assurance level
    before issuing. Hard-fails on verification failure or audit failure.
    """
    clean_scope = [s.strip() for s in scope if isinstance(s, str) and s.strip()]
    if not clean_scope:
        raise ValueError("Consent scope must contain at least one field.")
    if is_break_glass and not (reason_code and reason_code.strip()):
        raise ValueError("Break-glass grants require a non-empty reason_code.")

    redis_client = get_consent_redis_client()
    actual_verifier = verifier or RedisAssuranceVerifier()

    # 1. Verify Assurance Evidence
    try:
        assurance_result = await actual_verifier.verify(
            level=assurance_level,
            patient_id=patient_id,
            evidence=assurance_evidence,
            redis=redis_client,
        )
    except Exception as exc:
        raise ConsentEngineUnavailable("Assurance store is unavailable.") from exc

    if not assurance_result.verified:
        await append_audit_log(
            actor_uid=clinician_id,
            event_type="ASSURANCE_VERIFICATION_FAILED",
            target_id=patient_id,
            status="FORBIDDEN",
            metadata={
                "claimed_level": assurance_level,
                "evidence_keys": list(assurance_evidence.keys()),
            },
        )
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Assurance verification failed")

    ttl = ttl_seconds or (BREAK_GLASS_TTL_SECONDS if is_break_glass else DEFAULT_TTL_SECONDS)
    if ttl <= 0:
        raise ValueError("ttl_seconds must be positive.")

    target_id = f"{patient_id}:{clinician_id}:{purpose}"
    await append_audit_log_or_503(
        actor_uid=clinician_id,
        event_type="CONSENT_GRANT_ATTEMPT",
        target_id=target_id,
        status="STARTED",
        metadata={
            "purpose": purpose,
            "is_break_glass": is_break_glass,
            "assurance_level": assurance_result.actual_level,
        },
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
        "expires_at": expires_at.isoformat(),
        "hospital_id": hospital_id,
        "session_binding": session_binding,
        "reason_code_version": reason_code_version,
        "assurance_level": assurance_result.actual_level,
    }

    try:
        # Postgres first: a grant must have a durable record before it's
        # allowed to become live and readable in Redis.
        row = ConsentGrantLog(
            token_hash=_token_hash(token),
            patient_id=patient_id,
            clinician_id=clinician_id,
            hospital_id=uuid.UUID(hospital_id) if hospital_id else None,
            purpose=purpose,
            scope=clean_scope,
            is_break_glass=is_break_glass,
            reason_code=clean_reason,
            issued_at=now,
            expires_at=expires_at,
            assurance_level=assurance_result.actual_level,
            assurance_verified_at=assurance_result.verification_timestamp,
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
            except Exception as exc:
                logger.error(
                    "Break-glass compliance notification enqueue failed",
                    extra={"error_type": type(exc).__name__},
                )
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
        metadata={
            "purpose": purpose,
            "is_break_glass": is_break_glass,
            "expires_at": expires_at.isoformat(),
            "reason_code_version": reason_code_version,
        },
    )

    return token


async def issue_routine(
    patient_id: str,
    clinician_id: str,
    purpose: ConsentPurpose,
    scope: List[str],
    db: AsyncSession,
    assurance_level: AssuranceLevel = AssuranceLevel.STANDARD,
    assurance_evidence: Optional[dict] = None,
    redis: Optional[redis_async.Redis] = None,
    ttl_seconds: int = 3600,
    hospital_id: str | None = None,
) -> str:
    """Issue a routine consent capability.

    Consolidation Method (Sprint 2): Enforces is_break_glass=False and
    validates purpose against the canonical ConsentPurpose enum.
    """
    if not isinstance(purpose, ConsentPurpose):
        raise ValueError(f"Invalid purpose. Must be one of: {[p.value for p in ConsentPurpose]}")

    evidence = assurance_evidence or {}

    target_id = f"{patient_id}:{clinician_id}:{purpose.value}"
    await append_audit_log_or_503(
        actor_uid=clinician_id,
        event_type="ROUTINE_CONSENT_GRANT_ATTEMPT",
        target_id=target_id,
        status="STARTED",
        metadata={"purpose": purpose.value, "assurance_level": assurance_level},
    )

    try:
        token = await issue(
            db=db,
            patient_id=patient_id,
            clinician_id=clinician_id,
            purpose=purpose.value,
            scope=scope,
            assurance_level=assurance_level,
            assurance_evidence=evidence,
            hospital_id=hospital_id,
            ttl_seconds=ttl_seconds,
            is_break_glass=False,
        )
    except Exception as exc:
        await append_audit_log(
            actor_uid=clinician_id,
            event_type="ROUTINE_CONSENT_GRANT_FAILED",
            target_id=target_id,
            status="FAILED",
            metadata={"error_code": "ROUTINE_CONSENT_ISSUANCE_FAILED"},
        )
        raise

    await append_audit_log_or_503(
        actor_uid=clinician_id,
        event_type="ROUTINE_CONSENT_GRANT_SUCCESS",
        target_id=target_id,
        status="SUCCESS",
    )
    return token


async def issue_break_glass(
    patient_id: str,
    clinician_id: str,
    reason_code: str,
    db: AsyncSession,
    redis: Optional[redis_async.Redis] = None,
    hospital_id: str | None = None,
    scope: list[str] | None = None,
    reason_code_version: str | None = None,
    session_binding: str | None = None,
    mfa_verified_at: datetime | None = None,
) -> str:
    """Issue a break-glass emergency consent capability.

    Consolidation Method (Sprint 2): Enforces is_break_glass=True,
    a fixed 15-minute TTL, and server-approved minimum-necessary scope.
    """
    if not reason_code or not reason_code.strip():
        raise ValueError("Break-glass grants require a non-empty reason_code.")
    if not hospital_id or not session_binding or not mfa_verified_at or not reason_code_version:
        raise ValueError("Break-glass grants require tenant, session, policy, and MFA bindings.")
    if not scope:
        raise ValueError("Break-glass grants require an approved minimum-necessary scope.")

    target_id = f"{patient_id}:{clinician_id}:BREAK_GLASS"
    await append_audit_log_or_503(
        actor_uid=clinician_id,
        event_type="BREAK_GLASS_GRANT_ATTEMPT",
        target_id=target_id,
        status="STARTED",
        metadata={"reason_code": reason_code, "reason_code_version": reason_code_version},
    )

    try:
        token = await issue(
            db=db,
            patient_id=patient_id,
            clinician_id=clinician_id,
            purpose="EMERGENCY",
            scope=scope,
            assurance_level=AssuranceLevel.BREAK_GLASS,
            assurance_evidence={"server_mfa_verified_at": mfa_verified_at.isoformat()},
            hospital_id=hospital_id,
            ttl_seconds=900,  # Enforce 15-minute TTL
            is_break_glass=True,
            reason_code=reason_code,
            reason_code_version=reason_code_version,
            session_binding=session_binding,
        )
    except Exception as exc:
        await append_audit_log(
            actor_uid=clinician_id,
            event_type="BREAK_GLASS_GRANT_FAILED",
            target_id=target_id,
            status="FAILED",
            metadata={"error_code": "BREAK_GLASS_ISSUANCE_FAILED"},
        )
        raise

    await append_audit_log_or_503(
        actor_uid=clinician_id,
        event_type="BREAK_GLASS_GRANT_SUCCESS",
        target_id=target_id,
        status="SUCCESS",
    )
    return token


async def validate(
    *,
    token: str,
    patient_id: str | None = None,
    clinician_id: str | None = None,
    purpose: str | None = None,
    hospital_id: str | None = None,
    session_binding: str | None = None,
) -> ConsentCapability | None:
    """Validate a live capability without consuming it. Fails closed.

    Any of ``patient_id``, ``clinician_id``, or ``purpose`` may be
    ``None`` to skip that constraint. V2 routes always pass all three;
    v1 self-consent routes discover the patient_id from the token itself.
    """

    try:
        redis_client = get_consent_redis_client()
        raw_value = await redis_client.get(_token_key(token))
    except Exception as exc:
        raise ConsentEngineUnavailable("Consent store is unavailable.") from exc

    capability = _parse_payload(raw_value)
    if capability is None or not _matches(
        capability, patient_id, clinician_id, purpose, hospital_id, session_binding
    ):
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
    token_hash = _token_hash(token)
    await append_audit_log(
        actor_uid="SYSTEM_CONSENT",
        event_type="BREAK_GLASS_REVOKE_ATTEMPT",
        target_id=token_hash,
        status="STARTED",
        metadata={"reason": reason},
    )

    try:
        redis_client = get_consent_redis_client()
        await redis_client.delete(_token_key(token))
    except Exception as exc:
        logger.error(
            "Live break-glass capability revocation failed; durable revocation will continue",
            extra={"error_type": type(exc).__name__},
        )

    try:
        stmt = select(ConsentGrantLog).where(ConsentGrantLog.token_hash == token_hash)
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None and row.revoked_at is None and row.consumed_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            row.revoked_reason = reason
            await db.commit()

        await append_audit_log(
            actor_uid="SYSTEM_CONSENT",
            event_type="BREAK_GLASS_REVOKE_SUCCESS",
            target_id=token_hash,
            status="SUCCESS",
        )
    except Exception:
        await db.rollback()
