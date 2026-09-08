"""Real Redis qualification for Slice 6D one-time rotation authority."""

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

import app.services.patient_device_rotation as rotation_authority
from app.services.patient_device_rotation import (
    DeviceRotationAuthorityUnavailable,
    DeviceRotationChallenge,
    DeviceRotationChallengeError,
    _challenge_key,
    consume_device_rotation_challenge,
    issue_device_rotation_challenge,
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
        client_close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if client_close is not None:
            try:
                await _await_if_needed(client_close())
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


async def _cleanup(redis, patient_ids, session_ids, nonces):
    keys = [f"nexa:patient_session_epoch:{patient_id}" for patient_id in patient_ids]
    keys.extend(_session_key(session_id) for session_id in session_ids)
    keys.extend(_challenge_key(nonce) for nonce in nonces)
    if keys:
        await redis.delete(*keys)


def _patch_real_redis(redis):
    return (
        patch("app.services.patient_device_rotation.get_redis_client", return_value=redis),
        patch("app.services.patient_session_authority.get_redis_client", return_value=redis),
    )


@pytest.mark.asyncio
async def test_rotation_challenge_is_exact_session_bound_and_one_time(real_redis):
    patient = str(uuid.uuid4())
    session_a = f"session-{uuid.uuid4().hex}"
    session_b = f"session-{uuid.uuid4().hex}"
    device = str(uuid.uuid4())
    nonces: list[str] = []
    await _create_live_session(real_redis, patient, "subject", session_a)
    await _create_live_session(real_redis, patient, "subject", session_b)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            challenge = await issue_device_rotation_challenge(
                patient_id=patient,
                session_id=session_a,
                device_id=device,
                current_key_version=1,
                new_public_key_fingerprint="a" * 64,
            )
            nonces.append(challenge.nonce)
            with pytest.raises(DeviceRotationChallengeError) as wrong_session:
                await consume_device_rotation_challenge(
                    challenge_nonce=challenge.nonce,
                    patient_id=patient,
                    session_id=session_b,
                    device_id=device,
                    current_key_version=1,
                    new_public_key_fingerprint="a" * 64,
                )
            assert wrong_session.value.code == "DEVICE_ROTATION_BINDING_MISMATCH"

            challenge = await issue_device_rotation_challenge(
                patient_id=patient,
                session_id=session_a,
                device_id=device,
                current_key_version=1,
                new_public_key_fingerprint="b" * 64,
            )
            nonces.append(challenge.nonce)
            consumed = await consume_device_rotation_challenge(
                challenge_nonce=challenge.nonce,
                patient_id=patient,
                session_id=session_a,
                device_id=device,
                current_key_version=1,
                new_public_key_fingerprint="b" * 64,
            )
            assert consumed.signing_payload == challenge.signing_payload
            with pytest.raises(DeviceRotationChallengeError) as replay:
                await consume_device_rotation_challenge(
                    challenge_nonce=challenge.nonce,
                    patient_id=patient,
                    session_id=session_a,
                    device_id=device,
                    current_key_version=1,
                    new_public_key_fingerprint="b" * 64,
                )
            assert replay.value.code == "DEVICE_ROTATION_CHALLENGE_INVALID"
        finally:
            await _cleanup(real_redis, [patient], [session_a, session_b], nonces)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong_value_factory"),
    [
        ("patient_id", lambda: str(uuid.uuid4())),
        ("device_id", lambda: str(uuid.uuid4())),
        ("current_key_version", lambda: 8),
        ("new_public_key_fingerprint", lambda: "f" * 64),
    ],
)
async def test_rotation_challenge_rejects_cross_binding_fields(
    real_redis, field, wrong_value_factory
):
    patient = str(uuid.uuid4())
    other_patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    other_session = f"session-{uuid.uuid4().hex}"
    device = str(uuid.uuid4())
    nonces: list[str] = []
    await _create_live_session(real_redis, patient, "subject-a", session)
    await _create_live_session(real_redis, other_patient, "subject-b", other_session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            challenge = await issue_device_rotation_challenge(
                patient_id=patient,
                session_id=session,
                device_id=device,
                current_key_version=2,
                new_public_key_fingerprint="c" * 64,
            )
            nonces.append(challenge.nonce)
            attempted = {
                "patient_id": patient,
                "session_id": session,
                "device_id": device,
                "current_key_version": 2,
                "new_public_key_fingerprint": "c" * 64,
            }
            attempted[field] = wrong_value_factory()
            if field == "patient_id":
                attempted["session_id"] = other_session
            with pytest.raises(DeviceRotationChallengeError) as exc_info:
                await consume_device_rotation_challenge(
                    challenge_nonce=challenge.nonce, **attempted
                )
            expected_codes = {"DEVICE_ROTATION_BINDING_MISMATCH"}
            if field == "patient_id":
                # The exact live-session check intentionally runs before the
                # challenge binding comparison. A wrong patient/session tuple
                # may therefore be rejected even earlier as inactive, which is
                # at least as fail-closed as the later binding mismatch.
                expected_codes.add("DEVICE_ROTATION_SESSION_INACTIVE")
            assert exc_info.value.code in expected_codes
        finally:
            await _cleanup(
                real_redis,
                [patient, other_patient],
                [session, other_session],
                nonces,
            )


@pytest.mark.asyncio
async def test_concurrent_rotation_challenge_consumption_has_exactly_one_winner(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    device = str(uuid.uuid4())
    nonces: list[str] = []
    await _create_live_session(real_redis, patient, "subject", session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            challenge = await issue_device_rotation_challenge(
                patient_id=patient,
                session_id=session,
                device_id=device,
                current_key_version=1,
                new_public_key_fingerprint="d" * 64,
            )
            nonces.append(challenge.nonce)

            async def contender():
                try:
                    return await consume_device_rotation_challenge(
                        challenge_nonce=challenge.nonce,
                        patient_id=patient,
                        session_id=session,
                        device_id=device,
                        current_key_version=1,
                        new_public_key_fingerprint="d" * 64,
                    )
                except DeviceRotationChallengeError as exc:
                    return exc.code

            results = await asyncio.gather(*[contender() for _ in range(12)])
            winners = [r for r in results if isinstance(r, DeviceRotationChallenge)]
            assert len(winners) == 1
            assert all(
                isinstance(r, DeviceRotationChallenge)
                or r == "DEVICE_ROTATION_CHALLENGE_INVALID"
                for r in results
            )
        finally:
            await _cleanup(real_redis, [patient], [session], nonces)


@pytest.mark.asyncio
async def test_revoked_session_cannot_consume_previously_issued_rotation_challenge(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    device = str(uuid.uuid4())
    nonces: list[str] = []
    await _create_live_session(real_redis, patient, "subject", session)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            challenge = await issue_device_rotation_challenge(
                patient_id=patient,
                session_id=session,
                device_id=device,
                current_key_version=1,
                new_public_key_fingerprint="e" * 64,
            )
            nonces.append(challenge.nonce)
            assert await revoke_patient_session(patient_id=patient, session_id=session)
            with pytest.raises(DeviceRotationChallengeError) as exc_info:
                await consume_device_rotation_challenge(
                    challenge_nonce=challenge.nonce,
                    patient_id=patient,
                    session_id=session,
                    device_id=device,
                    current_key_version=1,
                    new_public_key_fingerprint="e" * 64,
                )
            assert exc_info.value.code == "DEVICE_ROTATION_SESSION_INACTIVE"
        finally:
            await _cleanup(real_redis, [patient], [session], nonces)


@pytest.mark.asyncio
async def test_rotation_challenge_expiry_fails_closed(real_redis, monkeypatch):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    device = str(uuid.uuid4())
    nonces: list[str] = []
    await _create_live_session(real_redis, patient, "subject", session)
    monkeypatch.setattr(rotation_authority, "DEVICE_ROTATION_CHALLENGE_TTL_SECONDS", 1)
    with _patch_real_redis(real_redis)[0], _patch_real_redis(real_redis)[1]:
        try:
            challenge = await issue_device_rotation_challenge(
                patient_id=patient,
                session_id=session,
                device_id=device,
                current_key_version=1,
                new_public_key_fingerprint="1" * 64,
            )
            nonces.append(challenge.nonce)
            await asyncio.sleep(1.2)
            with pytest.raises(DeviceRotationChallengeError) as exc_info:
                await consume_device_rotation_challenge(
                    challenge_nonce=challenge.nonce,
                    patient_id=patient,
                    session_id=session,
                    device_id=device,
                    current_key_version=1,
                    new_public_key_fingerprint="1" * 64,
                )
            assert exc_info.value.code in {
                "DEVICE_ROTATION_CHALLENGE_INVALID",
                "DEVICE_ROTATION_CHALLENGE_EXPIRED",
            }
        finally:
            await _cleanup(real_redis, [patient], [session], nonces)


class _UnavailableRedis:
    async def set(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")


@pytest.mark.asyncio
async def test_rotation_redis_unavailable_fails_closed_after_session_validation(real_redis):
    patient = str(uuid.uuid4())
    session = f"session-{uuid.uuid4().hex}"
    await _create_live_session(real_redis, patient, "subject", session)
    with (
        patch(
            "app.services.patient_device_rotation.get_redis_client",
            return_value=_UnavailableRedis(),
        ),
        patch(
            "app.services.patient_session_authority.get_redis_client",
            return_value=real_redis,
        ),
    ):
        try:
            with pytest.raises(DeviceRotationAuthorityUnavailable):
                await issue_device_rotation_challenge(
                    patient_id=patient,
                    session_id=session,
                    device_id=str(uuid.uuid4()),
                    current_key_version=1,
                    new_public_key_fingerprint="2" * 64,
                )
        finally:
            await _cleanup(real_redis, [patient], [session], [])
