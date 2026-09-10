"""Server-side authority for patient registration-account recovery.

Registration recovery deliberately separates three authorities:

* Supabase OTP proves fresh control of the external phone identity.
* ``registration_recovery_attempt_token`` only serializes OTP verification and
  bounds invalid guesses. It is not login, patient-session, device, or repair
  authority.
* ``registration_recovery_token`` is minted only after verified identity and
  server-side graph classification. It is one-time repair authority bound to the
  exact provider subject, patient, repair plan, and observed graph fingerprint.

No plaintext phone number, OTP, provider token, patient private key, or raw
recovery token is stored in Redis.
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
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_otp_rate_limit_config
from app.core.redis import get_async_redis_client


REGISTRATION_RECOVERY_ATTEMPT_TTL_SECONDS = 5 * 60
REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS = 5 * 60
REGISTRATION_RECOVERY_MAX_INVALID_OTPS = 5
REGISTRATION_RECOVERY_OPERATION = "repair_patient_registration_account"

_ATTEMPT_PREFIX = "nexa:patient_registration_recovery_attempt:"
_CAPABILITY_PREFIX = "nexa:patient_registration_recovery_capability:"
_CAPABILITY_SLOT_PREFIX = "nexa:patient_registration_recovery_slot:"
_SCOPE = "patient_registration_recovery"
_PROVIDER = "supabase"
_CLAIM_TTL_SECONDS = 60


class RegistrationRecoveryAuthorityUnavailable(RuntimeError):
    """Redis-backed recovery authority could not be proven safely."""


class RegistrationRecoveryAttemptError(ValueError):
    """Stable, non-secret recovery-attempt denial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RegistrationRecoveryCapabilityError(ValueError):
    """Stable, non-secret repair-capability denial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryAttemptClaim:
    attempt_id: str
    claim_id: str


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryCapability:
    token: str
    patient_id: str
    provider_subject: str
    repair_kind: str
    graph_fingerprint: str
    issued_at: str
    expires_at: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _attempt_key(token: str) -> str:
    return _ATTEMPT_PREFIX + _sha256(token)


def _capability_key(token: str) -> str:
    return _CAPABILITY_PREFIX + _sha256(token)


def _capability_slot_key(provider_subject: str) -> str:
    return _CAPABILITY_SLOT_PREFIX + _sha256(f"{_PROVIDER}:{provider_subject}")


def _phone_digest(phone: str) -> str:
    secret = get_otp_rate_limit_config().hmac_secret.encode("utf-8")
    return hmac.new(secret, phone.encode("utf-8"), hashlib.sha256).hexdigest()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _decode(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


async def issue_registration_recovery_attempt(phone: str) -> str:
    """Create bounded continuity state after neutral recovery-OTP initiation."""

    token = secrets.token_urlsafe(32)
    state = {
        "version": 1,
        "scope": _SCOPE,
        "provider": _PROVIDER,
        "attempt_id": uuid.uuid4().hex,
        "phone_digest": _phone_digest(phone),
        "state": "pending",
        "invalid_otp_count": 0,
    }
    try:
        written = await _maybe_await(
            get_async_redis_client().set(
                _attempt_key(token),
                json.dumps(state, sort_keys=True, separators=(",", ":")),
                nx=True,
                ex=REGISTRATION_RECOVERY_ATTEMPT_TTL_SECONDS,
            )
        )
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery attempt store is unavailable"
        ) from exc
    if not written:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery attempt could not be allocated"
        )
    return token


async def claim_registration_recovery_attempt(
    token: str, phone: str
) -> RegistrationRecoveryAttemptClaim:
    """Atomically serialize one OTP verifier without charging its guess budget."""

    if not token or len(token) > 512:
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_INVALID")
    claim_id = secrets.token_urlsafe(24)
    key = _attempt_key(token)
    phone_digest = _phone_digest(phone)
    redis = get_async_redis_client()
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return {'invalid'} end
    local ok, state = pcall(cjson.decode, raw)
    if not ok or state.version ~= 1 or state.scope ~= ARGV[1]
       or state.provider ~= ARGV[2] or state.phone_digest ~= ARGV[3]
       or not state.attempt_id then
        return {'invalid'}
    end
    if state.state == 'exhausted' then return {'exhausted'} end
    local now = tonumber(redis.call('TIME')[1])
    if state.state == 'pending' or (state.state == 'verifying' and
       tonumber(state.claim_until or 0) <= now) then
        local count = tonumber(state.invalid_otp_count or 0)
        if not count or count < 0 then return {'invalid'} end
        if count >= tonumber(ARGV[6]) then
            state.state = 'exhausted'
            state.claim_id = nil
            state.claim_until = nil
            local ttl = redis.call('TTL', KEYS[1])
            if ttl > 0 then redis.call('SET', KEYS[1], cjson.encode(state), 'XX', 'EX', ttl) end
            return {'exhausted'}
        end
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
                    REGISTRATION_RECOVERY_MAX_INVALID_OTPS,
                )
            )
            result = [item.decode() if isinstance(item, bytes) else item for item in result]
        else:
            raw = await _maybe_await(redis.get(key))
            state = _decode(raw)
            if (
                state is None
                or state.get("version") != 1
                or state.get("scope") != _SCOPE
                or state.get("provider") != _PROVIDER
                or not hmac.compare_digest(
                    str(state.get("phone_digest", "")), phone_digest
                )
                or not isinstance(state.get("attempt_id"), str)
            ):
                result = ["invalid"]
            elif state.get("state") == "exhausted":
                result = ["exhausted"]
            elif state.get("state") == "pending" or (
                state.get("state") == "verifying"
                and int(state.get("claim_until", 0)) <= int(time.time())
            ):
                count = int(state.get("invalid_otp_count", 0))
                ttl = int(await _maybe_await(redis.ttl(key)))
                if ttl <= 0:
                    result = ["invalid"]
                elif count >= REGISTRATION_RECOVERY_MAX_INVALID_OTPS:
                    state.pop("claim_id", None)
                    state.pop("claim_until", None)
                    state["state"] = "exhausted"
                    await _maybe_await(
                        redis.set(key, json.dumps(state, sort_keys=True), xx=True, ex=ttl)
                    )
                    result = ["exhausted"]
                else:
                    state.update(
                        state="verifying",
                        claim_id=claim_id,
                        claim_until=int(time.time()) + _CLAIM_TTL_SECONDS,
                    )
                    await _maybe_await(
                        redis.set(key, json.dumps(state, sort_keys=True), xx=True, ex=ttl)
                    )
                    result = ["claimed", state["attempt_id"]]
            else:
                result = ["in_progress"]
    except RegistrationRecoveryAttemptError:
        raise
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery attempt store is unavailable"
        ) from exc

    if not result or result[0] == "invalid":
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_INVALID")
    if result[0] == "exhausted":
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_EXHAUSTED")
    if result[0] == "in_progress":
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_IN_PROGRESS")
    if result[0] == "claimed" and len(result) == 2:
        return RegistrationRecoveryAttemptClaim(str(result[1]), claim_id)
    raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_INVALID")


async def record_registration_recovery_invalid_otp(
    token: str,
    phone: str,
    claim: RegistrationRecoveryAttemptClaim,
) -> None:
    """Charge exactly one provider-confirmed invalid OTP and release the claim."""

    key = _attempt_key(token)
    phone_digest = _phone_digest(phone)
    redis = get_async_redis_client()
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return 0 end
    local ok, state = pcall(cjson.decode, raw)
    if not ok or state.version ~= 1 or state.scope ~= ARGV[1]
       or state.provider ~= ARGV[2] or state.phone_digest ~= ARGV[3]
       or state.attempt_id ~= ARGV[5] or state.state ~= 'verifying'
       or state.claim_id ~= ARGV[4] then
        return 0
    end
    local count = tonumber(state.invalid_otp_count or 0)
    if not count or count < 0 then return 0 end
    local ttl = redis.call('TTL', KEYS[1])
    if ttl <= 0 then return 0 end
    count = count + 1
    state.invalid_otp_count = count
    state.claim_id = nil
    state.claim_until = nil
    if count >= tonumber(ARGV[6]) then state.state = 'exhausted' else state.state = 'pending' end
    redis.call('SET', KEYS[1], cjson.encode(state), 'XX', 'EX', ttl)
    return 1
    """
    try:
        if hasattr(redis, "eval"):
            recorded = await _maybe_await(
                redis.eval(
                    script,
                    1,
                    key,
                    _SCOPE,
                    _PROVIDER,
                    phone_digest,
                    claim.claim_id,
                    claim.attempt_id,
                    REGISTRATION_RECOVERY_MAX_INVALID_OTPS,
                )
            )
        else:
            raw = await _maybe_await(redis.get(key))
            state = _decode(raw)
            ttl = int(await _maybe_await(redis.ttl(key))) if state else -1
            if (
                state is None
                or ttl <= 0
                or state.get("version") != 1
                or state.get("scope") != _SCOPE
                or state.get("provider") != _PROVIDER
                or not hmac.compare_digest(
                    str(state.get("phone_digest", "")), phone_digest
                )
                or state.get("attempt_id") != claim.attempt_id
                or state.get("state") != "verifying"
                or state.get("claim_id") != claim.claim_id
            ):
                recorded = 0
            else:
                count = int(state.get("invalid_otp_count", 0)) + 1
                state["invalid_otp_count"] = count
                state.pop("claim_id", None)
                state.pop("claim_until", None)
                state["state"] = (
                    "exhausted"
                    if count >= REGISTRATION_RECOVERY_MAX_INVALID_OTPS
                    else "pending"
                )
                recorded = await _maybe_await(
                    redis.set(key, json.dumps(state, sort_keys=True), xx=True, ex=ttl)
                )
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery attempt store is unavailable"
        ) from exc
    if not recorded:
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_INVALID")


async def release_registration_recovery_claim(
    token: str,
    phone: str,
    claim: RegistrationRecoveryAttemptClaim,
) -> None:
    """Release the exact verifier claim without replenishing invalid-OTP budget."""

    key = _attempt_key(token)
    phone_digest = _phone_digest(phone)
    redis = get_async_redis_client()
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return 0 end
    local ok, state = pcall(cjson.decode, raw)
    if not ok or state.version ~= 1 or state.scope ~= ARGV[1]
       or state.provider ~= ARGV[2] or state.phone_digest ~= ARGV[3]
       or state.attempt_id ~= ARGV[5] or state.state ~= 'verifying'
       or state.claim_id ~= ARGV[4] then
        return 0
    end
    local ttl = redis.call('TTL', KEYS[1])
    if ttl <= 0 then return 0 end
    state.state = 'pending'
    state.claim_id = nil
    state.claim_until = nil
    redis.call('SET', KEYS[1], cjson.encode(state), 'XX', 'EX', ttl)
    return 1
    """
    try:
        if hasattr(redis, "eval"):
            await _maybe_await(
                redis.eval(
                    script,
                    1,
                    key,
                    _SCOPE,
                    _PROVIDER,
                    phone_digest,
                    claim.claim_id,
                    claim.attempt_id,
                )
            )
        else:
            raw = await _maybe_await(redis.get(key))
            state = _decode(raw)
            if (
                state
                and state.get("version") == 1
                and state.get("scope") == _SCOPE
                and state.get("provider") == _PROVIDER
                and hmac.compare_digest(
                    str(state.get("phone_digest", "")), phone_digest
                )
                and state.get("attempt_id") == claim.attempt_id
                and state.get("state") == "verifying"
                and state.get("claim_id") == claim.claim_id
            ):
                ttl = int(await _maybe_await(redis.ttl(key)))
                if ttl > 0:
                    state["state"] = "pending"
                    state.pop("claim_id", None)
                    state.pop("claim_until", None)
                    await _maybe_await(
                        redis.set(key, json.dumps(state, sort_keys=True), xx=True, ex=ttl)
                    )
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery attempt store is unavailable"
        ) from exc


async def consume_registration_recovery_attempt(
    token: str,
    phone: str,
    claim: RegistrationRecoveryAttemptClaim,
) -> None:
    """Consume exact verified attempt after repair capability has been durably issued."""

    key = _attempt_key(token)
    phone_digest = _phone_digest(phone)
    redis = get_async_redis_client()
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return 0 end
    local ok, state = pcall(cjson.decode, raw)
    if not ok or state.version ~= 1 or state.scope ~= ARGV[1]
       or state.provider ~= ARGV[2] or state.phone_digest ~= ARGV[3]
       or state.attempt_id ~= ARGV[5] or state.state ~= 'verifying'
       or state.claim_id ~= ARGV[4] then
        return 0
    end
    redis.call('DEL', KEYS[1])
    return 1
    """
    try:
        if hasattr(redis, "eval"):
            consumed = await _maybe_await(
                redis.eval(
                    script,
                    1,
                    key,
                    _SCOPE,
                    _PROVIDER,
                    phone_digest,
                    claim.claim_id,
                    claim.attempt_id,
                )
            )
        else:
            raw = await _maybe_await(redis.get(key))
            state = _decode(raw)
            consumed = bool(
                state
                and state.get("version") == 1
                and state.get("scope") == _SCOPE
                and state.get("provider") == _PROVIDER
                and hmac.compare_digest(
                    str(state.get("phone_digest", "")), phone_digest
                )
                and state.get("attempt_id") == claim.attempt_id
                and state.get("state") == "verifying"
                and state.get("claim_id") == claim.claim_id
            )
            if consumed:
                await _maybe_await(redis.delete(key))
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery attempt store is unavailable"
        ) from exc
    if not consumed:
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_INVALID")


async def issue_registration_recovery_capability(
    *,
    patient_id: str,
    provider_subject: str,
    repair_kind: str,
    graph_fingerprint: str,
) -> RegistrationRecoveryCapability:
    """Mint one exact one-time repair authority for a verified recoverable graph."""

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS)
    token = secrets.token_urlsafe(32)
    digest = _sha256(token)
    record = {
        "version": 1,
        "scope": _SCOPE,
        "operation": REGISTRATION_RECOVERY_OPERATION,
        "patient_id": patient_id,
        "provider": _PROVIDER,
        "provider_subject": provider_subject,
        "repair_kind": repair_kind,
        "graph_fingerprint": graph_fingerprint,
        "token_digest": digest,
        "issued_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }
    redis = get_async_redis_client()
    key = _capability_key(token)
    slot = _capability_slot_key(provider_subject)
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"))
    script = """
    if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
    redis.call('SETEX', KEYS[1], ARGV[1], ARGV[2])
    redis.call('SETEX', KEYS[2], ARGV[1], ARGV[3])
    return 1
    """
    try:
        if hasattr(redis, "eval"):
            stored = await _maybe_await(
                redis.eval(
                    script,
                    2,
                    key,
                    slot,
                    REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS,
                    raw,
                    digest,
                )
            )
        else:
            if await _maybe_await(redis.get(slot)):
                stored = 0
            else:
                await _maybe_await(
                    redis.setex(key, REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS, raw)
                )
                await _maybe_await(
                    redis.setex(slot, REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS, digest)
                )
                stored = 1
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery capability store is unavailable"
        ) from exc
    if not stored:
        raise RegistrationRecoveryCapabilityError(
            "REGISTRATION_RECOVERY_CAPABILITY_ALREADY_ISSUED"
        )
    return RegistrationRecoveryCapability(
        token=token,
        patient_id=patient_id,
        provider_subject=provider_subject,
        repair_kind=repair_kind,
        graph_fingerprint=graph_fingerprint,
        issued_at=now.isoformat(),
        expires_at=expires.isoformat(),
    )


async def consume_registration_recovery_capability(
    token: str,
) -> RegistrationRecoveryCapability:
    """Atomically consume a valid one-time repair capability and its subject slot."""

    if not token or len(token) > 512:
        raise RegistrationRecoveryCapabilityError(
            "REGISTRATION_RECOVERY_CAPABILITY_INVALID"
        )
    redis = get_async_redis_client()
    key = _capability_key(token)
    digest = _sha256(token)
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return false end
    local ok, state = pcall(cjson.decode, raw)
    if not ok or state.version ~= 1 or state.scope ~= ARGV[1]
       or state.operation ~= ARGV[2] or state.provider ~= ARGV[3]
       or state.token_digest ~= ARGV[4] or not state.provider_subject then
        return false
    end
    local slot = ARGV[5] .. redis.sha1hex(ARGV[3] .. ':' .. state.provider_subject)
    if redis.call('GET', slot) ~= ARGV[4] then return false end
    redis.call('DEL', KEYS[1], slot)
    return raw
    """
    try:
        if hasattr(redis, "eval"):
            # Lua's SHA1 slot derivation intentionally differs from Python's SHA256
            # prefix, so production uses an explicit two-step read to derive the
            # exact slot and then one Lua linearization point for validation/deletion.
            raw = await _maybe_await(redis.get(key))
            state = _decode(raw)
            if state is None or not isinstance(state.get("provider_subject"), str):
                raise RegistrationRecoveryCapabilityError(
                    "REGISTRATION_RECOVERY_CAPABILITY_INVALID"
                )
            slot = _capability_slot_key(str(state["provider_subject"]))
            script = """
            local raw = redis.call('GET', KEYS[1])
            if not raw then return false end
            local ok, state = pcall(cjson.decode, raw)
            if not ok or state.version ~= 1 or state.scope ~= ARGV[1]
               or state.operation ~= ARGV[2] or state.provider ~= ARGV[3]
               or state.token_digest ~= ARGV[4] then
                return false
            end
            if redis.call('GET', KEYS[2]) ~= ARGV[4] then return false end
            redis.call('DEL', KEYS[1], KEYS[2])
            return raw
            """
            raw = await _maybe_await(
                redis.eval(
                    script,
                    2,
                    key,
                    slot,
                    _SCOPE,
                    REGISTRATION_RECOVERY_OPERATION,
                    _PROVIDER,
                    digest,
                )
            )
        else:
            raw = await _maybe_await(redis.get(key))
            state = _decode(raw)
            if state is None or not isinstance(state.get("provider_subject"), str):
                raw = None
            else:
                slot = _capability_slot_key(str(state["provider_subject"]))
                slot_value = await _maybe_await(redis.get(slot))
                if isinstance(slot_value, bytes):
                    slot_value = slot_value.decode("utf-8")
                if (
                    state.get("version") != 1
                    or state.get("scope") != _SCOPE
                    or state.get("operation") != REGISTRATION_RECOVERY_OPERATION
                    or state.get("provider") != _PROVIDER
                    or state.get("token_digest") != digest
                    or slot_value != digest
                ):
                    raw = None
                else:
                    await _maybe_await(redis.delete(key, slot))
    except RegistrationRecoveryCapabilityError:
        raise
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery capability store is unavailable"
        ) from exc

    state = _decode(raw)
    if state is None:
        raise RegistrationRecoveryCapabilityError(
            "REGISTRATION_RECOVERY_CAPABILITY_INVALID"
        )
    required = {
        "patient_id",
        "provider_subject",
        "repair_kind",
        "graph_fingerprint",
        "issued_at",
        "expires_at",
    }
    if any(not isinstance(state.get(field), str) for field in required):
        raise RegistrationRecoveryCapabilityError(
            "REGISTRATION_RECOVERY_CAPABILITY_INVALID"
        )
    try:
        expires_at = datetime.fromisoformat(str(state["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistrationRecoveryCapabilityError(
            "REGISTRATION_RECOVERY_CAPABILITY_INVALID"
        ) from exc
    if expires_at.tzinfo is None or datetime.now(timezone.utc) > expires_at:
        raise RegistrationRecoveryCapabilityError(
            "REGISTRATION_RECOVERY_CAPABILITY_EXPIRED"
        )
    return RegistrationRecoveryCapability(
        token=token,
        patient_id=str(state["patient_id"]),
        provider_subject=str(state["provider_subject"]),
        repair_kind=str(state["repair_kind"]),
        graph_fingerprint=str(state["graph_fingerprint"]),
        issued_at=str(state["issued_at"]),
        expires_at=str(state["expires_at"]),
    )
