"""Disposable Redis proof for registration-attempt capabilities."""

from __future__ import annotations

import asyncio
import hashlib
import os

import pytest

from app.core.redis import get_async_redis_client
from app.services.patient_registration_attempt_service import (
    RegistrationAttemptError,
    claim_registration_attempt,
    finalize_registration_attempt,
    issue_registration_attempt,
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
async def test_registration_attempt_real_redis_claim_finalization_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _require_local_redis()
    monkeypatch.setenv("UPSTASH_REDIS_URL", url)
    monkeypatch.setenv("OTP_RATE_LIMIT_HMAC_SECRET", "r" * 32)
    get_async_redis_client.cache_clear()
    redis = get_async_redis_client()
    phone = "+918000000001"
    try:
        token = await issue_registration_attempt(phone)
        first, second = await asyncio.gather(
            claim_registration_attempt(token, phone),
            claim_registration_attempt(token, phone),
            return_exceptions=True,
        )
        claims = [item for item in (first, second) if not isinstance(item, Exception)]
        failures = [item for item in (first, second) if isinstance(item, Exception)]
        assert len(claims) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RegistrationAttemptError)
        assert failures[0].code == "REGISTRATION_ATTEMPT_IN_PROGRESS"
        await finalize_registration_attempt(token, phone, claims[0], "patient-local")
        replay = await claim_registration_attempt(token, phone)
        assert replay.finalized is True
        assert replay.finalized_patient_id == "patient-local"
        with pytest.raises(RegistrationAttemptError) as wrong_phone:
            await claim_registration_attempt(token, "+919000000001")
        assert wrong_phone.value.code == "REGISTRATION_ATTEMPT_INVALID"
        await redis.delete(
            "nexa:patient_registration_attempt:"
            + hashlib.sha256(token.encode()).hexdigest()
        )
        with pytest.raises(RegistrationAttemptError) as expired:
            await claim_registration_attempt(token, phone)
        assert expired.value.code == "REGISTRATION_ATTEMPT_INVALID"
    finally:
        close = getattr(redis, "aclose", None) or redis.close
        result = close()
        if result is not None:
            await result
        get_async_redis_client.cache_clear()
