"""Real Redis qualification for exact-session device-enrollment authority."""

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

from app.services.patient_auth_service import (
    _CLAIM_PREFIX,
    _token_key,
    claim_device_enrollment_token,
    finalize_device_enrollment_token,
    issue_device_enrollment_token,
)
from app.services.patient_session_authority import (
    PatientSessionAuthorityUnavailable,
    create_patient_session,
    get_or_create_patient_session_epoch,
    revoke_all_patient_sessions,
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
    return epoch


async def _cleanup(redis, patient_ids: list[str], session_ids: list[str], tokens: list[str]):
    keys = [f"nexa:patient_session_epoch:{patient_id}" for patient_id in patient_ids]
    keys.extend(_session_key(session_id) for session_id in session_ids)
    for token in tokens:
        token_key = _token_key(token)
        keys.extend((token_key, _CLAIM_PREFIX + token_key))
    if keys:
        await redis.delete(*keys)


@pytest.mark.asyncio
async def test_exact_session_grant_binding_and_cross_patient_rejection(real_redis):
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    session_a = f"session-{uuid.uuid4().hex}"
    session_b = f"session-{uuid.uuid4().hex}"
    session_patient_b = f"session-{uuid.uuid4().hex}"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient_a, "subject-a", session_a)
    await _create_live_session(real_redis, patient_a, "subject-a", session_b)
    await _create_live_session(real_redis, patient_b, "subject-b", session_patient_b)
    with (
        patch("app.services.patient_auth_service.get_redis_client", return_value=real_redis),
        patch(
            "app.services.patient_session_authority.get_redis_client", return_value=real_redis
        ),
    ):
        try:
            grant = await issue_device_enrollment_token(patient_a, session_a)
            tokens.append(grant)
            assert await claim_device_enrollment_token(grant, patient_a, session_b) is None
            assert (
                await claim_device_enrollment_token(grant, patient_b, session_patient_b)
                is None
            )
            claim = await claim_device_enrollment_token(grant, patient_a, session_a)
            assert claim is not None
            assert (
                await finalize_device_enrollment_token(
                    grant,
                    claim,
                    patient_id=patient_a,
                    auth_session_id=session_a,
                )
                is True
            )
            assert await claim_device_enrollment_token(grant, patient_a, session_a) is None
        finally:
            await _cleanup(
                real_redis,
                [patient_a, patient_b],
                [session_a, session_b, session_patient_b],
                tokens,
            )


@pytest.mark.asyncio
async def test_revoked_session_and_logout_all_invalidate_enrollment_authority(real_redis):
    patient = str(uuid.uuid4())
    session_a = f"session-{uuid.uuid4().hex}"
    session_b = f"session-{uuid.uuid4().hex}"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, "subject-a", session_a)
    await _create_live_session(real_redis, patient, "subject-a", session_b)
    with (
        patch("app.services.patient_auth_service.get_redis_client", return_value=real_redis),
        patch(
            "app.services.patient_session_authority.get_redis_client", return_value=real_redis
        ),
    ):
        try:
            grant_a = await issue_device_enrollment_token(patient, session_a)
            tokens.append(grant_a)
            assert await revoke_patient_session(patient_id=patient, session_id=session_a)
            assert await claim_device_enrollment_token(grant_a, patient, session_a) is None

            grant_b = await issue_device_enrollment_token(patient, session_b)
            tokens.append(grant_b)
            await revoke_all_patient_sessions(patient)
            assert await claim_device_enrollment_token(grant_b, patient, session_b) is None
        finally:
            await _cleanup(real_redis, [patient], [session_a, session_b], tokens)


@pytest.mark.asyncio
async def test_concurrent_claims_have_exactly_one_winner_and_grant_is_one_time(real_redis):
    patient = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, "subject-a", session_id)
    with (
        patch("app.services.patient_auth_service.get_redis_client", return_value=real_redis),
        patch(
            "app.services.patient_session_authority.get_redis_client", return_value=real_redis
        ),
    ):
        try:
            grant = await issue_device_enrollment_token(patient, session_id)
            tokens.append(grant)
            results = await asyncio.gather(
                *[
                    claim_device_enrollment_token(grant, patient, session_id)
                    for _ in range(12)
                ]
            )
            winners = [result for result in results if result is not None]
            assert len(winners) == 1
            assert (
                await finalize_device_enrollment_token(
                    grant,
                    winners[0],
                    patient_id=patient,
                    auth_session_id=session_id,
                )
                is True
            )
            assert await claim_device_enrollment_token(grant, patient, session_id) is None
        finally:
            await _cleanup(real_redis, [patient], [session_id], tokens)


@pytest.mark.asyncio
async def test_logout_all_between_claim_and_finalize_burns_reserved_grant(real_redis):
    patient = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, "subject-a", session_id)
    with (
        patch("app.services.patient_auth_service.get_redis_client", return_value=real_redis),
        patch(
            "app.services.patient_session_authority.get_redis_client", return_value=real_redis
        ),
    ):
        try:
            grant = await issue_device_enrollment_token(patient, session_id)
            tokens.append(grant)
            claim = await claim_device_enrollment_token(grant, patient, session_id)
            assert claim is not None

            await revoke_all_patient_sessions(patient)

            assert (
                await finalize_device_enrollment_token(
                    grant,
                    claim,
                    patient_id=patient,
                    auth_session_id=session_id,
                )
                is False
            )
            assert await claim_device_enrollment_token(grant, patient, session_id) is None
        finally:
            await _cleanup(real_redis, [patient], [session_id], tokens)


class _UnavailableRedis:
    async def get(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def set(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def setex(self, *_args, **_kwargs):
        raise RuntimeError("redis unavailable")


@pytest.mark.asyncio
async def test_enrollment_authority_redis_unavailable_fails_closed(real_redis):
    patient = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    await _create_live_session(real_redis, patient, "subject-a", session_id)
    unavailable = _UnavailableRedis()
    with (
        patch("app.services.patient_auth_service.get_redis_client", return_value=unavailable),
        patch(
            "app.services.patient_session_authority.get_redis_client", return_value=real_redis
        ),
    ):
        try:
            with pytest.raises(PatientSessionAuthorityUnavailable):
                await issue_device_enrollment_token(patient, session_id)
        finally:
            await _cleanup(real_redis, [patient], [session_id], [])


@pytest.mark.asyncio
async def test_revoked_live_session_has_no_jwt_only_enrollment_fallback(real_redis):
    patient = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, "subject-a", session_id)
    with (
        patch("app.services.patient_auth_service.get_redis_client", return_value=real_redis),
        patch(
            "app.services.patient_session_authority.get_redis_client", return_value=real_redis
        ),
    ):
        try:
            grant = await issue_device_enrollment_token(patient, session_id)
            tokens.append(grant)
            await real_redis.delete(_session_key(session_id))
            assert await claim_device_enrollment_token(grant, patient, session_id) is None
        finally:
            await _cleanup(real_redis, [patient], [session_id], tokens)
