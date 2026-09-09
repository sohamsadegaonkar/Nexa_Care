"""Real Redis qualification for Slice 6E recovery and trusted enrollment authority."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from redis.asyncio import ConnectionPool, Redis

import app.services.patient_device_recovery as recovery_authority
from app.services.patient_device_recovery import (
    PatientRecoveryAuthorityUnavailable,
    PatientRecoveryCapability,
    PatientRecoveryCapabilityError,
    TrustedEnrollmentChallenge,
    TrustedEnrollmentChallengeError,
    _recovery_key,
    _recovery_slot_key,
    _trusted_challenge_key,
    consume_patient_recovery_capability,
    consume_trusted_enrollment_challenge,
    issue_patient_recovery_capability,
    issue_trusted_enrollment_challenge,
)
from app.services.patient_session_authority import (
    create_patient_session,
    get_or_create_patient_session_epoch,
    revoke_patient_session,
)

pytestmark = pytest.mark.redis


async def _await_if_needed(result: object) -> None:
    if inspect.isawaitable(result):
        await result


@pytest_asyncio.fixture
async def real_redis():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    pool = ConnectionPool.from_url(url, decode_responses=True)
    client = Redis(connection_pool=pool)
    await client.ping()
    try:
        yield client
    finally:
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close is not None:
            try:
                await _await_if_needed(close())
            except TypeError:
                pass
        pool_close = getattr(pool, "aclose", None)
        if pool_close is not None:
            await _await_if_needed(pool_close())
        else:
            await pool.disconnect(inuse_connections=True)
        await asyncio.sleep(0.05)


def _session_key(session_id: str) -> str:
    return "nexa:patient_session:" + hashlib.sha256(session_id.encode()).hexdigest()


async def _create_live_session(redis, patient_id: str, subject: str, session_id: str):
    with patch(
        "app.services.patient_session_authority.get_redis_client", return_value=redis
    ):
        epoch = await get_or_create_patient_session_epoch(patient_id)
        now = datetime.now(timezone.utc)
        await create_patient_session(
            patient_id=patient_id,
            supabase_user_id=subject,
            session_id=session_id,
            session_epoch=epoch,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )


async def _cleanup(redis, patient_ids, session_ids, tokens, nonces):
    keys = [f"nexa:patient_session_epoch:{patient_id}" for patient_id in patient_ids]
    keys.extend(_session_key(session_id) for session_id in session_ids)
    for patient_id, session_id in zip(patient_ids, session_ids):
        keys.append(_recovery_slot_key(patient_id, session_id))
    keys.extend(_recovery_key(token) for token in tokens)
    keys.extend(_trusted_challenge_key(nonce) for nonce in nonces)
    if keys:
        await redis.delete(*keys)


def _patch_real_redis(redis):
    return (
        patch("app.services.patient_device_recovery.get_redis_client", return_value=redis),
        patch("app.services.patient_session_authority.get_redis_client", return_value=redis),
    )


@pytest.mark.asyncio
async def test_recovery_capability_is_exact_session_bound_and_one_time(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    subject = "subject-a"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, subject, session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            cap = await issue_patient_recovery_capability(
                patient_id=patient, session_id=session, supabase_user_id=subject
            )
            tokens.append(cap.token)
            consumed = await consume_patient_recovery_capability(
                token=cap.token,
                patient_id=patient,
                session_id=session,
                supabase_user_id=subject,
            )
            assert consumed.token == cap.token
            with pytest.raises(PatientRecoveryCapabilityError) as replay:
                await consume_patient_recovery_capability(
                    token=cap.token,
                    patient_id=patient,
                    session_id=session,
                    supabase_user_id=subject,
                )
            assert replay.value.code == "PATIENT_RECOVERY_CAPABILITY_INVALID"
        finally:
            await _cleanup(real_redis, [patient], [session], tokens, [])


@pytest.mark.asyncio
async def test_recovery_capability_wrong_patient_subject_and_session_fail_closed(real_redis):
    patient = str(uuid.uuid4())
    other_patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    other_session = f"session-{uuid.uuid4().hex}"
    subject = "subject-a"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, subject, session)
    await _create_live_session(real_redis, other_patient, "subject-b", other_session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            cap = await issue_patient_recovery_capability(
                patient_id=patient, session_id=session, supabase_user_id=subject
            )
            tokens.append(cap.token)
            with pytest.raises(PatientRecoveryCapabilityError):
                await consume_patient_recovery_capability(
                    token=cap.token,
                    patient_id=other_patient,
                    session_id=other_session,
                    supabase_user_id="subject-b",
                )
            with pytest.raises(PatientRecoveryCapabilityError) as wrong_subject:
                await consume_patient_recovery_capability(
                    token=cap.token,
                    patient_id=patient,
                    session_id=session,
                    supabase_user_id="subject-b",
                )
            assert wrong_subject.value.code == "PATIENT_RECOVERY_IDENTITY_MISMATCH"
        finally:
            await _cleanup(
                real_redis,
                [patient, other_patient],
                [session, other_session],
                tokens,
                [],
            )


@pytest.mark.asyncio
async def test_concurrent_recovery_capability_consumption_has_exactly_one_winner(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    subject = "subject"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, subject, session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            cap = await issue_patient_recovery_capability(
                patient_id=patient, session_id=session, supabase_user_id=subject
            )
            tokens.append(cap.token)

            async def contender():
                try:
                    return await consume_patient_recovery_capability(
                        token=cap.token,
                        patient_id=patient,
                        session_id=session,
                        supabase_user_id=subject,
                    )
                except PatientRecoveryCapabilityError as exc:
                    return exc.code

            results = await asyncio.gather(*[contender() for _ in range(12)])
            winners = [r for r in results if isinstance(r, PatientRecoveryCapability)]
            assert len(winners) == 1
            assert all(
                isinstance(r, PatientRecoveryCapability)
                or r == "PATIENT_RECOVERY_CAPABILITY_INVALID"
                for r in results
            )
        finally:
            await _cleanup(real_redis, [patient], [session], tokens, [])


@pytest.mark.asyncio
async def test_revoked_session_cannot_consume_recovery_capability(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    subject = "subject"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, subject, session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            cap = await issue_patient_recovery_capability(
                patient_id=patient, session_id=session, supabase_user_id=subject
            )
            tokens.append(cap.token)
            assert await revoke_patient_session(patient_id=patient, session_id=session)
            with pytest.raises(PatientRecoveryCapabilityError) as exc_info:
                await consume_patient_recovery_capability(
                    token=cap.token,
                    patient_id=patient,
                    session_id=session,
                    supabase_user_id=subject,
                )
            assert exc_info.value.code == "PATIENT_RECOVERY_SESSION_INACTIVE"
        finally:
            await _cleanup(real_redis, [patient], [session], tokens, [])


@pytest.mark.asyncio
async def test_recovery_capability_expiry_fails_closed(real_redis, monkeypatch):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    subject = "subject"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, subject, session)
    monkeypatch.setattr(recovery_authority, "PATIENT_RECOVERY_TTL_SECONDS", 1)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            cap = await issue_patient_recovery_capability(
                patient_id=patient, session_id=session, supabase_user_id=subject
            )
            tokens.append(cap.token)
            await asyncio.sleep(1.2)
            with pytest.raises(PatientRecoveryCapabilityError) as exc_info:
                await consume_patient_recovery_capability(
                    token=cap.token,
                    patient_id=patient,
                    session_id=session,
                    supabase_user_id=subject,
                )
            assert exc_info.value.code in {
                "PATIENT_RECOVERY_CAPABILITY_INVALID",
                "PATIENT_RECOVERY_CAPABILITY_EXPIRED",
            }
        finally:
            await _cleanup(real_redis, [patient], [session], tokens, [])


@pytest.mark.asyncio
async def test_trusted_enrollment_challenge_real_redis_replay_and_concurrency(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    device = str(uuid.uuid4())
    nonces: list[str] = []
    await _create_live_session(real_redis, patient, "subject", session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            challenge = await issue_trusted_enrollment_challenge(
                patient_id=patient,
                session_id=session,
                authorizer_device_id=device,
                authorizer_key_version=1,
                new_public_key_fingerprint="a" * 64,
            )
            nonces.append(challenge.nonce)

            async def contender():
                try:
                    return await consume_trusted_enrollment_challenge(
                        challenge_nonce=challenge.nonce,
                        patient_id=patient,
                        session_id=session,
                        authorizer_device_id=device,
                        authorizer_key_version=1,
                        new_public_key_fingerprint="a" * 64,
                    )
                except TrustedEnrollmentChallengeError as exc:
                    return exc.code

            results = await asyncio.gather(*[contender() for _ in range(10)])
            winners = [r for r in results if isinstance(r, TrustedEnrollmentChallenge)]
            assert len(winners) == 1
            assert all(
                isinstance(r, TrustedEnrollmentChallenge)
                or r == "TRUSTED_ENROLLMENT_CHALLENGE_INVALID"
                for r in results
            )
        finally:
            await _cleanup(real_redis, [patient], [session], [], nonces)


class _UnavailableRedis:
    async def eval(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def set(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")


@pytest.mark.asyncio
async def test_recovery_redis_unavailable_fails_closed_after_session_validation(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    await _create_live_session(real_redis, patient, "subject", session)
    with (
        patch(
            "app.services.patient_device_recovery.get_redis_client",
            return_value=_UnavailableRedis(),
        ),
        patch(
            "app.services.patient_session_authority.get_redis_client",
            return_value=real_redis,
        ),
    ):
        try:
            with pytest.raises(PatientRecoveryAuthorityUnavailable):
                await issue_patient_recovery_capability(
                    patient_id=patient,
                    session_id=session,
                    supabase_user_id="subject",
                )
        finally:
            await _cleanup(real_redis, [patient], [session], [], [])
