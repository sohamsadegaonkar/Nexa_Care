"""Short-lived server-side capabilities for patient registration OTP flows.

The opaque value returned to a registration client is deliberately not an
authentication credential.  It is only a five-minute continuity proof for a
single registration attempt, and its Redis record never contains a plaintext
phone number, OTP, provider access token, or the opaque value itself.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.config import get_otp_rate_limit_config
from app.core.redis import get_async_redis_client


# This matches the existing patient OTP limiter window and device-enrollment
# capability lifetime.  The registration capability is never renewable.
REGISTRATION_ATTEMPT_TTL_SECONDS = 5 * 60
_CLAIM_TTL_SECONDS = 60
_PREFIX = "nexa:patient_registration_attempt:"
_SCOPE = "patient_registration"
_PROVIDER = "supabase"


class RegistrationAttemptError(RuntimeError):
    """Stable, value-free errors for registration continuity state."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RegistrationAttemptClaim:
    attempt_id: str
    claim_id: str | None
    finalized_patient_id: str | None = None

    @property
    def finalized(self) -> bool:
        return self.finalized_patient_id is not None


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _token_key(token: str) -> str:
    return _PREFIX + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _phone_digest(phone: str) -> str:
    """Use the established independent OTP HMAC key, never plaintext phone."""
    secret = get_otp_rate_limit_config().hmac_secret.encode("utf-8")
    return hmac.new(secret, phone.encode("utf-8"), hashlib.sha256).hexdigest()


def _decode(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _valid_state(state: dict[str, Any], phone: str) -> bool:
    return (
        state.get("version") == 1
        and state.get("scope") == _SCOPE
        and state.get("provider") == _PROVIDER
        and hmac.compare_digest(
            str(state.get("phone_digest", "")), _phone_digest(phone)
        )
        and isinstance(state.get("attempt_id"), str)
    )


async def issue_registration_attempt(phone: str) -> str:
    """Create one bounded registration-intent capability after OTP initiation."""
    try:
        token = secrets.token_urlsafe(32)
        state = {
            "version": 1,
            "attempt_id": uuid.uuid4().hex,
            "scope": _SCOPE,
            "provider": _PROVIDER,
            "phone_digest": _phone_digest(phone),
            "state": "pending",
        }
        written = await _maybe_await(
            get_async_redis_client().set(
                _token_key(token),
                json.dumps(state, sort_keys=True, separators=(",", ":")),
                nx=True,
                ex=REGISTRATION_ATTEMPT_TTL_SECONDS,
            )
        )
    except Exception as exc:
        raise RegistrationAttemptError("REGISTRATION_ATTEMPT_UNAVAILABLE") from exc
    if not written:
        # A cryptographic collision is extraordinarily unlikely, but issuing a
        # capability whose state was not durably written is never safe.
        raise RegistrationAttemptError("REGISTRATION_ATTEMPT_UNAVAILABLE")
    return token


async def claim_registration_attempt(
    token: str, phone: str
) -> RegistrationAttemptClaim:
    """Atomically claim a pending attempt or return its bounded finalization.

    The Lua path is the production path.  The guarded fallback exists only for
    narrow in-process fakes that do not implement ``eval``.
    """
    if not token or len(token) > 512:
        raise RegistrationAttemptError("REGISTRATION_ATTEMPT_INVALID")
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return {'invalid'} end
    local state = cjson.decode(raw)
    if state.version ~= 1 or state.scope ~= ARGV[1] or state.provider ~= ARGV[2]
       or state.phone_digest ~= ARGV[3] or not state.attempt_id then
        return {'invalid'}
    end
    if state.state == 'finalized' and state.patient_id then
        return {'finalized', state.attempt_id, state.patient_id}
    end
    local now = tonumber(redis.call('TIME')[1])
    if state.state == 'pending' or (state.state == 'verifying' and
       tonumber(state.claim_until or 0) <= now) then
        local ttl = redis.call('TTL', KEYS[1])
        if ttl <= 0 then return {'invalid'} end
        state.state = 'verifying'
        state.claim_id = ARGV[4]
        state.claim_until = now + tonumber(ARGV[5])
        redis.call('SET', KEYS[1], cjson.encode(state), 'XX', 'EX', ttl)
        return {'claimed', state.attempt_id}
    end
    return {'in_progress'}
    """
    try:
        redis = get_async_redis_client()
        key = _token_key(token)
        claim_id = secrets.token_urlsafe(24)
        phone_digest = _phone_digest(phone)
        if hasattr(redis, "eval"):
            result = await _maybe_await(
                redis.eval(
                    script,
                    1,
                    key,
                    _SCOPE,
                    _PROVIDER,
                    phone_digest,
                    claim_id,
                    _CLAIM_TTL_SECONDS,
                )
            )
            result = [
                item.decode() if isinstance(item, bytes) else item for item in result
            ]
        else:
            raw = await _maybe_await(redis.get(key))
            state = _decode(raw)
            if state is None or not _valid_state(state, phone):
                result = ["invalid"]
            elif state.get("state") == "finalized" and state.get("patient_id"):
                result = ["finalized", state["attempt_id"], state["patient_id"]]
            elif state.get("state") == "pending" or (
                state.get("state") == "verifying"
                and int(state.get("claim_until", 0)) <= int(time.time())
            ):
                ttl = int(await _maybe_await(redis.ttl(key)))
                if ttl <= 0:
                    result = ["invalid"]
                else:
                    state.update(
                        state="verifying",
                        claim_id=claim_id,
                        claim_until=int(time.time()) + _CLAIM_TTL_SECONDS,
                    )
                    await _maybe_await(
                        redis.set(
                            key, json.dumps(state, sort_keys=True), xx=True, ex=ttl
                        )
                    )
                    result = ["claimed", state["attempt_id"]]
            else:
                result = ["in_progress"]
    except RegistrationAttemptError:
        raise
    except Exception as exc:
        raise RegistrationAttemptError("REGISTRATION_ATTEMPT_UNAVAILABLE") from exc

    if not result or result[0] == "invalid":
        raise RegistrationAttemptError("REGISTRATION_ATTEMPT_INVALID")
    if result[0] == "in_progress":
        raise RegistrationAttemptError("REGISTRATION_ATTEMPT_IN_PROGRESS")
    if result[0] == "finalized" and len(result) == 3:
        return RegistrationAttemptClaim(
            attempt_id=str(result[1]),
            claim_id=None,
            finalized_patient_id=str(result[2]),
        )
    if result[0] == "claimed" and len(result) == 2:
        return RegistrationAttemptClaim(attempt_id=str(result[1]), claim_id=claim_id)
    raise RegistrationAttemptError("REGISTRATION_ATTEMPT_INVALID")


async def release_registration_attempt_claim(
    token: str, phone: str, claim: RegistrationAttemptClaim
) -> None:
    """Return only this worker's failed pre-commit claim to ``pending``."""
    if claim.finalized or not claim.claim_id:
        return
    redis = get_async_redis_client()
    key = _token_key(token)
    try:
        raw = await _maybe_await(redis.get(key))
        state = _decode(raw)
        if state is None or not _valid_state(state, phone):
            return
        if state.get("state") != "verifying" or state.get("claim_id") != claim.claim_id:
            return
        ttl = int(await _maybe_await(redis.ttl(key)))
        if ttl <= 0:
            return
        state.pop("claim_id", None)
        state.pop("claim_until", None)
        state["state"] = "pending"
        await _maybe_await(
            redis.set(key, json.dumps(state, sort_keys=True), xx=True, ex=ttl)
        )
    except Exception:
        # A failed release leaves a short claim lease.  It never authorizes a
        # login and becomes claimable only after the bounded lease expires.
        return


async def finalize_registration_attempt(
    token: str, phone: str, claim: RegistrationAttemptClaim, patient_id: str
) -> None:
    """Mark the same capability finalized after the DB transaction commits.

    If this Redis write fails after commit, the durable outbox idempotency key
    still proves the account was created by this attempt once the short claim
    lease expires; no existing account can be adopted by another attempt.
    """
    if claim.finalized or not claim.claim_id:
        return
    redis = get_async_redis_client()
    key = _token_key(token)
    try:
        raw = await _maybe_await(redis.get(key))
        state = _decode(raw)
        if (
            state is None
            or not _valid_state(state, phone)
            or state.get("state") != "verifying"
            or state.get("claim_id") != claim.claim_id
        ):
            raise RegistrationAttemptError("REGISTRATION_ATTEMPT_INVALID")
        ttl = int(await _maybe_await(redis.ttl(key)))
        if ttl <= 0:
            raise RegistrationAttemptError("REGISTRATION_ATTEMPT_INVALID")
        state.pop("claim_id", None)
        state.pop("claim_until", None)
        state["state"] = "finalized"
        state["patient_id"] = str(patient_id)
        written = await _maybe_await(
            redis.set(key, json.dumps(state, sort_keys=True), xx=True, ex=ttl)
        )
        if not written:
            raise RegistrationAttemptError("REGISTRATION_ATTEMPT_UNAVAILABLE")
    except RegistrationAttemptError:
        raise
    except Exception as exc:
        raise RegistrationAttemptError("REGISTRATION_ATTEMPT_UNAVAILABLE") from exc
