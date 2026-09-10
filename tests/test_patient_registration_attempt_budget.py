"""Security qualification for bounded patient-registration verification attempts."""

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


@pytest.mark.asyncio
async def test_registration_attempt_verification_budget_is_server_side_and_bounded() -> None:
    redis = _Redis()
    with (
        _secret(),
        patch(
            "app.services.patient_registration_attempt_service.get_async_redis_client",
            return_value=redis,
        ),
    ):
        token = await issue_registration_attempt(PHONE)
        key = next(iter(redis.values))

        initial = json.loads(redis.values[key])
        assert initial["verification_count"] == 0
        assert initial["state"] == "pending"

        for expected_count in range(1, REGISTRATION_ATTEMPT_MAX_VERIFICATIONS + 1):
            claim = await claim_registration_attempt(token, PHONE)
            state = json.loads(redis.values[key])
            assert state["verification_count"] == expected_count
            await release_registration_attempt_claim(token, PHONE, claim)
            released = json.loads(redis.values[key])
            assert released["verification_count"] == expected_count
            assert released["state"] == "pending"

        with pytest.raises(RegistrationAttemptError) as exc_info:
            await claim_registration_attempt(token, PHONE)

        exhausted = json.loads(redis.values[key])

    assert exc_info.value.code == "REGISTRATION_ATTEMPT_EXHAUSTED"
    assert exhausted["state"] == "exhausted"
    assert exhausted["verification_count"] == REGISTRATION_ATTEMPT_MAX_VERIFICATIONS


@pytest.mark.asyncio
async def test_exhausted_registration_attempt_cannot_be_recovered_by_release_or_reclaim() -> None:
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
            await release_registration_attempt_claim(token, PHONE, last_claim)

        with pytest.raises(RegistrationAttemptError) as first:
            await claim_registration_attempt(token, PHONE)
        assert first.value.code == "REGISTRATION_ATTEMPT_EXHAUSTED"

        # Releasing an old claim cannot replenish or resurrect the attempt.
        assert last_claim is not None
        await release_registration_attempt_claim(token, PHONE, last_claim)

        with pytest.raises(RegistrationAttemptError) as second:
            await claim_registration_attempt(token, PHONE)
        assert second.value.code == "REGISTRATION_ATTEMPT_EXHAUSTED"
