"""Atomic OTP-attempt to registration-repair capability transition.

The recovery identity proof and the repair capability are different authorities.
For a repairable graph, production Redis must consume the exact claimed OTP
attempt and mint the one-time repair capability at one linearization point.
That prevents an upstream/Redis failure from leaving the attempt consumed before
repair authority exists, and prevents two verifiers from minting capabilities
for the same verified identity proof.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from app.core.redis import get_async_redis_client
from app.services.patient_registration_recovery_authority import (
    REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS,
    REGISTRATION_RECOVERY_OPERATION,
    RegistrationRecoveryAttemptClaim,
    RegistrationRecoveryAttemptError,
    RegistrationRecoveryAuthorityUnavailable,
    RegistrationRecoveryCapability,
    RegistrationRecoveryCapabilityError,
    _PROVIDER,
    _SCOPE,
    _attempt_key,
    _capability_key,
    _capability_slot_key,
    _decode,
    _maybe_await,
    _phone_digest,
    _sha256,
)


async def exchange_registration_recovery_attempt_for_capability(
    *,
    attempt_token: str,
    phone: str,
    claim: RegistrationRecoveryAttemptClaim,
    patient_id: str,
    provider_subject: str,
    repair_kind: str,
    graph_fingerprint: str,
) -> RegistrationRecoveryCapability:
    """Atomically exchange one verified attempt claim for exact repair authority.

    Production Redis uses Lua across the claimed attempt, provider-subject slot,
    capability row, and attempt deletion. Test/local Redis doubles without
    ``eval`` mirror the semantics best-effort; real Redis qualification covers
    the atomic branch.
    """

    if not attempt_token or len(attempt_token) > 512:
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_INVALID")

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
    raw_capability = json.dumps(record, sort_keys=True, separators=(",", ":"))
    redis = get_async_redis_client()
    attempt_key = _attempt_key(attempt_token)
    capability_key = _capability_key(token)
    slot_key = _capability_slot_key(provider_subject)
    phone_digest = _phone_digest(phone)

    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return {'invalid_attempt'} end
    local ok, state = pcall(cjson.decode, raw)
    if not ok or state.version ~= 1 or state.scope ~= ARGV[1]
       or state.provider ~= ARGV[2] or state.phone_digest ~= ARGV[3]
       or state.attempt_id ~= ARGV[4] or state.state ~= 'verifying'
       or state.claim_id ~= ARGV[5] then
        return {'invalid_attempt'}
    end
    if redis.call('EXISTS', KEYS[3]) == 1 then return {'slot_busy'} end
    local ttl = redis.call('TTL', KEYS[1])
    if ttl <= 0 then return {'invalid_attempt'} end
    redis.call('SETEX', KEYS[2], ARGV[6], ARGV[7])
    redis.call('SETEX', KEYS[3], ARGV[6], ARGV[8])
    redis.call('DEL', KEYS[1])
    return {'ok'}
    """

    try:
        if hasattr(redis, "eval"):
            result = await _maybe_await(
                redis.eval(
                    script,
                    3,
                    attempt_key,
                    capability_key,
                    slot_key,
                    _SCOPE,
                    _PROVIDER,
                    phone_digest,
                    claim.attempt_id,
                    claim.claim_id,
                    REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS,
                    raw_capability,
                    digest,
                )
            )
            result = [item.decode() if isinstance(item, bytes) else item for item in result]
            outcome = str(result[0]) if result else "invalid_attempt"
        else:
            raw_attempt = await _maybe_await(redis.get(attempt_key))
            state = _decode(raw_attempt)
            if (
                state is None
                or state.get("version") != 1
                or state.get("scope") != _SCOPE
                or state.get("provider") != _PROVIDER
                or state.get("phone_digest") != phone_digest
                or state.get("attempt_id") != claim.attempt_id
                or state.get("state") != "verifying"
                or state.get("claim_id") != claim.claim_id
            ):
                outcome = "invalid_attempt"
            elif await _maybe_await(redis.get(slot_key)):
                outcome = "slot_busy"
            else:
                await _maybe_await(
                    redis.setex(
                        capability_key,
                        REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS,
                        raw_capability,
                    )
                )
                try:
                    await _maybe_await(
                        redis.setex(
                            slot_key,
                            REGISTRATION_RECOVERY_CAPABILITY_TTL_SECONDS,
                            digest,
                        )
                    )
                    deleted = await _maybe_await(redis.delete(attempt_key))
                    if not deleted:
                        await _maybe_await(redis.delete(capability_key, slot_key))
                        outcome = "invalid_attempt"
                    else:
                        outcome = "ok"
                except Exception:
                    await _maybe_await(redis.delete(capability_key, slot_key))
                    raise
    except (RegistrationRecoveryAttemptError, RegistrationRecoveryCapabilityError):
        raise
    except Exception as exc:
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery authority transition is unavailable"
        ) from exc

    if outcome == "invalid_attempt":
        raise RegistrationRecoveryAttemptError("REGISTRATION_RECOVERY_ATTEMPT_INVALID")
    if outcome == "slot_busy":
        raise RegistrationRecoveryCapabilityError(
            "REGISTRATION_RECOVERY_CAPABILITY_ALREADY_ISSUED"
        )
    if outcome != "ok":
        raise RegistrationRecoveryAuthorityUnavailable(
            "Registration recovery authority transition returned an invalid state"
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
