"""Real Redis qualification for patient registration-recovery authority."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.core.redis import get_async_redis_client
from app.services.patient_registration_recovery_authority import (
    REGISTRATION_RECOVERY_MAX_INVALID_OTPS,
    RegistrationRecoveryAttemptError,
    RegistrationRecoveryCapabilityError,
    _attempt_key,
    _capability_key,
    _capability_slot_key,
    claim_registration_recovery_attempt,
    consume_registration_recovery_capability,
    issue_registration_recovery_attempt,
    issue_registration_recovery_capability,
    record_registration_recovery_invalid_otp,
    release_registration_recovery_claim,
)


pytestmark = pytest.mark.redis


def _require_local_redis() -> str:
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    if not (url.startswith("redis://127.0.0.1") or url.startswith("redis://localhost")):
        pytest.fail("TEST_REDIS_URL must be loopback-only")
    return url


@pytest.mark.asyncio
async def test_recovery_attempt_real_redis_serializes_and_budgets_invalid_otps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _require_local_redis()
    monkeypatch.setenv("UPSTASH_REDIS_URL", url)
    monkeypatch.setenv("OTP_RATE_LIMIT_HMAC_SECRET", "r" * 32)
    get_async_redis_client.cache_clear()
    redis = get_async_redis_client()
    phone = "+918000000001"
    token = ""
    release_token = ""
    try:
        token = await issue_registration_recovery_attempt(phone)
        first, second = await asyncio.gather(
            claim_registration_recovery_attempt(token, phone),
            claim_registration_recovery_attempt(token, phone),
            return_exceptions=True,
        )
        claims = [item for item in (first, second) if not isinstance(item, Exception)]
        failures = [item for item in (first, second) if isinstance(item, Exception)]
        assert len(claims) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RegistrationRecoveryAttemptError)
        assert failures[0].code == "REGISTRATION_RECOVERY_ATTEMPT_IN_PROGRESS"

        await record_registration_recovery_invalid_otp(token, phone, claims[0])
        for _ in range(REGISTRATION_RECOVERY_MAX_INVALID_OTPS - 1):
            claim = await claim_registration_recovery_attempt(token, phone)
            await record_registration_recovery_invalid_otp(token, phone, claim)
        with pytest.raises(RegistrationRecoveryAttemptError) as exhausted:
            await claim_registration_recovery_attempt(token, phone)
        assert exhausted.value.code == "REGISTRATION_RECOVERY_ATTEMPT_EXHAUSTED"

        release_token = await issue_registration_recovery_attempt(phone)
        for _ in range(REGISTRATION_RECOVERY_MAX_INVALID_OTPS + 2):
            claim = await claim_registration_recovery_attempt(release_token, phone)
            await release_registration_recovery_claim(release_token, phone, claim)
        final_claim = await claim_registration_recovery_attempt(release_token, phone)
        await release_registration_recovery_claim(release_token, phone, final_claim)

        with pytest.raises(RegistrationRecoveryAttemptError) as wrong_phone:
            await claim_registration_recovery_attempt(release_token, "+919000000001")
        assert wrong_phone.value.code == "REGISTRATION_RECOVERY_ATTEMPT_INVALID"
    finally:
        keys = [key for key in (_attempt_key(token) if token else None, _attempt_key(release_token) if release_token else None) if key]
        if keys:
            await redis.delete(*keys)
        close = getattr(redis, "aclose", None) or redis.close
        result = close()
        if result is not None:
            await result
        get_async_redis_client.cache_clear()


@pytest.mark.asyncio
async def test_repair_capability_real_redis_is_subject_bound_one_time_and_reissuable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _require_local_redis()
    monkeypatch.setenv("UPSTASH_REDIS_URL", url)
    monkeypatch.setenv("OTP_RATE_LIMIT_HMAC_SECRET", "r" * 32)
    get_async_redis_client.cache_clear()
    redis = get_async_redis_client()
    subject = f"recovery-subject-{uuid.uuid4().hex}"
    first_token = ""
    second_token = ""
    try:
        first = await issue_registration_recovery_capability(
            patient_id=str(uuid.uuid4()),
            provider_subject=subject,
            repair_kind="restore_patient_record_anchor",
            graph_fingerprint="a" * 64,
        )
        first_token = first.token

        with pytest.raises(RegistrationRecoveryCapabilityError) as duplicate:
            await issue_registration_recovery_capability(
                patient_id=first.patient_id,
                provider_subject=subject,
                repair_kind=first.repair_kind,
                graph_fingerprint=first.graph_fingerprint,
            )
        assert duplicate.value.code == "REGISTRATION_RECOVERY_CAPABILITY_ALREADY_ISSUED"

        consumed = await consume_registration_recovery_capability(first.token)
        assert consumed.patient_id == first.patient_id
        assert consumed.provider_subject == subject
        assert consumed.repair_kind == first.repair_kind
        assert consumed.graph_fingerprint == first.graph_fingerprint

        with pytest.raises(RegistrationRecoveryCapabilityError) as replay:
            await consume_registration_recovery_capability(first.token)
        assert replay.value.code == "REGISTRATION_RECOVERY_CAPABILITY_INVALID"

        second = await issue_registration_recovery_capability(
            patient_id=first.patient_id,
            provider_subject=subject,
            repair_kind=first.repair_kind,
            graph_fingerprint="b" * 64,
        )
        second_token = second.token
        assert second.token != first.token
        consumed_second = await consume_registration_recovery_capability(second.token)
        assert consumed_second.graph_fingerprint == "b" * 64
    finally:
        keys = [
            _capability_slot_key(subject),
            *[
                key
                for key in (
                    _capability_key(first_token) if first_token else None,
                    _capability_key(second_token) if second_token else None,
                )
                if key
            ],
        ]
        await redis.delete(*keys)
        close = getattr(redis, "aclose", None) or redis.close
        result = close()
        if result is not None:
            await result
        get_async_redis_client.cache_clear()
