"""Real Redis qualification for OTP-attempt to registration-repair authority exchange."""

from __future__ import annotations

import asyncio
import os

import pytest

from app.core.redis import get_async_redis_client
from app.services.patient_registration_recovery_authority import (
    RegistrationRecoveryAttemptError,
    RegistrationRecoveryCapabilityError,
    claim_registration_recovery_attempt,
    consume_registration_recovery_capability,
    issue_registration_recovery_attempt,
)
from app.services.patient_registration_recovery_transition import (
    exchange_registration_recovery_attempt_for_capability,
)

pytestmark = [pytest.mark.redis, pytest.mark.asyncio]


def _require_local_redis() -> str:
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    if not (url.startswith("redis://127.0.0.1") or url.startswith("redis://localhost")):
        pytest.fail("TEST_REDIS_URL must be loopback-only")
    return url


async def _clear_recovery_keys(redis) -> None:
    keys = []
    async for key in redis.scan_iter(match="nexa:patient_registration_recovery_*"):
        keys.append(key)
    if keys:
        await redis.delete(*keys)


@pytest.fixture(autouse=True)
async def _redis_recovery_namespace(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPSTASH_REDIS_URL", _require_local_redis())
    monkeypatch.setenv("OTP_RATE_LIMIT_HMAC_SECRET", "r" * 32)
    get_async_redis_client.cache_clear()
    redis = get_async_redis_client()
    await _clear_recovery_keys(redis)
    try:
        yield redis
    finally:
        await _clear_recovery_keys(redis)
        close = getattr(redis, "aclose", None) or redis.close
        result = close()
        if result is not None:
            await result
        get_async_redis_client.cache_clear()


async def _exchange(*, attempt_token: str, phone: str, claim, subject: str):
    return await exchange_registration_recovery_attempt_for_capability(
        attempt_token=attempt_token,
        phone=phone,
        claim=claim,
        patient_id="11111111-1111-4111-8111-111111111111",
        provider_subject=subject,
        repair_kind="restore_patient_record_anchor",
        graph_fingerprint="a" * 64,
    )


async def test_exchange_is_single_winner_and_capability_is_one_time() -> None:
    phone = "+918000000001"
    subject = "supabase-recovery-subject-single-winner"
    attempt_token = await issue_registration_recovery_attempt(phone)
    claim = await claim_registration_recovery_attempt(attempt_token, phone)

    first, second = await asyncio.gather(
        _exchange(
            attempt_token=attempt_token,
            phone=phone,
            claim=claim,
            subject=subject,
        ),
        _exchange(
            attempt_token=attempt_token,
            phone=phone,
            claim=claim,
            subject=subject,
        ),
        return_exceptions=True,
    )

    capabilities = [item for item in (first, second) if not isinstance(item, Exception)]
    failures = [item for item in (first, second) if isinstance(item, Exception)]
    assert len(capabilities) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RegistrationRecoveryAttemptError)
    assert failures[0].code == "REGISTRATION_RECOVERY_ATTEMPT_INVALID"

    consumed = await consume_registration_recovery_capability(capabilities[0].token)
    assert consumed.provider_subject == subject
    with pytest.raises(RegistrationRecoveryCapabilityError) as replay:
        await consume_registration_recovery_capability(capabilities[0].token)
    assert replay.value.code == "REGISTRATION_RECOVERY_CAPABILITY_INVALID"


async def test_busy_subject_slot_does_not_consume_second_verified_attempt() -> None:
    phone = "+918000000001"
    subject = "supabase-recovery-subject-busy-slot"

    first_attempt = await issue_registration_recovery_attempt(phone)
    first_claim = await claim_registration_recovery_attempt(first_attempt, phone)
    first_capability = await _exchange(
        attempt_token=first_attempt,
        phone=phone,
        claim=first_claim,
        subject=subject,
    )

    second_attempt = await issue_registration_recovery_attempt(phone)
    second_claim = await claim_registration_recovery_attempt(second_attempt, phone)
    with pytest.raises(RegistrationRecoveryCapabilityError) as busy:
        await _exchange(
            attempt_token=second_attempt,
            phone=phone,
            claim=second_claim,
            subject=subject,
        )
    assert busy.value.code == "REGISTRATION_RECOVERY_CAPABILITY_ALREADY_ISSUED"

    # Freeing the first subject slot must allow the exact still-claimed second
    # verified attempt to exchange without asking the patient to prove identity again.
    await consume_registration_recovery_capability(first_capability.token)
    second_capability = await _exchange(
        attempt_token=second_attempt,
        phone=phone,
        claim=second_claim,
        subject=subject,
    )
    assert second_capability.provider_subject == subject
