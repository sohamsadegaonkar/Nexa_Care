"""Security qualification for bounded patient-registration invalid OTP attempts."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.patient_registration_attempt_service import (
    REGISTRATION_ATTEMPT_MAX_VERIFICATIONS,
    RegistrationAttemptError,
    claim_registration_attempt,
    issue_registration_attempt,
    record_registration_attempt_invalid_otp,
    release_registration_attempt_claim,
)

PHONE = "+918000000001"


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, *, nx=False, xx=False, ex=None):
        if nx and key in self.values:
            return False
        if xx and key not in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = int(ex or 300)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def ttl(self, key):
        return self.ttls.get(key, -2)


def _secret():
    return patch(
        "app.services.patient_registration_attempt_service.get_otp_rate_limit_config",
        return_value=SimpleNamespace(hmac_secret="a" * 32),
    )


def _state(redis: _Redis) -> dict:
    return json.loads(next(iter(redis.values.values())))


@pytest.mark.asyncio
async def test_claim_and_release_do_not_consume_invalid_otp_budget() -> None:
    """DB/provider availability retries must not burn the OTP-guess budget."""

    redis = _Redis()
    with (
        _secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            return_value=redis,
        ),
    ):
        token = await issue_registration_attempt(PHONE)
        assert _state(redis)["verification_count"] == 0

        for _ in range(REGISTRATION_ATTEMPT_MAX_VERIFICATIONS + 2):
            claim = await claim_registration_attempt(token, PHONE)
            claimed = _state(redis)
            assert claimed["state"] == "verifying"
            assert claimed["verification_count"] == 0

            await release_registration_attempt_claim(token, PHONE, claim)
            released = _state(redis)
            assert released["state"] == "pending"
            assert released["verification_count"] == 0


@pytest.mark.asyncio
async def test_confirmed_invalid_otps_exhaust_attempt_after_five_failures() -> None:
    redis = _Redis()
    with (
        _secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            return_value=redis,
        ),
    ):
        token = await issue_registration_attempt(PHONE)

        for expected_count in range(1, REGISTRATION_ATTEMPT_MAX_VERIFICATIONS + 1):
            claim = await claim_registration_attempt(token, PHONE)
            assert _state(redis)["verification_count"] == expected_count - 1

            await record_registration_attempt_invalid_otp(token, PHONE, claim)
            recorded = _state(redis)
            assert recorded["verification_count"] == expected_count
            assert recorded["state"] == (
                "exhausted"
                if expected_count == REGISTRATION_ATTEMPT_MAX_VERIFICATIONS
                else "pending"
            )

        with pytest.raises(RegistrationAttemptError) as exc_info:
            await claim_registration_attempt(token, PHONE)

    assert exc_info.value.code == "REGISTRATION_ATTEMPT_EXHAUSTED"


@pytest.mark.asyncio
async def test_invalid_otp_recording_requires_exact_active_claim() -> None:
    """A stale worker cannot charge or release a newer verifier's claim."""

    redis = _Redis()
    with (
        _secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            return_value=redis,
        ),
    ):
        token = await issue_registration_attempt(PHONE)
        stale_claim = await claim_registration_attempt(token, PHONE)
        await release_registration_attempt_claim(token, PHONE, stale_claim)
        active_claim = await claim_registration_attempt(token, PHONE)

        with pytest.raises(RegistrationAttemptError) as exc_info:
            await record_registration_attempt_invalid_otp(token, PHONE, stale_claim)

        still_active = _state(redis)
        assert exc_info.value.code == "REGISTRATION_ATTEMPT_INVALID"
        assert still_active["state"] == "verifying"
        assert still_active["claim_id"] == active_claim.claim_id
        assert still_active["verification_count"] == 0

        await record_registration_attempt_invalid_otp(token, PHONE, active_claim)
        recorded = _state(redis)
        assert recorded["state"] == "pending"
        assert recorded["verification_count"] == 1


@pytest.mark.asyncio
async def test_exhausted_registration_attempt_cannot_be_resurrected_by_old_release() -> None:
    redis = _Redis()
    with (
        _secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            return_value=redis,
        ),
    ):
        token = await issue_registration_attempt(PHONE)
        last_claim = None
        for _ in range(REGISTRATION_ATTEMPT_MAX_VERIFICATIONS):
            last_claim = await claim_registration_attempt(token, PHONE)
            await record_registration_attempt_invalid_otp(token, PHONE, last_claim)

        exhausted = _state(redis)
        assert exhausted["state"] == "exhausted"
        assert exhausted["verification_count"] == REGISTRATION_ATTEMPT_MAX_VERIFICATIONS

        assert last_claim is not None
        await release_registration_attempt_claim(token, PHONE, last_claim)

        with pytest.raises(RegistrationAttemptError) as exc_info:
            await claim_registration_attempt(token, PHONE)

    assert exc_info.value.code == "REGISTRATION_ATTEMPT_EXHAUSTED"
