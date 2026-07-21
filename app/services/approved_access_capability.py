"""Short-lived provider capabilities claimed from signed patient approvals."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.redis import get_async_redis_client


CAPABILITY_PREFIX = "consent_access:capability:"
CLAIM_PREFIX = "consent_access:claim:"
CLAIM_LOCK_PREFIX = "consent_access:claim-lock:"


class ApprovedAccessStoreUnavailable(RuntimeError):
    """Raised when the capability store cannot be safely read or written."""


class ApprovedAccessClaimInProgress(RuntimeError):
    """Raised when another worker is rotating this request's capability."""


@dataclass(frozen=True, slots=True)
class ApprovedAccessCapability:
    patient_id: str
    clinician_id: str
    hospital_id: str
    request_id: str
    purpose: str
    scope: list[str]
    is_break_glass: bool
    reason_code: str | None
    issued_at: str
    expires_at: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _capability_key(digest: str) -> str:
    return f"{CAPABILITY_PREFIX}{digest}"


def _claim_key(request_id: str) -> str:
    return f"{CLAIM_PREFIX}{request_id}"


def _decode(raw: object) -> dict | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _scope_allows(scope: str, requested_category: str) -> bool:
    clinical_reads = {"clinical_summary", "timeline_view"}
    if scope == "full":
        return requested_category in clinical_reads | {
            "full",
            "policy_read",
            "policy_update",
        }
    return scope == "clinical" and requested_category in clinical_reads


async def issue_from_approved_request(
    *, request_data: dict
) -> tuple[str, ApprovedAccessCapability]:
    """Rotate the request's capability so retries leave only one active grant."""
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(str(request_data["access_expires_at"]))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    ttl = int((expires_at - now).total_seconds())
    if ttl <= 0:
        raise ValueError("Approved access window has expired")

    token = secrets.token_urlsafe(48)
    digest = token_hash(token)
    scope = str(request_data["scope"])
    payload = {
        "request_id": str(request_data["request_id"]),
        "provider_id": str(request_data["provider_id"]),
        "hospital_id": str(request_data["hospital_id"]),
        "patient_id": str(request_data["patient_id"]),
        "purpose": str(request_data["purpose"]),
        "scope": scope,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }

    try:
        redis = get_async_redis_client()
        lock_key = f"{CLAIM_LOCK_PREFIX}{payload['request_id']}"
        lock_value = secrets.token_hex(16)
        if not await redis.set(lock_key, lock_value, nx=True, ex=5):
            raise ApprovedAccessClaimInProgress(
                "An access claim is already in progress"
            )
        try:
            prior_digest = await redis.get(_claim_key(payload["request_id"]))
            if isinstance(prior_digest, bytes):
                prior_digest = prior_digest.decode("utf-8")
            await redis.set(_capability_key(digest), json.dumps(payload), ex=ttl)
            await redis.set(_claim_key(payload["request_id"]), digest, ex=ttl)
            if isinstance(prior_digest, str) and prior_digest != digest:
                await redis.delete(_capability_key(prior_digest))
        finally:
            await redis.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end",
                1,
                lock_key,
                lock_value,
            )
    except ApprovedAccessClaimInProgress:
        raise
    except Exception as exc:
        raise ApprovedAccessStoreUnavailable(
            "Approved access store is unavailable"
        ) from exc

    return token, ApprovedAccessCapability(
        patient_id=payload["patient_id"],
        clinician_id=payload["provider_id"],
        hospital_id=payload["hospital_id"],
        request_id=payload["request_id"],
        purpose=payload["purpose"],
        scope=[scope],
        is_break_glass=False,
        reason_code=None,
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
    )


async def invalidate_request(request_id: str) -> None:
    try:
        redis = get_async_redis_client()
        digest = await redis.get(_claim_key(request_id))
        if isinstance(digest, bytes):
            digest = digest.decode("utf-8")
        if isinstance(digest, str):
            await redis.delete(_capability_key(digest))
        await redis.delete(_claim_key(request_id))
    except Exception as exc:
        raise ApprovedAccessStoreUnavailable(
            "Approved access store is unavailable"
        ) from exc


async def validate(
    *,
    token: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    requested_category: str,
) -> ApprovedAccessCapability | None:
    """Validate the hash-addressed grant and its live approved request."""
    try:
        redis = get_async_redis_client()
        digest = token_hash(token)
        payload = _decode(await redis.get(_capability_key(digest)))
        if payload is None:
            return None
        request_id = str(payload.get("request_id", ""))
        active_digest = await redis.get(_claim_key(request_id))
        if isinstance(active_digest, bytes):
            active_digest = active_digest.decode("utf-8")
        request_data = _decode(await redis.get(f"consent_request:{request_id}"))
    except Exception as exc:
        raise ApprovedAccessStoreUnavailable(
            "Approved access store is unavailable"
        ) from exc

    now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None

    expected = {
        "provider_id": provider_id,
        "hospital_id": hospital_id,
        "patient_id": patient_id,
    }
    if (
        active_digest != digest
        or request_data is None
        or request_data.get("status") != "approved"
    ):
        return None
    if now >= expires_at or any(
        str(payload.get(k)) != str(v) for k, v in expected.items()
    ):
        return None
    if any(
        str(request_data.get(k)) != str(payload.get(k))
        for k in (
            "request_id",
            "provider_id",
            "hospital_id",
            "patient_id",
            "purpose",
            "scope",
        )
    ):
        return None
    if not _scope_allows(str(payload.get("scope")), requested_category):
        return None

    return ApprovedAccessCapability(
        patient_id=patient_id,
        clinician_id=provider_id,
        hospital_id=hospital_id,
        request_id=request_id,
        purpose=str(payload["purpose"]),
        scope=[str(payload["scope"])],
        is_break_glass=False,
        reason_code=None,
        issued_at=str(payload["issued_at"]),
        expires_at=expires_at.isoformat(),
    )
