"""Pure-unit adversarial coverage for Slice 6E recovery authority."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.patient_device_recovery import (
    PatientRecoveryAuthorityUnavailable,
    PatientRecoveryCapabilityError,
    TrustedEnrollmentChallengeError,
    canonical_trusted_enrollment_payload,
    consume_patient_recovery_capability,
    consume_trusted_enrollment_challenge,
    issue_patient_recovery_capability,
    issue_trusted_enrollment_challenge,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def setex(self, key: str, _ttl: int, value: str):
        self.values[key] = value
        return True

    def delete(self, *keys: str):
        count = 0
        for key in keys:
            count += int(self.values.pop(key, None) is not None)
        return count


class UnavailableRedis:
    def get(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    def set(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")


@pytest.mark.asyncio
async def test_recovery_capability_is_bound_to_exact_session_identity_and_one_time():
    redis = FakeRedis()
    live = AsyncMock(return_value={"status": "active", "supabase_user_id": "subject-a"})
    with (
        patch("app.services.patient_device_recovery.get_redis_client", return_value=redis),
        patch(
            "app.services.patient_device_recovery.resolve_patient_session_id",
            new=live,
        ),
    ):
        cap = await issue_patient_recovery_capability(
            patient_id="patient-a",
            session_id="session-a-1234567890",
            supabase_user_id="subject-a",
        )
        with pytest.raises(PatientRecoveryCapabilityError) as wrong_subject:
            await consume_patient_recovery_capability(
                token=cap.token,
                patient_id="patient-a",
                session_id="session-a-1234567890",
                supabase_user_id="subject-b",
            )
        assert wrong_subject.value.code == "PATIENT_RECOVERY_IDENTITY_MISMATCH"

        consumed = await consume_patient_recovery_capability(
            token=cap.token,
            patient_id="patient-a",
            session_id="session-a-1234567890",
            supabase_user_id="subject-a",
        )
        assert consumed.token == cap.token
        with pytest.raises(PatientRecoveryCapabilityError) as replay:
            await consume_patient_recovery_capability(
                token=cap.token,
                patient_id="patient-a",
                session_id="session-a-1234567890",
                supabase_user_id="subject-a",
            )
        assert replay.value.code == "PATIENT_RECOVERY_CAPABILITY_INVALID"


@pytest.mark.asyncio
async def test_recovery_capability_allows_only_one_outstanding_token_per_session():
    redis = FakeRedis()
    live = AsyncMock(return_value={"status": "active", "supabase_user_id": "subject"})
    with (
        patch("app.services.patient_device_recovery.get_redis_client", return_value=redis),
        patch(
            "app.services.patient_device_recovery.resolve_patient_session_id",
            new=live,
        ),
    ):
        await issue_patient_recovery_capability(
            patient_id="patient-a",
            session_id="session-a-1234567890",
            supabase_user_id="subject",
        )
        with pytest.raises(PatientRecoveryCapabilityError) as duplicate:
            await issue_patient_recovery_capability(
                patient_id="patient-a",
                session_id="session-a-1234567890",
                supabase_user_id="subject",
            )
        assert duplicate.value.code == "PATIENT_RECOVERY_CAPABILITY_ALREADY_ISSUED"


@pytest.mark.asyncio
async def test_recovery_session_and_redis_fail_closed():
    inactive = AsyncMock(return_value=None)
    with patch(
        "app.services.patient_device_recovery.resolve_patient_session_id", new=inactive
    ):
        with pytest.raises(PatientRecoveryCapabilityError) as exc_info:
            await issue_patient_recovery_capability(
                patient_id="patient-a",
                session_id="session-a-1234567890",
                supabase_user_id="subject",
            )
        assert exc_info.value.code == "PATIENT_RECOVERY_SESSION_INACTIVE"

    live = AsyncMock(return_value={"status": "active", "supabase_user_id": "subject"})
    with (
        patch("app.services.patient_device_recovery.get_redis_client", return_value=UnavailableRedis()),
        patch(
            "app.services.patient_device_recovery.resolve_patient_session_id", new=live
        ),
    ):
        with pytest.raises(PatientRecoveryAuthorityUnavailable):
            await issue_patient_recovery_capability(
                patient_id="patient-a",
                session_id="session-a-1234567890",
                supabase_user_id="subject",
            )


@pytest.mark.asyncio
async def test_trusted_enrollment_challenge_binds_all_security_fields_and_replays_fail():
    redis = FakeRedis()
    live = AsyncMock(return_value={"status": "active", "supabase_user_id": "subject"})
    with (
        patch("app.services.patient_device_recovery.get_redis_client", return_value=redis),
        patch(
            "app.services.patient_device_recovery.resolve_patient_session_id", new=live
        ),
    ):
        challenge = await issue_trusted_enrollment_challenge(
            patient_id="patient-a",
            session_id="session-a-1234567890",
            authorizer_device_id="device-a",
            authorizer_key_version=3,
            new_public_key_fingerprint="a" * 64,
        )
        expected = canonical_trusted_enrollment_payload(
            patient_id="patient-a",
            session_id="session-a-1234567890",
            authorizer_device_id="device-a",
            authorizer_key_version=3,
            new_public_key_fingerprint="a" * 64,
            challenge_nonce=challenge.nonce,
            issued_at=challenge.issued_at,
            expires_at=challenge.expires_at,
        )
        assert challenge.signing_payload == expected

        with pytest.raises(TrustedEnrollmentChallengeError) as wrong_key:
            await consume_trusted_enrollment_challenge(
                challenge_nonce=challenge.nonce,
                patient_id="patient-a",
                session_id="session-a-1234567890",
                authorizer_device_id="device-a",
                authorizer_key_version=3,
                new_public_key_fingerprint="b" * 64,
            )
        assert wrong_key.value.code == "TRUSTED_ENROLLMENT_BINDING_MISMATCH"

        challenge = await issue_trusted_enrollment_challenge(
            patient_id="patient-a",
            session_id="session-a-1234567890",
            authorizer_device_id="device-a",
            authorizer_key_version=3,
            new_public_key_fingerprint="c" * 64,
        )
        await consume_trusted_enrollment_challenge(
            challenge_nonce=challenge.nonce,
            patient_id="patient-a",
            session_id="session-a-1234567890",
            authorizer_device_id="device-a",
            authorizer_key_version=3,
            new_public_key_fingerprint="c" * 64,
        )
        with pytest.raises(TrustedEnrollmentChallengeError) as replay:
            await consume_trusted_enrollment_challenge(
                challenge_nonce=challenge.nonce,
                patient_id="patient-a",
                session_id="session-a-1234567890",
                authorizer_device_id="device-a",
                authorizer_key_version=3,
                new_public_key_fingerprint="c" * 64,
            )
        assert replay.value.code == "TRUSTED_ENROLLMENT_CHALLENGE_INVALID"


@pytest.mark.asyncio
async def test_trusted_enrollment_concurrent_consumption_has_one_winner():
    redis = FakeRedis()
    live = AsyncMock(return_value={"status": "active", "supabase_user_id": "subject"})
    with (
        patch("app.services.patient_device_recovery.get_redis_client", return_value=redis),
        patch(
            "app.services.patient_device_recovery.resolve_patient_session_id", new=live
        ),
    ):
        challenge = await issue_trusted_enrollment_challenge(
            patient_id="patient-a",
            session_id="session-a-1234567890",
            authorizer_device_id="device-a",
            authorizer_key_version=1,
            new_public_key_fingerprint="d" * 64,
        )

        async def contender():
            try:
                return await consume_trusted_enrollment_challenge(
                    challenge_nonce=challenge.nonce,
                    patient_id="patient-a",
                    session_id="session-a-1234567890",
                    authorizer_device_id="device-a",
                    authorizer_key_version=1,
                    new_public_key_fingerprint="d" * 64,
                )
            except TrustedEnrollmentChallengeError as exc:
                return exc.code

        results = await asyncio.gather(*[contender() for _ in range(8)])
        winners = [r for r in results if not isinstance(r, str)]
        assert len(winners) == 1
        assert all(
            not isinstance(r, str) or r == "TRUSTED_ENROLLMENT_CHALLENGE_INVALID"
            for r in results
        )
