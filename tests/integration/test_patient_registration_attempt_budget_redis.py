"""Real-Redis qualification for registration invalid-OTP budget transitions."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from redis.asyncio import Redis

from app.services.patient_registration_attempt_service import (
    REGISTRATION_ATTEMPT_MAX_VERIFICATIONS,
    RegistrationAttemptError,
    claim_registration_attempt,
    issue_registration_attempt,
    record_registration_attempt_invalid_otp,
    release_registration_attempt_claim,
)
from tests.helpers.qualification_infra import get_qualification_redis_url

pytestmark = [pytest.mark.redis, pytest.mark.asyncio]
PHONE = "+918000000001"


async def test_real_redis_lua_charges_only_confirmed_invalid_otps() -> None:
    """Production EVAL path must not charge claims or transient releases."""

    redis = Redis.from_url(get_qualification_redis_url(), decode_responses=True)
    prefix = f"nexa-qual:registration-budget:{uuid.uuid4().hex}:"
    try:
        with (
            patch(
                "app.services.patient_registration_attempt_service.get_async_redis_client",
                return_value=redis,
            ),
            patch(
                "app.services.patient_registration_attempt_service.get_otp_rate_limit_config",
                return_value=SimpleNamespace(hmac_secret="r" * 32),
            ),
            patch(
                "app.services.patient_registration_attempt_service._PREFIX",
                prefix,
            ),
        ):
            token = await issue_registration_attempt(PHONE)
            keys = await redis.keys(f"{prefix}*")
            assert len(keys) == 1
            key = keys[0]

            initial = json.loads(await redis.get(key))
            assert initial["state"] == "pending"
            assert initial["verification_count"] == 0

            transient_claim = await claim_registration_attempt(token, PHONE)
            claimed = json.loads(await redis.get(key))
            assert claimed["state"] == "verifying"
            assert claimed["verification_count"] == 0

            await release_registration_attempt_claim(token, PHONE, transient_claim)
            released = json.loads(await redis.get(key))
            assert released["state"] == "pending"
            assert released["verification_count"] == 0

            for expected_count in range(1, REGISTRATION_ATTEMPT_MAX_VERIFICATIONS + 1):
                claim = await claim_registration_attempt(token, PHONE)
                before_record = json.loads(await redis.get(key))
                assert before_record["verification_count"] == expected_count - 1
                assert before_record["state"] == "verifying"

                await record_registration_attempt_invalid_otp(token, PHONE, claim)
                recorded = json.loads(await redis.get(key))
                assert recorded["verification_count"] == expected_count
                assert recorded["state"] == (
                    "exhausted"
                    if expected_count == REGISTRATION_ATTEMPT_MAX_VERIFICATIONS
                    else "pending"
                )

            with pytest.raises(RegistrationAttemptError) as exc_info:
                await claim_registration_attempt(token, PHONE)
            assert exc_info.value.code == "REGISTRATION_ATTEMPT_EXHAUSTED"

            terminal = json.loads(await redis.get(key))
            assert terminal["verification_count"] == REGISTRATION_ATTEMPT_MAX_VERIFICATIONS
            assert terminal["state"] == "exhausted"
    finally:
        keys = await redis.keys(f"{prefix}*")
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_real_redis_lua_rejects_stale_claim_without_charging_active_claim() -> None:
    """A stale worker must never spend the budget owned by a newer verifier."""

    redis = Redis.from_url(get_qualification_redis_url(), decode_responses=True)
    prefix = f"nexa-qual:registration-budget-stale:{uuid.uuid4().hex}:"
    try:
        with (
            patch(
                "app.services.patient_registration_attempt_service.get_async_redis_client",
                return_value=redis,
            ),
            patch(
                "app.services.patient_registration_attempt_service.get_otp_rate_limit_config",
                return_value=SimpleNamespace(hmac_secret="s" * 32),
            ),
            patch(
                "app.services.patient_registration_attempt_service._PREFIX",
                prefix,
            ),
        ):
            token = await issue_registration_attempt(PHONE)
            first = await claim_registration_attempt(token, PHONE)
            await release_registration_attempt_claim(token, PHONE, first)
            second = await claim_registration_attempt(token, PHONE)

            with pytest.raises(RegistrationAttemptError) as stale:
                await record_registration_attempt_invalid_otp(token, PHONE, first)
            assert stale.value.code == "REGISTRATION_ATTEMPT_INVALID"

            keys = await redis.keys(f"{prefix}*")
            assert len(keys) == 1
            state = json.loads(await redis.get(keys[0]))
            assert state["state"] == "verifying"
            assert state["claim_id"] == second.claim_id
            assert state["verification_count"] == 0

            await record_registration_attempt_invalid_otp(token, PHONE, second)
            charged = json.loads(await redis.get(keys[0]))
            assert charged["state"] == "pending"
            assert charged["verification_count"] == 1
    finally:
        keys = await redis.keys(f"{prefix}*")
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
